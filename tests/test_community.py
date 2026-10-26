"""Economic/XP invariants and Discord boundaries, using disposable MongoDB when configured."""
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

import storage.users
import storage.welcomer_settings
from kuroo.engine.Bot import AutoShardedBot
from kuroo.memory.cache import cache
from kuroo.src.commands.community import Community, manager, send_card
from kuroo.src.commands.root import Root
from kuroo.src.events.on_member_join import on_member_join
from kuroo.style import sync_emojis
from kuroo.workflows import cards, community

T0 = datetime(2030, 1, 1, tzinfo=timezone.utc)


@unittest.skipUnless(os.getenv('KUROO_TEST_MONGO_URI'), 'Set KUROO_TEST_MONGO_URI for disposable MongoDB integration tests')
class CommunityMongoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from kuroo.workflows import cache as cache_module
        self.client = AsyncIOMotorClient(os.environ['KUROO_TEST_MONGO_URI'], serverSelectionTimeoutMS=3000)
        await self.client.admin.command('ping')
        self.db = self.client['kuroo_test_' + uuid4().hex]
        async def collection(name):
            return self.db[name]
        self.patches = [patch('storage.engine.get_collection', collection), patch('kuroo.workflows.community.get_collection', collection),
                        patch('kuroo.workflows.community.now', return_value=T0)]
        self.saved = (cache.users, cache.admins[:], cache.owners[:])
        cache.users, cache.admins, cache.owners = {}, [], []
        for item in self.patches:
            item.start()
        await storage.users.create_table()
        await storage.welcomer_settings.create_table()
        await community.prepare()

    async def asyncTearDown(self):
        await self.client.drop_database(self.db.name)
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        cache.users, cache.admins, cache.owners = self.saved

    async def test_xp_is_per_server_and_uses_persistent_cooldown(self):
        await community.award_xp(1, 100)
        self.assertIsNone(await community.award_xp(1, 100))
        await community.award_xp(2, 100)
        with patch('kuroo.workflows.community.now', return_value=T0 + timedelta(seconds=60)):
            await community.award_xp(1, 100)
        self.assertEqual((await community.rank(1, 100))['xp'], 30)
        self.assertEqual((await community.rank(2, 100))['xp'], 15)

    async def test_simultaneous_xp_only_awards_once(self):
        results = await asyncio.gather(*[community.award_xp(1, 100) for _ in range(10)])
        self.assertEqual(sum(value is not None for value in results), 1)
        self.assertEqual((await community.rank(1, 100))['xp'], 15)

    async def test_level_boundaries_and_deterministic_rank_ties(self):
        self.assertEqual([community.level_for_xp(n) for n in [0, 99, 100, 399, 400]], [0, 0, 1, 1, 2])
        await self.db.member_levels.insert_many([{'guild_id': 1, 'user_id': i, 'xp': 100} for i in [100, 101]])
        self.assertEqual((await community.rank(1, 100))['rank'], 1)
        self.assertEqual((await community.rank(1, 101))['rank'], 2)
        self.assertIsNone((await community.rank(1, 999))['rank'])
        await self.db.member_levels.update_one({'guild_id': 1, 'user_id': 100}, {'$set': {'xp': 399}})
        result = await community.award_xp(1, 100)
        self.assertTrue(result['leveled_up'])
        self.assertEqual(result['level'], 2)

    async def test_daily_concurrent_claim_and_24_hour_boundary(self):
        results = await asyncio.gather(*[community.earn(100, 'daily') for _ in range(10)], return_exceptions=True)
        self.assertEqual(sum(not isinstance(value, Exception) for value in results), 1)
        self.assertEqual((await community.wallet(100))['balance'], 250)
        with patch('kuroo.workflows.community.now', return_value=T0 + timedelta(hours=24) - timedelta(milliseconds=1)):
            with self.assertRaises(community.CommunityError):
                await community.earn(100, 'daily')
        with patch('kuroo.workflows.community.now', return_value=T0 + timedelta(hours=24)):
            await community.earn(100, 'daily')
        self.assertEqual((await community.wallet(100))['balance'], 500)

    async def test_work_is_separate_and_persisted(self):
        await community.earn(100, 'daily')
        record, reward = await community.earn(100, 'work')
        self.assertTrue(80 <= reward <= 160)
        self.assertEqual(record['balance'], 250 + reward)
        with self.assertRaises(community.CommunityError):
            await community.earn(100, 'work')
        with patch('kuroo.workflows.community.now', return_value=T0 + timedelta(hours=1)):
            await community.earn(100, 'work')

    async def test_banking_conserves_existing_coins_under_concurrency(self):
        await storage.users.insert(user_id=100, balance=1000, type='admin', admin_no_prefix=False)
        results = await asyncio.gather(*[community.move_bank(100, 600) for _ in range(10)], return_exceptions=True)
        self.assertEqual(sum(not isinstance(value, Exception) for value in results), 1)
        record = await community.wallet(100)
        self.assertEqual((record['balance'], record['bank']), (400, 600))
        await community.move_bank(100, 200, withdraw=True)
        record = await community.wallet(100)
        self.assertEqual(record['balance'] + record['bank'], 1000)
        self.assertEqual(record['type'], 'admin')
        self.assertFalse(record['admin_no_prefix'])
        self.assertEqual(cache.users['100']['balance'], 600)

    async def test_invalid_amounts_and_overdraft_do_not_mutate(self):
        await storage.users.insert(user_id=100, balance=30)
        for value in [-1, 0, 1.5, True, community.MAX_AMOUNT + 1]:
            with self.assertRaises(community.CommunityError):
                await community.move_bank(100, value)
        with self.assertRaises(community.CommunityError):
            await community.move_bank(100, 31)
        with self.assertRaises(community.CommunityError):
            await community.move_bank(100, 1, withdraw=True)
        self.assertEqual((await community.wallet(100))['balance'], 30)

    async def test_purchase_and_ownership_are_atomic(self):
        await storage.users.insert(user_id=100, balance=1000)
        with self.assertRaises(community.CommunityError):
            await community.equip(100, 'night-owl')
        results = await asyncio.gather(*[community.buy(100, 'night-owl') for _ in range(10)], return_exceptions=True)
        self.assertEqual(sum(not isinstance(value, Exception) for value in results), 1)
        record = await community.equip(100, 'night-owl')
        self.assertEqual(record['owned_titles'], ['night-owl'])
        self.assertEqual(record['balance'], 500)
        self.assertEqual(record['card_title'], 'night-owl')
        self.assertEqual((await community.equip(100, 'none'))['card_title'], 'none')
        with self.assertRaises(community.CommunityError):
            await community.buy(100, 'aurora')

    async def test_old_wallets_and_root_commands_share_global_economy(self):
        record = await storage.users.insert(user_id=100, balance=10)
        await community.earn(100, 'daily')
        await community.admin_balance(100, 50, mode='add')
        self.assertEqual((await community.wallet(100))['balance'], 310)
        await community.admin_balance(100, 1000, mode='remove')
        self.assertEqual((await community.wallet(100))['balance'], 0)
        await community.admin_balance(100, 90, mode='set')
        self.assertEqual((await community.wallet(100))['id'], record['id'])
        self.assertEqual(cache.users['100']['balance'], 90)
        await community.admin_balance(100, community.MAX_AMOUNT + 1, mode='set')
        self.assertEqual((await community.wallet(100))['balance'], community.MAX_AMOUNT + 1)

    async def test_leaderboard_paging_and_settings_persist(self):
        await self.db.member_levels.insert_many([{'guild_id': 1, 'user_id': i, 'xp': 100} for i in range(11)])
        await self.db.member_levels.insert_one({'guild_id': 2, 'user_id': 30, 'xp': 9999})
        rows, pages = await community.leaderboard(1, 2)
        self.assertEqual((len(rows), pages, rows[0]['user_id']), (1, 2, 10))
        await community.configure(1, leveling=False, level_channel=42, leave_channel=43)
        self.assertFalse((await community.settings(1))['leveling'])
        self.assertEqual((await community.settings(1))['leave_channel'], 43)

    async def test_configurable_xp_cooldown_and_disabled_state_persist(self):
        await community.configure_levels(1, xp_amount=100, xp_cooldown=5)
        result = await community.award_xp(1, 100)
        self.assertEqual((result['xp'], result['level']), (100, 1))
        self.assertIsNone(await community.award_xp(1, 100))
        with patch('kuroo.workflows.community.now', return_value=T0 + timedelta(seconds=5)):
            await community.award_xp(1, 100)
        self.assertEqual((await community.rank(1, 100))['xp'], 200)
        await community.configure(1, leveling=False)
        with patch('kuroo.workflows.community.now', return_value=T0 + timedelta(hours=1)):
            self.assertIsNone(await community.award_xp(1, 100))
        for values in [dict(xp_amount=0), dict(xp_amount=True), dict(xp_cooldown=4)]:
            with self.assertRaises(community.CommunityError):
                await community.configure_levels(1, **values)

    async def test_atomic_xp_edits_clamp_and_isolate_guilds(self):
        await asyncio.gather(*[community.edit_xp(1, 100, 100, mode='add') for _ in range(10)])
        self.assertEqual((await community.rank(1, 100))['xp'], 1000)
        self.assertEqual((await community.rank(2, 100))['xp'], 0)
        self.assertEqual((await community.edit_xp(1, 100, 5000, mode='remove'))['xp'], 0)
        self.assertEqual((await community.edit_xp(1, 100, 400, mode='set'))['level'], 2)
        await community.edit_xp(1, 100, community.MAX_AMOUNT, mode='add')
        self.assertEqual((await community.rank(1, 100))['xp'], community.MAX_AMOUNT)
        await community.award_xp(1, 100)
        self.assertEqual((await community.rank(1, 100))['xp'], community.MAX_AMOUNT)

    async def test_exclusions_and_message_configuration_are_persistent(self):
        await asyncio.gather(*[community.xp_exclusion(1, 'channels', cid) for cid in [3, 3, 4]])
        await community.xp_exclusion(1, 'roles', 9)
        await community.xp_exclusion(1, 'channels', 3, remove=True)
        await community.configure_levels(1, level_message='{user} in {server}: {level} / {xp}')
        config = await community.level_settings(1)
        self.assertEqual(config['xp_ignored_channels'], [4])
        self.assertEqual(config['xp_ignored_roles'], [9])
        for template in ['{user.id}', '{user!r}', '{xp:100000}', '{unknown}', '{', 'x'*1001]:
            with self.assertRaises(community.CommunityError):
                await community.configure_levels(1, level_message=template)

    async def test_global_reward_configuration_respects_existing_claim_times(self):
        await community.earn(100, 'daily')
        await community.configure_rewards('daily', 1000, 1000, 60)
        with self.assertRaises(community.CommunityError):
            await community.earn(100, 'daily')
        with patch('kuroo.workflows.community.now', return_value=T0 + timedelta(seconds=60)):
            record, reward = await community.earn(100, 'daily')
        self.assertEqual((record['balance'], reward), (1250, 1000))
        await community.configure_rewards('work', 300, 300, 120)
        self.assertEqual((await community.earn(100, 'work'))[1], 300)
        for args in [('daily', 1, 2, 60), ('work', 20, 10, 60), ('work', 1, 2, 0)]:
            with self.assertRaises(community.CommunityError):
                await community.configure_rewards(*args)

    async def test_admin_reward_reset_preserves_wallet_and_other_cooldown(self):
        await community.earn(100, 'daily')
        await community.earn(100, 'work')
        before = await community.wallet(100)
        await community.reset_earn_cooldown(100, 'daily')
        self.assertEqual((await community.wallet(100))['balance'], before['balance'])
        await community.earn(100, 'daily')
        with self.assertRaises(community.CommunityError):
            await community.earn(100, 'work')
        await community.reset_earn_cooldown(100, 'all')
        await community.earn(100, 'work')


class CommunityBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_sync_runs_before_cogs(self):
        bot = AutoShardedBot()
        order = []
        async def sync(instance):
            self.assertIs(instance, bot)
            order.append('sync')
        async def load(name):
            order.append(name)
        async def publish():
            order.append('slash')
            return []
        try:
            with patch.object(bot.BotConfig, 'SYNC_EMOJIS', True), patch('kuroo.style.sync_emojis.sync_application_emojis', side_effect=sync), patch.object(bot, 'load_extension', side_effect=load), patch.object(bot.tree, 'sync', side_effect=publish):
                await bot.setup_hook()
            self.assertEqual(order, ['sync', 'kuroo.src', 'slash'])
            with patch.object(bot.BotConfig, 'SYNC_EMOJIS', False), patch('kuroo.style.sync_emojis.sync_application_emojis', new=AsyncMock()) as sync_mock, patch.object(bot, 'load_extension', new=AsyncMock()), patch.object(bot.tree, 'sync', new=AsyncMock(return_value=[])):
                await bot.setup_hook()
                sync_mock.assert_not_awaited()
        finally:
            await bot.http.close()

    async def test_cards_are_real_pngs_and_hostile_names_fit(self):
        for raw in [cards.rank_card(name='Very long name ' * 100, guild='Server', xp=225, level=1, rank=3, avatar_bytes=b'invalid'),
                    cards.leaderboard_card(guild='Server', rows=[]),
                    cards.member_card(name='User\n\x00name', guild='Server', count=15),
                    cards.member_card(name='User', guild='Server', count=14, leaving=True)]:
            with Image.open(BytesIO(raw)) as image:
                self.assertEqual(image.format, 'PNG')
                self.assertEqual(image.width, 1100)
                self.assertLess(max(image.getpixel((30, 30))), 65)
        self.assertEqual(cards.clean('A\n\x00B'), 'AB')

    async def test_avatar_timeout_falls_back(self):
        asset = SimpleNamespace(read=AsyncMock(side_effect=asyncio.TimeoutError()))
        asset.with_static_format = lambda kind: asset
        member = SimpleNamespace(display_avatar=SimpleNamespace(with_size=lambda size: asset))
        self.assertIsNone(await cards.avatar_data(member))

    async def test_card_serialization_is_v2_media_without_accents(self):
        async def capture(**kwargs):
            with discord.http.handle_message_parameters(**kwargs) as params:
                body = json.loads(params.multipart[0]['value'])
                self.assertTrue(body['flags'] & 32768)
                self.assertIn('attachment://card.png', json.dumps(body))
                self.assertNotIn('accent_color', json.dumps(body))
                self.assertEqual(body['components'][1]['type'], 12)
                self.assertFalse(body['allowed_mentions']['parse'])
        await send_card(SimpleNamespace(send=capture), b'png', 'card.png', 'Preview')

    async def test_manager_gate_checks_bot_admin_or_manage_guild(self):
        predicate = manager().predicate
        with patch('kuroo.src.commands.community.checks.check_is_admin_predicate', return_value=False):
            for allowed in [False, True]:
                ctx = SimpleNamespace(guild=object(), author=SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=allowed)))
                self.assertEqual(await predicate(ctx), allowed)
        with patch('kuroo.src.commands.community.checks.check_is_admin_predicate', return_value=True):
            self.assertTrue(await predicate(SimpleNamespace(author=object())))

    async def test_xp_excludes_bots_dms_webhooks_commands_and_disabled_servers(self):
        bot = SimpleNamespace(get_context=AsyncMock(return_value=SimpleNamespace(valid=True)))
        cog = Community(bot)
        base = dict(author=SimpleNamespace(id=100, bot=False), guild=SimpleNamespace(id=1), webhook_id=None, content='hello world', channel=SimpleNamespace(id=3))
        with patch('kuroo.src.commands.community.community.award_xp', new=AsyncMock()) as award:
            for changed in [{'guild': None}, {'author': SimpleNamespace(bot=True)}, {'webhook_id': 1}, {}]:
                await cog.on_message(SimpleNamespace(**(base | changed)))
            award.assert_not_awaited()
        bot.get_context.return_value = SimpleNamespace(valid=False)
        with patch('kuroo.src.commands.community.community.settings', new=AsyncMock(return_value={'leveling': False})), patch('kuroo.src.commands.community.checks.check_blacklist_predicate', return_value=True), patch('kuroo.src.commands.community.checks.check_ignore_predicate', return_value=True), patch('kuroo.src.commands.community.community.award_xp', new=AsyncMock()) as award:
            await cog.on_message(SimpleNamespace(**base))
            award.assert_not_awaited()

    async def test_commands_do_not_consume_message_xp_cooldown(self):
        bot = SimpleNamespace(get_context=AsyncMock(return_value=SimpleNamespace(valid=True)))
        cog = Community(bot)
        message = SimpleNamespace(author=SimpleNamespace(id=100, bot=False), guild=SimpleNamespace(id=1, get_channel=lambda cid: None),
                                  webhook_id=None, content='rank', channel=SimpleNamespace(id=3))
        with patch('kuroo.src.commands.community.community.settings', new=AsyncMock(return_value={})), patch('kuroo.src.commands.community.checks.check_blacklist_predicate', return_value=True), patch('kuroo.src.commands.community.checks.check_ignore_predicate', return_value=True), patch('kuroo.src.commands.community.community.award_xp', new=AsyncMock(return_value={'leveled_up': False})) as award:
            await cog.on_message(message)
            award.assert_not_awaited()
            bot.get_context.return_value = SimpleNamespace(valid=False)
            await cog.on_message(message)
            award.assert_awaited_once_with(1, 100)
            await cog.on_message(message)
            # Every eligible message reaches the atomic persistent limiter.
            self.assertEqual(award.await_count, 2)

    async def test_excluded_channels_and_roles_never_award_xp(self):
        bot = SimpleNamespace(get_context=AsyncMock(return_value=SimpleNamespace(valid=False)))
        cog = Community(bot)
        message = SimpleNamespace(author=SimpleNamespace(id=100, bot=False, roles=[SimpleNamespace(id=9)]),
                                  guild=SimpleNamespace(id=1), channel=SimpleNamespace(id=3), webhook_id=None, content='hello')
        for config in [{'xp_ignored_channels': [3]}, {'xp_ignored_roles': [9]}]:
            with patch('kuroo.workflows.community.settings', new=AsyncMock(return_value=config)), patch('kuroo.src.commands.community.checks.check_blacklist_predicate', return_value=True), patch('kuroo.src.commands.community.checks.check_ignore_predicate', return_value=True), patch('kuroo.workflows.community.award_xp', new=AsyncMock()) as award:
                await cog.on_message(message)
                award.assert_not_awaited()

    async def test_level_template_renders_without_pinging_members(self):
        channel = SimpleNamespace(send=AsyncMock())
        bot = SimpleNamespace(get_context=AsyncMock(return_value=SimpleNamespace(valid=False)))
        message = SimpleNamespace(author=SimpleNamespace(id=100, bot=False, roles=[], display_name='A*'),
                                  guild=SimpleNamespace(id=1, name='Server', get_channel=lambda cid: channel),
                                  channel=SimpleNamespace(id=3), webhook_id=None, content='hello')
        with patch.object(Community, '_award_roles', new=AsyncMock(return_value=[])), patch('kuroo.workflows.community.settings', new=AsyncMock(return_value={'level_channel': 2, 'level_message': '{user} / {level} / {xp} / {server}'})), patch('kuroo.src.commands.community.checks.check_blacklist_predicate', return_value=True), patch('kuroo.src.commands.community.checks.check_ignore_predicate', return_value=True), patch('kuroo.workflows.community.award_xp', new=AsyncMock(return_value={'leveled_up': True, 'level': 2, 'xp': 400})):
            await Community(bot).on_message(message)
        self.assertIn('/ 2 / 400 / Server', channel.send.await_args.args[0])
        self.assertFalse(channel.send.await_args.kwargs['allowed_mentions'].everyone)

    async def test_new_permission_boundaries_and_server_economy_toggle(self):
        from discord.ext import commands
        for command in [Community.economy_rewards, Community.economy_balance, Community.economy_reset]:
            check = command.checks[0]
            with patch('kuroo.src.checks.checks.check_is_admin_predicate', return_value=False):
                self.assertFalse(check(SimpleNamespace(author=object())))
            with patch('kuroo.src.checks.checks.check_is_admin_predicate', return_value=True):
                self.assertTrue(check(SimpleNamespace(author=object())))
        for command in [Community.level_rate, Community.level_announcements, Community.level_message, Community.level_exclude_role,
                        Community.level_exclude_channel, Community.xp_edit, Community.economy_toggle]:
            with patch('kuroo.src.commands.community.checks.check_is_admin_predicate', return_value=False):
                for allowed in [False, True]:
                    ctx = SimpleNamespace(guild=object(), author=SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=allowed)))
                    self.assertEqual(await command.checks[0](ctx), allowed)
        ctx = SimpleNamespace(guild=SimpleNamespace(id=1), command=Community.daily)
        with patch('kuroo.workflows.community.settings', new=AsyncMock(return_value={'economy': False})):
            with self.assertRaises(commands.DisabledCommand):
                await Community(None).cog_check(ctx)

    async def test_announcement_channel_alias_and_disable_use_same_configuration(self):
        ctx = SimpleNamespace(guild=SimpleNamespace(id=1), defer=AsyncMock(), send=AsyncMock())
        cog = Community(None)
        channel = SimpleNamespace(id=42, mention='<#42>')
        with patch('kuroo.workflows.community.configure', new=AsyncMock()) as configure:
            await Community.level_announcements.callback(cog, ctx, channel)
            configure.assert_awaited_with(1, level_channel=42)
            await Community.level_announcements.callback(cog, ctx)
            configure.assert_awaited_with(1, level_channel=0)

    async def test_economy_acknowledges_interactions_before_database_work(self):
        order = []
        async def defer():
            order.append('defer')
        async def earn(user_id, kind):
            order.append('earn')
            return {'balance': 250}, 250
        ctx = SimpleNamespace(defer=defer, author=SimpleNamespace(id=100), send=AsyncMock())
        with patch('kuroo.workflows.community.earn', side_effect=earn):
            await Community.daily.callback(None, ctx)
        self.assertEqual(order, ['defer', 'earn'])

    async def test_original_owner_balance_commands_use_atomic_service(self):
        ctx = SimpleNamespace(send=AsyncMock())
        user = SimpleNamespace(id=100, display_name='User')
        for command, mode in [(Root.givebalance, 'add'), (Root.removebalance, 'remove'), (Root.setbalance, 'set')]:
            with patch('kuroo.workflows.community.admin_balance', new=AsyncMock(return_value={'balance': 10})) as update:
                await command.callback(None, ctx, user, 5)
                update.assert_awaited_once_with(100, 5, mode=mode)
        with patch('kuroo.workflows.community.admin_balance', new=AsyncMock(return_value={'balance': 0})) as update:
            await Root.setbalance.callback(None, ctx, user, -5)
            update.assert_awaited_once_with(100, 0, mode='set')

    async def test_leave_cards_route_only_to_configured_channel(self):
        channel = SimpleNamespace(send=AsyncMock())
        member = SimpleNamespace(display_name='User', guild=SimpleNamespace(id=1, get_channel=lambda cid: channel if cid == 42 else None))
        cog = Community(SimpleNamespace())
        with patch('kuroo.src.commands.community.community.settings', new=AsyncMock(return_value={'leave_channel': 42})), patch('kuroo.src.commands.community.cards.member_image', new=AsyncMock(return_value=b'png')), patch('kuroo.src.commands.community.send_card', new=AsyncMock()) as send:
            await cog.on_member_remove(member)
            self.assertEqual(send.await_args.args[0], channel)
        with patch('kuroo.src.commands.community.community.settings', new=AsyncMock(return_value={})), patch('kuroo.src.commands.community.send_card', new=AsyncMock()) as send:
            await cog.on_member_remove(member)
            send.assert_not_awaited()

    async def test_welcome_keeps_custom_text_and_uses_media_gallery(self):
        channel = SimpleNamespace(send=AsyncMock())
        guild = SimpleNamespace(id=1, name='Server', get_channel=lambda cid: channel)
        member = SimpleNamespace(guild=guild)
        bot = SimpleNamespace(cache=SimpleNamespace(welcomer_settings={'1': {'welcome': True, 'welcome_channel': 42, 'welcome_message': True, 'welcome_message_content': 'custom', 'welcome_embed': False}}))
        with patch('kuroo.src.events.on_member_join.fetch_variables', return_value='custom'), patch('kuroo.workflows.cards.member_image', new=AsyncMock(return_value=b'png')):
            await on_member_join(bot).guild_welcome_module(member)
        kwargs = channel.send.await_args.kwargs
        self.assertEqual(kwargs['content'], 'custom')
        self.assertEqual(kwargs['view'].to_components()[0]['type'], 12)
        self.assertEqual(kwargs['file'].filename, 'kuroo-welcome.png')



