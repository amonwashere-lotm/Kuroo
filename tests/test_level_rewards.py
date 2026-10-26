"""Level reward entitlements, period accounting and Discord post lifecycle."""
import asyncio
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import discord
from motor.motor_asyncio import AsyncIOMotorClient
from PIL import Image

from kuroo.src.commands.community import Community
from kuroo.workflows import cards, community, leveling
from kuroo.workflows.live_leaderboards import LiveLeaderboards

T0 = datetime(2030, 1, 6, 23, 59, tzinfo=timezone.utc)  # Sunday, January 6.


@unittest.skipUnless(os.getenv('KUROO_TEST_MONGO_URI'), 'Set KUROO_TEST_MONGO_URI for disposable Mongo tests')
class LevelingMongoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = AsyncIOMotorClient(os.environ['KUROO_TEST_MONGO_URI'], serverSelectionTimeoutMS=3000)
        await self.client.admin.command('ping')
        self.db = self.client['kuroo_test_' + uuid4().hex]
        async def collection(name):
            return self.db[name]
        self.patches = [patch('storage.engine.get_collection', collection), patch.object(community, 'get_collection', collection),
                        patch.object(community, 'now', return_value=T0)]
        for item in self.patches:
            item.start()
        await community.prepare()

    async def asyncTearDown(self):
        await self.client.drop_database(self.db.name)
        self.client.close()
        for item in reversed(self.patches):
            item.stop()

    async def premium(self, guild_id=1, end=None):
        await self.db.guilds.replace_one({'guild_id': guild_id}, {'guild_id': guild_id,
            'subscription': 'diamond_guild_premium', 'subscription_end': end}, upsert=True)

    async def test_only_ten_free_slots_even_under_simultaneous_configuration(self):
        results = await asyncio.gather(*[leveling.set_reward(1, 100 + i, i + 1) for i in range(20)], return_exceptions=True)
        self.assertEqual(sum(not isinstance(item, Exception) for item in results), 10)
        self.assertTrue(all(isinstance(item, community.CommunityError) for item in results if isinstance(item, Exception)))
        entries = await leveling.rewards(1)
        await leveling.set_reward(1, entries[0]['role_id'], 50)
        self.assertEqual(len(await leveling.rewards(1)), 10)
        await leveling.remove_reward(1, entries[0]['role_id'])
        await leveling.set_reward(1, 999, 1)
        self.assertEqual(len(await leveling.rewards(1)), 10)
        self.assertEqual(await leveling.rewards(2), [])

    async def test_premium_extra_rewards_pause_at_expiry_without_deletion(self):
        await self.premium(end=T0 + timedelta(minutes=1))
        for i in range(12):
            await leveling.set_reward(1, 100+i, 12-i)
        self.assertEqual(len(await leveling.active_rewards(1)), 12)
        with patch.object(community, 'now', return_value=T0 + timedelta(minutes=1)):
            self.assertFalse(await leveling.has_premium(1))
            self.assertEqual([r['level'] for r in await leveling.active_rewards(1)], list(range(1, 11)))
            self.assertEqual(len(await leveling.rewards(1)), 12)
            with self.assertRaises(community.CommunityError):
                await leveling.set_reward(1, 999, 1)
            await leveling.remove_reward(1, 100)
        await self.premium()  # Lifetime premium restores retained slots.
        self.assertEqual(len(await leveling.active_rewards(1)), 11)

    async def test_all_valid_premium_tiers_work_and_unknown_or_expired_tiers_do_not(self):
        for tier in ('silver_guild_preminum', 'golden_guild_premium', 'diamond_guild_premium', 'free', 'unknown'):
            await self.db.guilds.replace_one({'guild_id': 1}, {'guild_id': 1, 'subscription': tier}, upsert=True)
            self.assertEqual(await leveling.has_premium(1), tier not in ('free', 'unknown'))
        await self.premium(end=T0 - timedelta(seconds=1))
        self.assertFalse(await leveling.has_premium(1))

    async def test_period_counters_share_the_atomic_message_cooldown(self):
        results = await asyncio.gather(*[community.award_xp(1, 100) for _ in range(10)])
        self.assertEqual(sum(item is not None for item in results), 1)
        for period in leveling.PERIODS:
            rows = await community.period_leaderboard(1, period)
            self.assertEqual([(row['user_id'], row['xp']) for row in rows], [(100, 15)])
        await community.edit_xp(1, 100, 9999, mode='set')
        self.assertEqual((await community.period_leaderboard(1, 'daily'))[0]['xp'], 15)
        self.assertEqual((await community.rank(1, 100))['xp'], 9999)
        self.assertEqual(await community.period_leaderboard(2, 'daily'), [])

    async def test_calendar_rollovers_reset_only_the_appropriate_scores(self):
        await community.award_xp(1, 100)
        with patch.object(community, 'now', return_value=T0 + timedelta(minutes=1)):
            self.assertEqual(await community.period_leaderboard(1, 'daily'), [])
            self.assertEqual(await community.period_leaderboard(1, 'weekly'), [])
            self.assertEqual((await community.period_leaderboard(1, 'monthly'))[0]['xp'], 15)
            await community.award_xp(1, 100)
            self.assertEqual((await community.period_leaderboard(1, 'monthly'))[0]['xp'], 30)
            self.assertEqual((await community.period_leaderboard(1, 'weekly'))[0]['xp'], 15)
        with patch.object(community, 'now', return_value=datetime(2030, 2, 1, tzinfo=timezone.utc)):
            self.assertEqual(await community.period_leaderboard(1, 'monthly'), [])
            await community.award_xp(1, 100)
            self.assertEqual((await community.period_leaderboard(1, 'monthly'))[0]['xp'], 15)
            self.assertEqual((await community.rank(1, 100))['xp'], 45)

    async def test_period_ties_top_ten_and_lifetime_legacy_records(self):
        await self.db.member_levels.insert_one({'guild_id': 1, 'user_id': 999, 'xp': 5000})
        self.assertEqual(await community.period_leaderboard(1, 'daily'), [])
        await asyncio.gather(*[community.award_xp(1, user_id) for user_id in range(11)])
        self.assertEqual([r['user_id'] for r in await community.period_leaderboard(1, 'daily')], list(range(10)))
        await community.award_xp(1, 999)
        self.assertEqual((await community.rank(1, 999))['xp'], 5015)

    async def test_period_scores_count_only_xp_actually_earned_at_cap(self):
        await community.edit_xp(1, 100, community.MAX_AMOUNT - 2, mode='set')
        await community.award_xp(1, 100)
        self.assertEqual((await community.period_leaderboard(1, 'daily'))[0]['xp'], 2)
        with patch.object(community, 'now', return_value=T0 + timedelta(minutes=1)):
            await community.award_xp(1, 100)
            self.assertEqual((await community.period_leaderboard(1, 'monthly'))[0]['xp'], 2)

    async def test_board_persistence_free_daily_premium_gate_and_removal_after_expiry(self):
        await leveling.save_board(1, 'daily', 10, 20)
        self.assertEqual((await leveling.board(1, 'daily'))['channel_id'], 10)
        for period in ('weekly', 'monthly'):
            with self.assertRaises(community.CommunityError):
                await leveling.save_board(1, period, 10, 20)
        await self.premium(end=T0 + timedelta(minutes=1))
        await leveling.save_board(1, 'weekly', 10, 21)
        self.assertEqual(len([r async for r in leveling.boards(1)]), 2)
        self.assertEqual([r async for r in leveling.boards(2)], [])
        with patch.object(community, 'now', return_value=T0 + timedelta(minutes=1)):
            with self.assertRaises(community.CommunityError):
                await leveling.require_period(1, 'weekly')
            await leveling.remove_board(1, 'weekly')  # Always allow cleanup, even without premium.
        self.assertIsNone(await leveling.board(1, 'weekly'))


