"""Persistent XP and a global coin economy. Every mutation is one atomic document update.

Community command ideas were researched in Programmify/Logiq (MIT); see
third_party/Logiq-LICENSE and README credits. This storage implementation is native to Kuroo.
"""
from datetime import datetime, timedelta, timezone
from math import isqrt
import secrets

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


async def award_xp(guild_id, user_id):
    collection = await get_collection('member_levels')
    key = {'guild_id': guild_id, 'user_id': user_id}
    try:
        await collection.update_one(key, {'$setOnInsert': dict(key, xp=0)}, upsert=True)
    except DuplicateKeyError:
        pass
    stamp = now()
    before = await collection.find_one_and_update(
        dict(key, **{'$or': [{'xp_at': None}, {'xp_at': {'$lte': stamp - timedelta(seconds=XP_COOLDOWN)}}]}),
        {'$inc': {'xp': XP_PER_MESSAGE}, '$set': {'xp_at': stamp}},
        return_document=ReturnDocument.BEFORE,
    )
    if before is None:
        return None
    xp = before['xp'] + XP_PER_MESSAGE
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
    cooldown = timedelta(hours=24 if kind == 'daily' else 1)
    reward = 250 if kind == 'daily' else 80 + secrets.randbelow(81)
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
