"""Persistent XP and a global coin economy. Every mutation is one atomic document update.

Community command ideas were researched in Programmify/Logiq (MIT); see
third_party/Logiq-LICENSE and README credits. This storage implementation is native to Kuroo.
"""
from datetime import datetime, timedelta, timezone
from math import isqrt
import secrets
from string import Formatter

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

import storage.users
from kuroo.bridge.storage import get_collection

XP_PER_MESSAGE = 15
XP_COOLDOWN = 60
MAX_AMOUNT = 1_000_000_000_000
SHOP = {
    'night-owl': {'name': 'Night owl', 'cost': 500},
    'aurora': {'name': 'Aurora', 'cost': 1500},
    'after-hours': {'name': 'After hours', 'cost': 3000},
}


class CommunityError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc)


def level_for_xp(xp):
    return isqrt(max(0, int(xp)) // 100)


def level_floor(level):
    return 100 * level * level


async def prepare():
    levels = await get_collection('member_levels')
    await levels.create_index([('guild_id', 1), ('user_id', 1)], unique=True)
    await levels.create_index([('guild_id', 1), ('xp', -1), ('user_id', 1)])
    users = await get_collection('users')
    await users.create_index('user_id', unique=True, sparse=True, name='users_user_id_unique')
    await users.create_index([('balance', -1), ('user_id', 1)])


async def settings(guild_id):
    collection = await get_collection('community_settings')
    return await collection.find_one({'_id': guild_id}) or {}


async def configure(guild_id, **values):
    collection = await get_collection('community_settings')
    await collection.update_one({'_id': guild_id}, {'$set': values}, upsert=True)


LEVEL_DEFAULTS = {'leveling': True, 'xp_amount': XP_PER_MESSAGE, 'xp_cooldown': XP_COOLDOWN,
                  'level_channel': 0, 'level_message': '{user} reached **level {level}**!',
                  'xp_ignored_channels': [], 'xp_ignored_roles': []}
ECONOMY_DEFAULTS = {'daily_reward': 250, 'daily_cooldown': 86400,
                    'work_min': 80, 'work_max': 160, 'work_cooldown': 3600}


def whole(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise CommunityError(f'Enter a whole number from {minimum:,} to {maximum:,}.')


def validate_level_message(template):
    if not 1 <= len(template) <= 1000:
        raise CommunityError('Announcement messages must contain 1–1000 characters.')
    try:
        for _, field, spec, conversion in Formatter().parse(template):
            if field is not None and (field not in ('user', 'level', 'xp', 'server') or spec or conversion):
                raise ValueError()
    except ValueError:
        raise CommunityError('Use only {user}, {level}, {xp} and {server} without format modifiers.') from None


async def level_settings(guild_id):
    return LEVEL_DEFAULTS | await settings(guild_id)


async def configure_levels(guild_id, **values):
    for key, value in values.items():
        if key == 'xp_amount':
            whole(value, 1, 1000)
        elif key == 'xp_cooldown':
            whole(value, 5, 3600)
        elif key == 'level_message':
            validate_level_message(value)
        else:
            raise CommunityError('Unknown leveling setting.')
    await configure(guild_id, **values)


async def xp_exclusion(guild_id, kind, target_id, *, remove=False):
    if kind not in ('channels', 'roles'):
        raise CommunityError('Choose channels or roles.')
    collection = await get_collection('community_settings')
    await collection.update_one({'_id': guild_id},
        {'$pull' if remove else '$addToSet': {f'xp_ignored_{kind}': target_id}}, upsert=True)


async def edit_xp(guild_id, user_id, amount, *, mode):
    whole(amount, 0, MAX_AMOUNT)
    if mode not in ('add', 'remove', 'set'):
        raise CommunityError('Choose add, remove or set.')
    collection = await get_collection('member_levels')
    key = {'guild_id': guild_id, 'user_id': user_id}
    try:
        await collection.update_one(key, {'$setOnInsert': dict(key, xp=0)}, upsert=True)
    except DuplicateKeyError:
        pass
    value = amount if mode == 'set' else {'$min': [MAX_AMOUNT, {'$max': [0,
        {'$add': [{'$ifNull': ['$xp', 0]}, amount if mode == 'add' else -amount]}]}]}
    record = await collection.find_one_and_update(key, [{'$set': {'xp': value}}], return_document=ReturnDocument.AFTER)
    return {'xp': record['xp'], 'level': level_for_xp(record['xp'])}


async def economy_settings():
    return ECONOMY_DEFAULTS | await settings('economy')


async def configure_rewards(kind, minimum, maximum, cooldown):
    if kind not in ('daily', 'work'):
        raise CommunityError('Choose daily or work.')
    whole(minimum, 1, 1_000_000)
    whole(maximum, minimum, 1_000_000)
    whole(cooldown, 60, 604800)
    if kind == 'daily' and minimum != maximum:
        raise CommunityError('Daily rewards use one fixed amount; use the same minimum and maximum.')
    values = {f'{kind}_cooldown': cooldown}
    values.update({'daily_reward': minimum} if kind == 'daily' else {'work_min': minimum, 'work_max': maximum})
    await configure('economy', **values)


async def reset_earn_cooldown(user_id, kind):
    if kind not in ('daily', 'work', 'all'):
        raise CommunityError('Choose daily, work or all.')
    await wallet(user_id)
    fields = ['daily_at', 'work_at'] if kind == 'all' else [f'{kind}_at']
    collection = await get_collection('users')
    record = await collection.find_one_and_update({'user_id': user_id}, {'$unset': {field: '' for field in fields}},
                                                 return_document=ReturnDocument.AFTER)
    return await _publish(record)


async def award_xp(guild_id, user_id):
    config = await level_settings(guild_id)
    if not config['leveling']:
        return None
    collection = await get_collection('member_levels')
    key = {'guild_id': guild_id, 'user_id': user_id}
    try:
        await collection.update_one(key, {'$setOnInsert': dict(key, xp=0)}, upsert=True)
    except DuplicateKeyError:
        pass
    stamp = now()
    before = await collection.find_one_and_update(
        dict(key, **{'$or': [{'xp_at': None}, {'xp_at': {'$lte': stamp - timedelta(seconds=config['xp_cooldown'])}}]}),
        [{'$set': {'xp': {'$min': [MAX_AMOUNT, {'$add': ['$xp', config['xp_amount']]}]}, 'xp_at': stamp}}],
        return_document=ReturnDocument.BEFORE,
    )
    if before is None:
        return None
    xp = min(MAX_AMOUNT, before['xp'] + config['xp_amount'])
    return {'xp': xp, 'level': level_for_xp(xp), 'leveled_up': level_for_xp(xp) > level_for_xp(before['xp'])}


async def rank(guild_id, user_id):
    collection = await get_collection('member_levels')
    entry = await collection.find_one({'guild_id': guild_id, 'user_id': user_id})
    xp = entry.get('xp', 0) if entry else 0
    position = None
    if xp:
        position = 1 + await collection.count_documents({'guild_id': guild_id, '$or': [
            {'xp': {'$gt': xp}}, {'xp': xp, 'user_id': {'$lt': user_id}},
        ]})
    return {'xp': xp, 'level': level_for_xp(xp), 'rank': position}


async def leaderboard(guild_id, page=1, *, coins=False):
    if not 1 <= page <= 1000:
        raise CommunityError('Choose a page from 1 to 1000.')
    collection = await get_collection('users' if coins else 'member_levels')
    field = 'balance' if coins else 'xp'
    query = {field: {'$gt': 0}}
    if not coins:
        query['guild_id'] = guild_id
    total = await collection.count_documents(query)
    rows = await collection.find(query).sort([(field, -1), ('user_id', 1)]).skip((page - 1) * 10).limit(10).to_list(length=10)
    return rows, max(1, (total + 9) // 10)


async def wallet(user_id):
    record = await storage.users.get(user_id=user_id)
    if record is None:
        try:
            record = await storage.users.insert(user_id=user_id)
        except DuplicateKeyError:
            record = await storage.users.get(user_id=user_id)
    return record


async def _publish(record):
    await storage.users._store._trigger_update_hook(record)
    return record


async def earn(user_id, kind):
    if kind not in ('daily', 'work'):
        raise CommunityError('Unknown reward.')
    await wallet(user_id)
    stamp = now()
    field = 'daily_at' if kind == 'daily' else 'work_at'
    config = await economy_settings()
    cooldown = timedelta(seconds=config[f'{kind}_cooldown'])
    reward = config['daily_reward'] if kind == 'daily' else config['work_min'] + secrets.randbelow(config['work_max'] - config['work_min'] + 1)
    collection = await get_collection('users')
    record = await collection.find_one_and_update(
        {'user_id': user_id, '$or': [{field: None}, {field: {'$lte': stamp - cooldown}}]},
        {'$inc': {'balance': reward}, '$set': {field: stamp, 'updated_at': stamp}},
        return_document=ReturnDocument.AFTER,
    )
    if record is None:
        current = await collection.find_one({'user_id': user_id})
        from kuroo.workflows.premium_state import utc
        retry_at = utc(current[field]) + cooldown
        raise CommunityError(f'You can claim {kind} again <t:{int(retry_at.timestamp())}:R>.')
    return await _publish(record), reward


def valid_amount(amount):
    if isinstance(amount, bool) or not isinstance(amount, int) or not 1 <= amount <= MAX_AMOUNT:
        raise CommunityError(f'Enter a whole coin amount from 1 to {MAX_AMOUNT:,}.')


async def move_bank(user_id, amount, *, withdraw=False):
    valid_amount(amount)
    await wallet(user_id)
    source, target = ('bank', 'balance') if withdraw else ('balance', 'bank')
    collection = await get_collection('users')
    record = await collection.find_one_and_update(
        {'user_id': user_id, source: {'$gte': amount}},
        {'$inc': {source: -amount, target: amount}, '$set': {'updated_at': now()}},
        return_document=ReturnDocument.AFTER,
    )
    if record is None:
        raise CommunityError(f'Not enough coins in your {"bank" if withdraw else "wallet"}.')
    return await _publish(record)


async def buy(user_id, item):
    if item not in SHOP:
        raise CommunityError('Unknown item. Use `/shop` to see the available titles.')
    await wallet(user_id)
    collection = await get_collection('users')
    record = await collection.find_one_and_update(
        {'user_id': user_id, 'balance': {'$gte': SHOP[item]['cost']}, 'owned_titles': {'$ne': item}},
        {'$inc': {'balance': -SHOP[item]['cost']}, '$addToSet': {'owned_titles': item},
         '$set': {'updated_at': now()}}, return_document=ReturnDocument.AFTER,
    )
    if record is None:
        raise CommunityError('You already own this title, or you do not have enough wallet coins.')
    return await _publish(record)


async def equip(user_id, item):
    if item != 'none' and item not in SHOP:
        raise CommunityError('Unknown title. Use `/inventory` to see your titles.')
    await wallet(user_id)
    query = {'user_id': user_id}
    if item != 'none':
        query['owned_titles'] = item
    collection = await get_collection('users')
    record = await collection.find_one_and_update(query, {'$set': {'card_title': item}},
                                                 return_document=ReturnDocument.AFTER)
    if record is None:
        raise CommunityError('Buy this title in `/shop` before equipping it.')
    return await _publish(record)


async def admin_balance(user_id, amount, *, mode):
    """Keep the original root balance commands on the same atomic economy path."""
    if isinstance(amount, bool) or not isinstance(amount, int) or not 0 <= amount < 2**63:
        raise CommunityError('Enter a nonnegative whole coin amount that fits a signed 64-bit integer.')
    await wallet(user_id)
    collection = await get_collection('users')
    if mode == 'add':
        update = {'$inc': {'balance': amount}}
    elif mode == 'remove':
        update = [{'$set': {'balance': {'$max': [0, {'$subtract': ['$balance', amount]}]}}}]
    elif mode == 'set':
        update = {'$set': {'balance': amount}}
    else:
        raise CommunityError('Unknown balance operation.')
    record = await collection.find_one_and_update({'user_id': user_id}, update, return_document=ReturnDocument.AFTER)
    return await _publish(record)