class RewardDiscordTests(unittest.IsolatedAsyncioTestCase):
    def member(self):
        role = SimpleNamespace(id=10, is_assignable=lambda: True)
        guild = SimpleNamespace(id=1, me=SimpleNamespace(guild_permissions=SimpleNamespace(manage_roles=True)),
                                get_role=lambda role_id: role if role_id == 10 else None)
        return SimpleNamespace(guild=guild, roles=[], add_roles=AsyncMock()), role

    async def test_earned_roles_only_assign_once_and_never_replace_other_roles(self):
        member, role = self.member()
        with patch.object(leveling, 'active_rewards', new=AsyncMock(return_value=[{'level': 2, 'role_id': 10}])):
            cog = Community(None)
            self.assertEqual(await cog._award_roles(member, 1), [])
            self.assertEqual(await cog._award_roles(member, 2), [10])
            member.add_roles.assert_awaited_once_with(role, reason='Kuroo level 2 reward')
            member.roles.append(role)
            self.assertEqual(await cog._award_roles(member, 5), [])
            self.assertEqual(member.add_roles.await_count, 1)

    async def test_unassignable_deleted_or_missing_manage_roles_are_skipped(self):
        member, role = self.member()
        role.is_assignable = lambda: False
        with patch.object(leveling, 'active_rewards', new=AsyncMock(return_value=[{'level': 1, 'role_id': 10}, {'level': 1, 'role_id': 99}])):
            self.assertEqual(await Community(None)._award_roles(member, 10), [])
            member.add_roles.assert_not_awaited()
        member.guild.me.guild_permissions.manage_roles = False
        with patch.object(leveling, 'active_rewards', new=AsyncMock()) as rewards:
            self.assertEqual(await Community(None)._award_roles(member, 10), [])
            rewards.assert_not_awaited()

    async def test_failed_role_assignment_is_retryable_by_claim(self):
        member, role = self.member()
        member.add_roles.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'missing permission')
        with patch.object(leveling, 'active_rewards', new=AsyncMock(return_value=[{'level': 1, 'role_id': 10}])):
            self.assertEqual(await Community(None)._award_roles(member, 1), [])
            member.add_roles.side_effect = None
            self.assertEqual(await Community(None)._award_roles(member, 1), [10])

    async def test_configuration_requires_manage_roles_and_actor_and_bot_hierarchy(self):
        class Role:
            guild = SimpleNamespace(id=1)
            managed = False
            position = 2
            def is_default(self): return False
            def is_assignable(self): return self.position < 5
            def __ge__(self, other): return self.position >= other.position
        role = Role()
        actor = SimpleNamespace(id=100, guild_permissions=SimpleNamespace(manage_roles=False), top_role=SimpleNamespace(position=3))
        guild = SimpleNamespace(id=1, owner_id=999, me=SimpleNamespace(guild_permissions=SimpleNamespace(manage_roles=True)))
        ctx = SimpleNamespace(guild=guild, author=actor)
        with patch('kuroo.src.commands.community.checks.check_is_admin_predicate', return_value=False):
            with self.assertRaises(community.CommunityError):
                Community(None)._check_reward_role(ctx, role)
            actor.guild_permissions.manage_roles = True
            Community(None)._check_reward_role(ctx, role)
            role.position = 3
            with self.assertRaises(community.CommunityError):
                Community(None)._check_reward_role(ctx, role)
        with patch('kuroo.src.commands.community.checks.check_is_admin_predicate', return_value=True):
            Community(None)._check_reward_role(ctx, role)
            role.position = 5
            with self.assertRaises(community.CommunityError):
                Community(None)._check_reward_role(ctx, role)

    async def test_automatic_level_up_and_manual_xp_edit_apply_rewards(self):
        cog = Community(SimpleNamespace(get_context=AsyncMock(return_value=SimpleNamespace(valid=False))))
        member = SimpleNamespace(id=100, bot=False, roles=[])
        message = SimpleNamespace(guild=SimpleNamespace(id=1, get_channel=lambda cid: None), author=member,
                                  content='hello', webhook_id=None, channel=SimpleNamespace(id=2))
        with patch('kuroo.src.commands.community.checks.check_blacklist_predicate', return_value=True), patch('kuroo.src.commands.community.checks.check_ignore_predicate', return_value=True), patch.object(community, 'settings', new=AsyncMock(return_value={})), patch.object(community, 'award_xp', new=AsyncMock(return_value={'leveled_up': True, 'level': 2})), patch.object(cog, '_award_roles', new=AsyncMock()) as award:
            await cog.on_message(message)
            award.assert_awaited_once_with(member, 2)
        ctx = SimpleNamespace(guild=SimpleNamespace(id=1), defer=AsyncMock(), send=AsyncMock())
        member.display_name = 'Member'
        with patch.object(community, 'edit_xp', new=AsyncMock(return_value={'xp': 400, 'level': 2})), patch.object(cog, '_award_roles', new=AsyncMock()) as award:
            await Community.xp_edit.callback(cog, ctx, member, 'set', 400)
            award.assert_awaited_once_with(member, 2)

    async def test_deleted_role_rewards_can_be_removed_by_id_without_resolving_role(self):
        ctx = SimpleNamespace(guild=SimpleNamespace(id=1), author=SimpleNamespace(guild_permissions=SimpleNamespace(manage_roles=True)),
                              defer=AsyncMock(), send=AsyncMock())
        with patch('kuroo.src.commands.community.checks.check_is_admin_predicate', return_value=False), patch.object(leveling, 'remove_reward', new=AsyncMock()) as remove:
            for token in ['123', '<@&123>']:
                await Community.levelreward_remove.callback(Community(None), ctx, token)
                remove.assert_awaited_with(1, 123)
            for token in ['@unknown', '-1', '0', str(2**64)]:
                with self.assertRaises(community.CommunityError):
                    await Community.levelreward_remove.callback(Community(None), ctx, token)
            self.assertEqual(remove.await_count, 2)

    async def test_manager_commands_have_checks_and_live_channel_is_required_for_slash(self):
        for command in [Community.levelreward_set, Community.levelreward_remove, Community.liveleaderboard,
                        Community.liveleaderboard_set, Community.liveleaderboard_remove]:
            with patch('kuroo.src.commands.community.checks.check_is_admin_predicate', return_value=False):
                for allowed in (False, True):
                    ctx = SimpleNamespace(guild=object(), author=SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=allowed)))
                    self.assertEqual(await command.checks[0](ctx), allowed)
        params = Community.liveleaderboard_set.app_command.parameters
        self.assertTrue(next(p for p in params if p.name == 'channel').required)
        self.assertTrue(next(p for p in params if p.name == 'period').required)


class LiveBoardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.message = SimpleNamespace(id=20, author=SimpleNamespace(id=42), delete=AsyncMock(), edit=AsyncMock())
        self.permissions = SimpleNamespace(view_channel=True, send_messages=True, attach_files=True, read_message_history=True)
        self.guild = SimpleNamespace(id=1, name='Server', me=object(), get_member=lambda uid: SimpleNamespace(display_name='Member'))
        self.channel = SimpleNamespace(id=10, guild=self.guild, permissions_for=lambda me: self.permissions,
                                       fetch_message=AsyncMock(return_value=self.message), send=AsyncMock(return_value=self.message))
        self.guild.get_channel = lambda channel_id: self.channel if channel_id == 10 else None
        self.bot = SimpleNamespace(user=SimpleNamespace(id=42), get_guild=lambda guild_id: self.guild if guild_id == 1 else None,
                                   get_user=lambda uid: None)
        self.worker = LiveLeaderboards(self.bot)
        self.config = {'_id': '1:daily', 'guild_id': 1, 'period': 'daily', 'channel_id': 10, 'message_id': 20}
        self.rows = [{'user_id': 100, 'xp': 15, 'level': 1}]

    async def test_refresh_edits_existing_post_skips_unchanged_and_resets_at_midnight(self):
        with patch.object(leveling, 'board', new=AsyncMock(return_value=self.config)), patch.object(community, 'period_leaderboard', new=AsyncMock(return_value=self.rows)), patch.object(self.worker, 'write', new=AsyncMock(return_value=self.message)) as write, patch.object(community, 'now', return_value=T0):
            await self.worker.refresh(1, 'daily')
            write.assert_awaited_once_with(self.channel, self.guild, 'daily', self.rows, self.message)
            await self.worker.refresh(1, 'daily')
            self.assertEqual(write.await_count, 1)
            self.assertEqual(self.channel.fetch_message.await_count, 2)
            with patch.object(community, 'now', return_value=T0 + timedelta(minutes=1)):
                await self.worker.refresh(1, 'daily')
            self.assertEqual(write.await_count, 2)
        # A new worker after restart reads the saved message ID and edits it.
        with patch.object(leveling, 'board', new=AsyncMock(return_value=self.config)), patch.object(community, 'period_leaderboard', new=AsyncMock(return_value=self.rows)), patch.object(LiveLeaderboards, 'write', new=AsyncMock(return_value=self.message)) as write:
            await LiveLeaderboards(self.bot).refresh(1, 'daily')
            self.assertEqual(write.await_args.args[-1], self.message)

    async def test_deleted_post_is_recreated_and_new_id_is_saved(self):
        self.channel.fetch_message.side_effect = discord.NotFound(SimpleNamespace(status=404, reason='Not Found'), 'deleted')
        new_message = SimpleNamespace(id=21)
        with patch.object(leveling, 'board', new=AsyncMock(return_value=self.config)), patch.object(community, 'period_leaderboard', new=AsyncMock(return_value=[])), patch.object(self.worker, 'write', new=AsyncMock(return_value=new_message)) as write, patch.object(leveling, 'save_board', new=AsyncMock()) as save:
            await self.worker.refresh(1, 'daily')
            self.assertIsNone(write.await_args.args[-1])
            save.assert_awaited_once_with(1, 'daily', 10, 21)

    async def test_deleted_between_fetch_and_edit_is_recreated(self):
        error = discord.NotFound(SimpleNamespace(status=404, reason='Not Found'), 'deleted')
        with patch.object(leveling, 'board', new=AsyncMock(return_value=self.config)), patch.object(community, 'period_leaderboard', new=AsyncMock(return_value=[])), patch.object(self.worker, 'write', new=AsyncMock(side_effect=[error, SimpleNamespace(id=21)])) as write, patch.object(leveling, 'save_board', new=AsyncMock()) as save:
            await self.worker.refresh(1, 'daily')
            self.assertEqual(write.await_count, 2)
            save.assert_awaited_once_with(1, 'daily', 10, 21)

    async def test_weekly_and_monthly_refresh_pause_when_premium_expires(self):
        for period in ('weekly', 'monthly'):
            with patch.object(leveling, 'board', new=AsyncMock(return_value=self.config | {'period': period})), patch.object(leveling, 'has_premium', new=AsyncMock(return_value=False)), patch.object(self.worker, 'write', new=AsyncMock()) as write:
                with self.assertRaises(community.CommunityError):
                    await self.worker.refresh(1, period)
                write.assert_not_awaited()
        with patch.object(leveling, 'board', new=AsyncMock(return_value=self.config | {'period': 'weekly'})), patch.object(leveling, 'has_premium', new=AsyncMock(return_value=True)), patch.object(community, 'period_leaderboard', new=AsyncMock(return_value=[])), patch.object(self.worker, 'write', new=AsyncMock(return_value=self.message)) as write:
            await self.worker.refresh(1, 'weekly')
            write.assert_awaited_once()

    async def test_required_channel_permissions_and_premium_checked_before_post(self):
        for permission in ('view_channel', 'send_messages', 'attach_files', 'read_message_history'):
            setattr(self.permissions, permission, False)
            with patch.object(self.worker, 'write', new=AsyncMock()) as write:
                with self.assertRaises(community.CommunityError):
                    await self.worker.configure(self.guild, self.channel, 'daily')
                write.assert_not_awaited()
            setattr(self.permissions, permission, True)
        with patch.object(leveling, 'has_premium', new=AsyncMock(return_value=False)), patch.object(self.worker, 'write', new=AsyncMock()) as write:
            with self.assertRaises(community.CommunityError):
                await self.worker.configure(self.guild, self.channel, 'weekly')
            write.assert_not_awaited()

    async def test_configure_cleans_new_post_on_failed_save_and_removes_previous_on_success(self):
        with patch.object(leveling, 'board', new=AsyncMock(return_value=self.config)), patch.object(community, 'period_leaderboard', new=AsyncMock(return_value=[])), patch.object(self.worker, 'write', new=AsyncMock(return_value=self.message)), patch.object(leveling, 'save_board', new=AsyncMock(side_effect=RuntimeError('storage unavailable'))), patch.object(self.worker, 'delete_message', new=AsyncMock()) as delete:
            with self.assertRaises(RuntimeError):
                await self.worker.configure(self.guild, self.channel, 'daily')
            self.message.delete.assert_awaited_once()
            delete.assert_not_awaited()
        with patch.object(leveling, 'board', new=AsyncMock(return_value=self.config)), patch.object(community, 'period_leaderboard', new=AsyncMock(return_value=[])), patch.object(self.worker, 'write', new=AsyncMock(return_value=self.message)), patch.object(leveling, 'save_board', new=AsyncMock()) as save, patch.object(self.worker, 'delete_message', new=AsyncMock()) as delete:
            await self.worker.configure(self.guild, self.channel, 'daily')
            save.assert_awaited_once_with(1, 'daily', 10, 20)
            delete.assert_awaited_once_with(self.config)

    async def test_failed_board_does_not_stop_other_boards(self):
        async def configs():
            for period in ('weekly', 'daily'):
                yield self.config | {'period': period}
        with patch.object(leveling, 'boards', configs), patch.object(self.worker, 'refresh', new=AsyncMock(side_effect=[RuntimeError('offline'), None])) as refresh:
            await self.worker.refresh_all()
            self.assertEqual(refresh.await_count, 2)

    async def test_real_pillow_posts_and_edits_use_v2_without_accents(self):
        self.message.edit.return_value = self.message
        await self.worker.write(self.channel, self.guild, 'daily', self.rows)
        kwargs = self.channel.send.await_args.kwargs
        with discord.http.handle_message_parameters(**{k:v for k,v in kwargs.items() if k != 'allowed_mentions'}) as params:
            payload = params.payload or json.loads(next(part['value'] for part in params.multipart if part['name'] == 'payload_json'))
            self.assertTrue(payload['flags'] & 32768)
            self.assertNotIn('accent_color', json.dumps(payload))
            self.assertEqual(payload['components'][0]['type'], 17)
        # The generated image is also valid independently of Discord transport.
        raw, caption = await self.worker.render(self.guild, 'monthly', [])
        with Image.open(BytesIO(raw)) as image:
            self.assertEqual(image.size, (1100, 960))
            self.assertEqual(image.format, 'PNG')
        self.assertIn('Monthly XP leaderboard', caption)
        await self.worker.write(self.channel, self.guild, 'daily', self.rows, self.message)
        kwargs = self.message.edit.await_args.kwargs
        with discord.http.handle_message_parameters(**{k:v for k,v in kwargs.items() if k != 'allowed_mentions'}) as params:
            payload = params.payload or json.loads(next(part['value'] for part in params.multipart if part['name'] == 'payload_json'))
            self.assertIn('attachment://kuroo-live.png', json.dumps(payload))

    async def test_reconnect_does_not_duplicate_refresh_loop_and_unload_cancels_it(self):
        waiting = asyncio.Event()
        bot = SimpleNamespace(wait_until_ready=waiting.wait)
        cog = Community(bot)
        await cog.on_ready()
        first = cog.live_refresh.get_task()
        await cog.on_ready()
        self.assertIs(cog.live_refresh.get_task(), first)
        await asyncio.sleep(0)
        cog.cog_unload()
        with self.assertRaises(asyncio.CancelledError):
            await first

    async def test_period_starts_are_utc_calendar_boundaries(self):
        self.assertEqual(community.period_start('weekly', T0), datetime(2029, 12, 31, tzinfo=timezone.utc))
        self.assertEqual(community.period_start('monthly', T0), datetime(2030, 1, 1, tzinfo=timezone.utc))
        stamp = datetime(2030, 1, 7, 1, tzinfo=timezone(timedelta(hours=5)))
        self.assertEqual(community.period_start('daily', stamp), datetime(2030, 1, 6, tzinfo=timezone.utc))
