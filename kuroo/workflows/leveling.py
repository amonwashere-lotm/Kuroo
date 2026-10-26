"""Premium gates and durable configuration for level rewards and live boards."""
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

import storage.guilds
from kuroo.workflows import community
from kuroo.workflows.premium_state import TIERS, utc

FREE_REWARDS = 10
PERIODS = ('daily', 'weekly', 'monthly')


async def has_premium(guild_id):
    record = await storage.guilds.get(guild_id=guild_id)
    if not record or record.get('subscription') not in TIERS:
        return False
    expiry = record.get('subscription_end')
    return expiry is None or utc(expiry) > community.now()


async def require_period(guild_id, period):
    if period not in PERIODS:
        raise community.CommunityError('Choose daily, weekly or monthly.')
    if period != 'daily' and not await has_premium(guild_id):
        raise community.CommunityError('Weekly and monthly live leaderboards require server premium. Daily is free.')


async def rewards(guild_id):
    collection = await community.get_collection('level_rewards')
    record = await collection.find_one({'_id': guild_id}) or {}
    return sorted(record.get('rewards', []), key=lambda entry: (entry['level'], entry['role_id']))


async def active_rewards(guild_id):
    entries = await rewards(guild_id)
    return entries if len(entries) <= FREE_REWARDS or await has_premium(guild_id) else entries[:FREE_REWARDS]


async def set_reward(guild_id, role_id, level):
    community.whole(level, 1, 1_000_000)
    premium = await has_premium(guild_id)
    collection = await community.get_collection('level_rewards')
    try:
        await collection.update_one({'_id': guild_id}, {'$setOnInsert': {'rewards': []}}, upsert=True)
    except DuplicateKeyError:
        pass
    query = {'_id': guild_id}
    if not premium:
        query['$or'] = [{'rewards.role_id': role_id}, {'$expr': {'$lt': [{'$size': '$rewards'}, FREE_REWARDS]}}]
    record = await collection.find_one_and_update(query, [{'$set': {'rewards': {'$concatArrays': [
        {'$filter': {'input': '$rewards', 'as': 'reward', 'cond': {'$ne': ['$$reward.role_id', role_id]}}},
        [{'role_id': role_id, 'level': level}],
    ]}}}], return_document=ReturnDocument.AFTER)
    if record is None:
        raise community.CommunityError('The first 10 level reward roles are free. More than 10 requires server premium.')
    return record


async def remove_reward(guild_id, role_id):
    collection = await community.get_collection('level_rewards')
    await collection.update_one({'_id': guild_id}, {'$pull': {'rewards': {'role_id': role_id}}})


async def board(guild_id, period):
    collection = await community.get_collection('live_leaderboards')
    return await collection.find_one({'_id': f'{guild_id}:{period}'})


async def boards(guild_id=None):
    collection = await community.get_collection('live_leaderboards')
    query = {} if guild_id is None else {'guild_id': guild_id}
    async for entry in collection.find(query):
        yield entry


async def save_board(guild_id, period, channel_id, message_id):
    await require_period(guild_id, period)
    collection = await community.get_collection('live_leaderboards')
    await collection.replace_one({'_id': f'{guild_id}:{period}'}, {
        '_id': f'{guild_id}:{period}', 'guild_id': guild_id, 'period': period,
        'channel_id': channel_id, 'message_id': message_id,
    }, upsert=True)


async def remove_board(guild_id, period):
    collection = await community.get_collection('live_leaderboards')
    return await collection.find_one_and_delete({'_id': f'{guild_id}:{period}'})