async def response_chunks(*chunks):
    for chunk in chunks:
        yield chunk


class EmojiSyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.saved = sync_emojis._resolved.copy()
        sync_emojis._resolved.clear()

    async def asyncTearDown(self):
        sync_emojis._resolved.clear()
        sync_emojis._resolved.update(self.saved)

    async def test_reuses_by_name_and_updates_running_module(self):
        icons = SimpleNamespace(COIN='<:coin:100>', MIX='Before <a:spin:101> after')
        bot = SimpleNamespace(fetch_application_emojis=AsyncMock(return_value=[discord.PartialEmoji(name='coin', id=200), discord.PartialEmoji(name='spin', id=201, animated=True)]), create_application_emoji=AsyncMock())
        with patch('kuroo.style.sync_emojis.emoji', icons):
            await sync_emojis.sync_application_emojis(bot)
            self.assertEqual(icons.COIN, '<:coin:200>')
            self.assertEqual(icons.MIX, 'Before <a:spin:201> after')
            bot.create_application_emoji.assert_not_awaited()

    async def test_uploads_missing_emoji_once_and_can_reapply_after_reload(self):
        icons = SimpleNamespace(ONE='<:coin:100>', TWO='<:coin:100>')
        response = SimpleNamespace(raise_for_status=lambda: None, content=SimpleNamespace(iter_chunked=lambda size: response_chunks(b'im', b'age')))
        response_context = AsyncMock()
        response_context.__aenter__.return_value = response
        session = SimpleNamespace(get=lambda url: response_context)
        session_context = AsyncMock()
        session_context.__aenter__.return_value = session
        bot = SimpleNamespace(fetch_application_emojis=AsyncMock(return_value=[]), create_application_emoji=AsyncMock(return_value=discord.PartialEmoji(name='coin', id=200)))
        with patch('kuroo.style.sync_emojis.emoji', icons), patch('kuroo.style.sync_emojis.aiohttp.ClientSession', return_value=session_context):
            await sync_emojis.sync_application_emojis(bot)
            bot.create_application_emoji.assert_awaited_once_with(name='coin', image=b'image')
            self.assertEqual(icons.ONE, '<:coin:200>')
            icons.ONE = '<:coin:100>'
            sync_emojis.apply_synced_emojis()
            self.assertEqual(icons.ONE, '<:coin:200>')

    async def test_unavailable_source_uses_unicode_fallback(self):
        icons = SimpleNamespace(ONE='<:coin:100>')
        response = SimpleNamespace(raise_for_status=lambda: None, content=SimpleNamespace(iter_chunked=lambda size: response_chunks(b'x' * (256*1024), b'x')))
        response_context = AsyncMock()
        response_context.__aenter__.return_value = response
        session_context = AsyncMock()
        session_context.__aenter__.return_value = SimpleNamespace(get=lambda url: response_context)
        bot = SimpleNamespace(fetch_application_emojis=AsyncMock(return_value=[]), create_application_emoji=AsyncMock())
        with patch('kuroo.style.sync_emojis.emoji', icons), patch('kuroo.style.sync_emojis.aiohttp.ClientSession', return_value=session_context):
            await sync_emojis.sync_application_emojis(bot)
            self.assertEqual(icons.ONE, '✦')
            bot.create_application_emoji.assert_not_awaited()

    async def test_discord_failure_does_not_abort_startup(self):
        error = discord.HTTPException(SimpleNamespace(status=403, reason='Forbidden'), 'denied')
        bot = SimpleNamespace(fetch_application_emojis=AsyncMock(side_effect=error))
        icons = SimpleNamespace(ONE='<:coin:100>')
        with patch('kuroo.style.sync_emojis.emoji', icons):
            await sync_emojis.sync_application_emojis(bot)
        self.assertEqual(icons.ONE, '<:coin:100>')
