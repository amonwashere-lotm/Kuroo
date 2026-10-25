"""Persistent, atomic claims and idempotent timed guild premium grants."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from functools import wraps
import asyncio

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

import storage.auto_responder
import storage.media_channels
import storage.guilds
import storage.redeem_codes
import storage.welcomer_settings
from kuroo.bridge.storage import get_collection
from kuroo.console.generator import generate_redeem_code
from kuroo.workflows.premium_state import TIERS, utc

PREMIUM_TIER = 'diamond_guild_premium'
_locks = defaultdict(asyncio.Lock)


def serialized(function):
    @wraps(function)
    async def wrapped(guild_id, *args, **kwargs):
        async with _locks[guild_id]:
            return await function(guild_id, *args, **kwargs)
    return wrapped


class PremiumError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc)


async def create_code():
    issued = now()
    return await storage.redeem_codes.insert(
        code=generate_redeem_code(), code_type='subscription',
        code_value=PREMIUM_TIER, valid_for_days=1, reward_kind='daily_guild',
        expires_at=issued + timedelta(hours=12),
    )


async def _guild(guild_id):
    record = await storage.guilds.get(guild_id=guild_id)
    if record:
        return record
    try:
        return await storage.guilds.insert(guild_id=guild_id, subscription='free')
    except DuplicateKeyError:
        return await storage.guilds.get(guild_id=guild_id)


async def _publish(record):
    await storage.guilds._store._trigger_update_hook(record)
    tier = record.get('subscription', 'free')
    end = record.get('subscription_end')
    if end and utc(end) <= now():
        tier = 'free'
    settings = await storage.welcomer_settings.get(guild_id=record['guild_id'])
    if not settings:
        settings = await storage.welcomer_settings.insert(guild_id=record['guild_id'])
    limit = {TIERS[0]: 5, TIERS[1]: 10, TIERS[2]: 15}.get(tier, 3)
    values = {'id': settings['id'], 'autoroles_limit': limit}
    if tier == 'free':
        # Retain the original free-tier cleanup contract when premium ends.
        values.update(autoroles=settings.get('autoroles', [])[:3],
                      greet_channels=settings.get('greet_channels', [])[:5], autonick=False)
        await storage.media_channels.delete_limited(limit=1, guild_id=record['guild_id'])
        await storage.auto_responder.delete_limited(limit=5, guild_id=record['guild_id'])
    await storage.welcomer_settings.update(**values)
    return record


@serialized
async def grant(guild_id, *, until, issued_at, tier=PREMIUM_TIER):
    """Grant until a fixed UTC time; retries never add extra time.

    Existing lifetime/longer premium is preserved. A revocation invalidates all
    rewards issued before it, including unfinished claims retried after a crash.
    """
    if tier not in TIERS:
        raise PremiumError('Unknown premium tier.')
    until, issued_at = utc(until), utc(issued_at)
    if until <= now():
        raise PremiumError('This premium reward has expired.')
    record = await _guild(guild_id)
    collection = await get_collection('guilds')
    lifetime = {'$and': [{'$in': ['$subscription', list(TIERS)]},
                         {'$eq': [{'$ifNull': ['$subscription_end', None]}, None]}]}
    # Keep the strongest tier for overlapping rewards.
    stronger = {'$gt': [{'$indexOfArray': [list(TIERS), '$subscription']}, TIERS.index(tier)]}
    active = {'$or': [lifetime, {'$gt': ['$subscription_end', now()]}]}
    updated = await collection.find_one_and_update(
        {'id': record['id'], '$or': [{'premium_revoked_at': None},
                                     {'premium_revoked_at': {'$lt': issued_at}}]},
        [{'$set': {
            'subscription': {'$cond': [{'$or': [lifetime, {'$and': [active, {'$or': [stronger, {'$gte': ['$subscription_end', until]}]}]}]}, '$subscription', tier]},
            'subscription_end': {'$cond': [lifetime, None, {'$max': ['$subscription_end', until]}]},
            'updated_at': now(),
        }}], return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise PremiumError('This reward predates an admin revocation. Use a new code or vote.')
    return await _publish(updated)


@serialized
async def revoke(guild_id):
    record = await _guild(guild_id)
    collection = await get_collection('guilds')
    record = await collection.find_one_and_update(
        {'id': record['id']},
        {'$set': {'subscription': 'free', 'subscription_end': None,
                  'premium_revoked_at': now(), 'updated_at': now()}},
        return_document=ReturnDocument.AFTER,
    )
    return await _publish(record)


async def _claim(collection_name, query, user_id, guild_id):
    collection = await get_collection(collection_name)
    claimed_at = now()
    record = await collection.find_one_and_update(
        dict(query, claimed={'$ne': True}, **{'$or': [
            {'expires_at': {'$gt': claimed_at}}, {'expires_at': None}]}),
        {'$set': {'claimed': True, 'claimed_by': user_id,
                  'claimed_guild': guild_id, 'claimed_at': claimed_at}},
        return_document=ReturnDocument.AFTER,
    )
    if record:
        return record
    # A failed grant can be retried only by the same user in the same guild.
    record = await collection.find_one(query)
    if (record and record.get('claimed') and not record.get('applied')
            and record.get('claimed_by') == user_id and record.get('claimed_guild') == guild_id):
        return record
    raise PremiumError('Reward is invalid, expired, or already claimed.')


async def redeem(code, user_id, guild_id, *, bot=None, can_manage_guild=False):
    code = code.strip().upper()
    collection = await get_collection('redeem_codes')
    existing = await collection.find_one({'code': code, 'code_type': 'subscription'})
    if not existing:
        raise PremiumError('Invalid redeem code.')
    if existing.get('reward_kind') == 'daily_guild' and not can_manage_guild:
        raise PremiumError('Manage Server permission is required to redeem server premium.')
    record = await _claim('redeem_codes', {'code': code, 'code_type': 'subscription'}, user_id, guild_id)
    tier = record.get('code_value')
    if record.get('reward_kind') == 'daily_guild':
        result = await grant(guild_id, until=utc(record['claimed_at']) + timedelta(days=1),
                             issued_at=record['claimed_at'])
    else:
        # Preserve the existing tiers, durations and lifetime legacy code contract.
        from kuroo.workflows.actions import change_guild_subscription, change_user_subscription
        if bot is None:
            raise PremiumError('Legacy codes must be redeemed through the bot.')
        if tier in TIERS:
            await change_guild_subscription(bot, guild_id, tier, record.get('valid_for_days'))
            result = await storage.guilds.get(guild_id=guild_id)
        elif tier == 'user_no_prefix':
            await change_user_subscription(bot, user_id, tier, record.get('valid_for_days'))
            result = None
        else:
            raise PremiumError('Unsupported redeem code type.')
    await collection.update_one({'_id': record['_id']}, {'$set': {'applied': True}})
    return result


async def record_vote(vote_id, user_id, created_at):
    created_at = utc(created_at)
    if created_at > now() + timedelta(minutes=5):
        raise PremiumError('Invalid vote timestamp.')
    collection = await get_collection('premium_votes')
    document = {'_id': vote_id, 'user_id': user_id, 'created_at': created_at,
                'expires_at': created_at + timedelta(hours=12), 'claimed': False}
    try:
        await collection.insert_one(document)
    except DuplicateKeyError:
        pass  # Delivery retries cannot refresh or duplicate a reward.


async def claim_vote(user_id, guild_id):
    collection = await get_collection('premium_votes')
    record = await collection.find_one({'user_id': user_id, 'expires_at': {'$gt': now()}},
                                       sort=[('created_at', -1)])
    if not record:
        raise PremiumError('No active vote found. Vote on Top.gg, then try again.')
    record = await _claim('premium_votes', {'_id': record['_id']}, user_id, guild_id)
    result = await grant(guild_id, until=utc(record['created_at']) + timedelta(hours=12),
                         issued_at=record['created_at'])
    await collection.update_one({'_id': record['_id']}, {'$set': {'applied': True}})
    return result


async def expire():
    """Persist expirations without overwriting a concurrent renewal."""
    collection = await get_collection('guilds')
    query = {'subscription': {'$in': list(TIERS)},
             'subscription_end': {'$lte': now(), '$ne': None}}
    while True:
        candidate = await collection.find_one(query)
        if not candidate:
            return
        async with _locks[candidate['guild_id']]:
            record = await collection.find_one_and_update(
                dict(query, id=candidate['id']),
                {'$set': {'subscription': 'free', 'subscription_end': None, 'updated_at': now()}},
                return_document=ReturnDocument.AFTER,
            )
            if record:
                await _publish(record)
