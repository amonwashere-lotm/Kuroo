import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import discord

from kuroo.engine.Bot import AutoShardedBot
from kuroo.src.events.ready import ready


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_cogs_and_hybrid_commands_load_without_gateway(self):
        bot = AutoShardedBot()
        await bot._async_setup_hook()
        try:
            self.assertEqual(bot.status, discord.Status.dnd)
            self.assertEqual(bot.activity.name, 'Kuroo Devlopment')
            await bot.load_extension('kuroo.src')
            self.assertEqual(len(bot.cogs), 41)
            self.assertGreater(len(list(bot.walk_commands())), 200)
            self.assertGreater(len(bot.tree.get_commands()), 60)
            self.assertIn('help', bot.all_commands)
            self.assertIsNotNone(bot.tree.get_command('help'))
            for name in ('premiumcode', 'premiumgrant', 'premiumrevoke', 'votepremium', 'redeem', 'noprefix', 'rank', 'lb', 'daily', 'work', 'balance', 'deposit', 'withdraw', 'shop', 'buy', 'inventory', 'equip', 'leveling', 'levelchannel', 'welcomecards', 'leave', 'welcomepreview', 'leavepreview'):
                self.assertIn(name, bot.all_commands)
                self.assertIsNotNone(bot.tree.get_command(name))
        finally:
            for name in list(bot.cogs):
                await bot.remove_cog(name)
            await bot.http.close()

    async def test_presence_rotation_keeps_dnd_and_both_command_hints(self):
        sent = []
        async def change_presence(**kwargs):
            sent.append(kwargs)
        bot = SimpleNamespace(
            wait_until_ready=AsyncMock(), is_closed=lambda: len(sent) >= 8,
            change_presence=change_presence, BotConfig=SimpleNamespace(PREFIX='!'),
            guilds=[SimpleNamespace(member_count=10)], commands=[1, 2],
            urls=SimpleNamespace(WEBSITE='https://example.com'),
        )
        cog = ready(bot)
        with patch('kuroo.src.events.ready.asyncio.sleep', new=AsyncMock()):
            await cog.activity()
        self.assertTrue(all(item['status'] == discord.Status.dnd for item in sent))
        self.assertEqual(sent[0]['activity'].name, 'Kuroo Devlopment')
        self.assertEqual(sent[7]['activity'].name, 'Kuroo Devlopment')
        names = [item['activity'].name for item in sent]
        self.assertIn('!help | /help', names)

    async def test_reconnect_does_not_duplicate_presence_task(self):
        bot = SimpleNamespace(
            user=SimpleNamespace(id=42), BotConfig=SimpleNamespace(DASHBOARD_ENABLED=False, DISCORD_CLIENT_ID=""),
            urls=SimpleNamespace(),
            tree=SimpleNamespace(sync=AsyncMock()), fetch_user=AsyncMock(),
        )
        cog = ready(bot)
        waiting = asyncio.Event()
        cog.activity = waiting.wait
        cog.on_ready_startups = AsyncMock()
        with patch('kuroo.src.events.ready.logger'), patch('kuroo.src.events.ready.users_config.developer', ()):
            await cog.on_ready()
            first_task = cog._activity_task
            await cog.on_ready()
            self.assertIs(cog._activity_task, first_task)
            cog.cog_unload()
            with self.assertRaises(asyncio.CancelledError):
                await first_task


if __name__ == '__main__':
    unittest.main()
