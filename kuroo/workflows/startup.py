import discord
import asyncio
import datetime

from kuroo.engine.Bot import AutoShardedBot
import storage.afk
from kuroo.console.logging import logger

from kuroo.memory.cache import cache

from kuroo.workflows.actions import change_guild_subscription, change_user_subscription
from kuroo.workflows.afk_delay import afk_delay

import json

import storage
check_guilds_subscription_running = False
async def check_guilds_subscription(bot: AutoShardedBot):
    global check_guilds_subscription_running
    if check_guilds_subscription_running:
        return
    check_guilds_subscription_running = True
    from kuroo.workflows.premium import expire
    try:
        await bot.wait_until_ready()
        while not bot.is_closed():
            try:
                await expire()
            except Exception:
                logger.error("Premium expiration sweep failed; retrying in 30 seconds")
            await asyncio.sleep(30)
    finally:
        check_guilds_subscription_running = False

check_users_subscription_running = False
async def check_users_subscription(bot: AutoShardedBot):
    global check_users_subscription_running
    if check_users_subscription_running:
        return logger.warning("Subscription check is already running")
    check_users_subscription_running = True
    while not bot.is_ready():
        await asyncio.sleep(1)
    # logger.info("Checking Subscriptions")
    while True:
        for user_id,data in cache.users.items():
            if data.get('subscription') == 'free':
                continue
            if not data.get('no_prefix_subscription'):
                continue
                
            if not data.get('no_prefix_end'):
                logger.warning(f"Infinite subscription found for user {user_id}")
                continue
            
            # if subscription has less than 3 days left notify the owner
            if data.get('no_prefix_end',datetime.datetime.now()).astimezone() < datetime.datetime.now().astimezone() + datetime.timedelta(days=3):
                logger.info(f"Subscription ending soon for user {user_id}")
                try:
                    user = bot.get_user(int(user_id))
                    if user:
                        await user.send(f"Your subscription is ending soon, please renew it.")
                    else:
                        logger.error(f"User {user_id} not found")
                except Exception as e:
                    logger.error(f"Error sending message to user {user_id}: {e}")

            if data.get('no_prefix_end',datetime.datetime.now()).astimezone() > datetime.datetime.now().astimezone():
                logger.info(f"Subscription has not ended for user {user_id}")
                continue
            
            logger.info(f"Subscription ended for user {user_id}")

            await change_user_subscription(
                bot=bot,
                user_id=int(user_id),
                subscription=None,
                valid_for_days=None
            )
        
        # 12 hours
        await asyncio.sleep(12*60*60)

restart_afk_functions_running = False
async def resume_afk_functions(bot: AutoShardedBot):
    global restart_afk_functions_running
    if restart_afk_functions_running:
        return logger.warning("AFK functions are already running")
    restart_afk_functions_running = True
    while not bot.is_ready():
        await asyncio.sleep(1)
    all_afk_data = await storage.afk.get_all()
    for afk_data in all_afk_data:
        asyncio.create_task(afk_delay(bot=bot, data=afk_data))