"""Run Mongo checks with KUROO_TEST_MONGO_URI pointing to a disposable local database."""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import discord
import httpx
from motor.motor_asyncio import AsyncIOMotorClient

import storage
from kuroo.memory.cache import cache
from kuroo.workflows import premium
from kuroo.workflows.premium_state import GuildRecord, utc
from kuroo.src.checks import checks
from kuroo.src.commands.premium import Premium, can_claim
from kuroo.src.commands.utils import Utils
from kuroo.src.commands.help import Help, HomeView
from kuroo.engine.Bot import AutoShardedBot
from kuroo.surface.server import app
from kuroo.surface.routes import topgg
from kuroo.config.config import BotConfigClass

T0 = datetime(2030, 1, 1, tzinfo=timezone.utc)


class PermissionAndLayoutTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.saved = (cache.users, cache.admins[:], cache.owners[:])
        cache.users, cache.admins, cache.owners = {}, [100], []
        self.bot = AutoShardedBot()
        await self.bot._async_setup_hook()
        self.bot._connection.user = discord.ClientUser(state=self.bot._connection, data={
            'id': '42', 'username': 'Kuroo', 'discriminator': '0', 'avatar': None,
        })

    async def asyncTearDown(self):
        cache.users, cache.admins, cache.owners = self.saved
        await self.bot.http.close()

    async def test_admin_commands_reject_server_administrators(self):
        admin = SimpleNamespace(author=SimpleNamespace(id=100))
        ordinary = SimpleNamespace(author=SimpleNamespace(id=101, guild_permissions=SimpleNamespace(administrator=True)))
        for command in (Premium.premiumcode, Premium.premiumgrant, Premium.premiumrevoke):
            self.assertTrue(command.checks[0](admin))
            self.assertFalse(command.checks[0](ordinary))

    async def test_default_no_prefix_and_disabled_preference_in_guild_and_dm(self):
        message = SimpleNamespace(author=SimpleNamespace(id=100), guild=None)
        self.assertIn('', await self.bot.get_prefix(message))
        message.guild = SimpleNamespace(id=200)
        self.assertIn('', await self.bot.get_prefix(message))
        cache.users['100'] = {'admin_no_prefix': False, 'no_prefix': True, 'no_prefix_subscription': True}
        self.assertNotIn('', await self.bot.get_prefix(message))
        self.assertIn('?', await self.bot.get_prefix(message))
        message.author.id = 101
        self.assertNotIn('', await self.bot.get_prefix(message))

    async def test_expired_user_subscription_does_not_enable_no_prefix(self):
        cache.users['101'] = {'no_prefix': True, 'no_prefix_subscription': True,
                              'no_prefix_end': datetime.now(timezone.utc) - timedelta(seconds=1)}
        message = SimpleNamespace(author=SimpleNamespace(id=101), guild=None)
        self.assertNotIn('', await self.bot.get_prefix(message))

    async def test_noprefix_command_persists_explicit_admin_preference(self):
        cache.users['100'] = {'id': 1, 'type': 'admin'}
        ctx = SimpleNamespace(author=SimpleNamespace(id=100), send=AsyncMock())
        cog = Utils(self.bot)
        with patch('storage.users._store', SimpleNamespace(update=AsyncMock())) as store:
            update = store.update
            await Utils.noprefix_command.callback(cog, ctx, 'off')
            update.assert_awaited_once_with({'id': 1, 'admin_no_prefix': False})

    async def test_help_banner_is_first_component_and_contains_no_accent(self):
        self.bot.help_banner_url = 'https://example.com/bot-banner.png'
        ctx = SimpleNamespace(guild=SimpleNamespace(id=200), author=SimpleNamespace(id=100))
        view = HomeView(self.bot, ctx, [])
        self.assertEqual(view.to_components()[0]['type'], 12)
        self.assertIn('bot-banner.png', json.dumps(view.to_components()[0]))
        with discord.http.handle_message_parameters(view=view) as params:
            self.assertNotIn('accent_color', json.dumps(params.payload))
        view.stop()

    async def test_help_fetches_the_uploaded_bot_banner(self):
        self.bot.fetch_user = AsyncMock(return_value=SimpleNamespace(banner=SimpleNamespace(url='https://example.com/uploaded-banner.png')))
        self.bot.tree.fetch_commands = AsyncMock(return_value=[])
        ctx = SimpleNamespace(interaction=None, guild=SimpleNamespace(id=200),
                              author=SimpleNamespace(id=100), send=AsyncMock())
        cog = Help(self.bot)
        await Help.help.callback(cog, ctx)
        self.bot.fetch_user.assert_awaited_once_with(42)
        view = ctx.send.await_args.kwargs['view']
        self.assertIn('uploaded-banner.png', json.dumps(view.to_components()[0]))
        view.stop()

    async def test_help_banner_override_avoids_profile_request(self):
        self.bot.fetch_user = AsyncMock()
        self.bot.tree.fetch_commands = AsyncMock(return_value=[])
        ctx = SimpleNamespace(interaction=None, guild=SimpleNamespace(id=200),
                              author=SimpleNamespace(id=100), send=AsyncMock())
        with patch.object(self.bot.BotConfig, 'BOT_BANNER_URL', 'https://example.com/custom-banner.png'):
            await Help.help.callback(Help(self.bot), ctx)
        self.bot.fetch_user.assert_not_awaited()
        view = ctx.send.await_args.kwargs['view']
        self.assertIn('custom-banner.png', json.dumps(view.to_components()[0]))
        view.stop()

    async def test_server_claim_permissions(self):
        self.assertTrue(can_claim(SimpleNamespace(id=100)))
        self.assertTrue(can_claim(SimpleNamespace(id=101, guild_permissions=SimpleNamespace(manage_guild=True))))
        self.assertFalse(can_claim(SimpleNamespace(id=101, guild_permissions=SimpleNamespace(manage_guild=False))))

    async def test_guild_cache_checks_exact_expiry_including_naive_mongo_dates(self):
        for end in (T0, T0.replace(tzinfo=None)):
            record = GuildRecord(subscription=premium.PREMIUM_TIER, subscription_end=end)
            with patch('kuroo.workflows.premium_state.datetime') as clock:
                clock.now.return_value = T0
                self.assertEqual(record.get('subscription'), 'free')
                self.assertEqual(record['subscription'], 'free')
        self.assertEqual(GuildRecord(subscription=premium.PREMIUM_TIER).get('subscription'), premium.PREMIUM_TIER)


@unittest.skipUnless(os.getenv('KUROO_TEST_MONGO_URI'), 'Set KUROO_TEST_MONGO_URI for disposable MongoDB integration tests')
class PremiumMongoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from kuroo.workflows import cache as cache_module
        self.cache_module = cache_module
        premium._locks.clear()
        self.client = AsyncIOMotorClient(os.environ['KUROO_TEST_MONGO_URI'], serverSelectionTimeoutMS=3000)
        await self.client.admin.command('ping')
        self.database = self.client['kuroo_test_' + uuid4().hex]
        async def collection(name):
            return self.database[name]
        self.patches = [patch('storage.engine.get_collection', collection),
                        patch('kuroo.workflows.premium.get_collection', collection),
                        patch('kuroo.workflows.premium.now', return_value=T0)]
        for patched in self.patches:
            patched.start()
        await storage.guilds.create_table()
        await storage.redeem_codes.create_table()
        await storage.users.create_table()
        await storage.welcomer_settings.create_table()

    async def asyncTearDown(self):
        await self.client.drop_database(self.database.name)
        self.client.close()
        for patched in reversed(self.patches):
            patched.stop()

    async def redeem(self, code, user=100, guild=200):
        return await premium.redeem(code, user, guild, can_manage_guild=True)

    async def test_generated_code_grants_one_day_and_can_only_be_claimed_once(self):
        code = await premium.create_code()
        self.assertEqual(code['expires_at'], T0 + timedelta(hours=12))
        record = await self.redeem(code['code'])
        self.assertEqual(utc(record['subscription_end']), T0 + timedelta(days=1))
        with self.assertRaises(premium.PremiumError):
            await self.redeem(code['code'])

    async def test_code_is_invalid_at_twelve_hour_boundary(self):
        code = await premium.create_code()
        with patch('kuroo.workflows.premium.now', return_value=T0 + timedelta(hours=12)):
            with self.assertRaises(premium.PremiumError):
                await self.redeem(code['code'])
        stored = await self.database.redeem_codes.find_one({'code': code['code']})
        self.assertFalse(stored['claimed'])

    async def test_unauthorized_claim_does_not_consume_code(self):
        code = await premium.create_code()
        with self.assertRaises(premium.PremiumError):
            await premium.redeem(code['code'], 101, 200)
        self.assertFalse((await self.database.redeem_codes.find_one({'code': code['code']}))['claimed'])

    async def test_concurrent_claims_cannot_grant_two_guilds(self):
        code = await premium.create_code()
        results = await asyncio.gather(self.redeem(code['code'], 100, 200),
                                       self.redeem(code['code'], 101, 201), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, premium.PremiumError) for result in results), 1)
        self.assertEqual(await self.database.guilds.count_documents({'subscription': premium.PREMIUM_TIER}), 1)

    async def test_failed_grant_is_retryable_without_extending_reward(self):
        code = await premium.create_code()
        original = premium.grant
        async def fail_after_apply(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError('Simulated process interruption')
        with patch('kuroo.workflows.premium.grant', side_effect=fail_after_apply):
            with self.assertRaises(RuntimeError):
                await self.redeem(code['code'])
        with patch('kuroo.workflows.premium.now', return_value=T0 + timedelta(hours=1)):
            result = await self.redeem(code['code'])
        self.assertEqual(utc(result['subscription_end']), T0 + timedelta(days=1))

    async def test_revoke_blocks_retry_of_reward_issued_before_revocation(self):
        code = await premium.create_code()
        with patch('kuroo.workflows.premium.grant', side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError):
                await self.redeem(code['code'])
        with patch('kuroo.workflows.premium.now', return_value=T0 + timedelta(minutes=1)):
            await premium.revoke(200)
            with self.assertRaises(premium.PremiumError):
                await self.redeem(code['code'])
        record = await storage.guilds.get(guild_id=200)
        self.assertEqual(record['subscription'], 'free')

    async def test_grant_and_revoke_arbitrary_server_id(self):
        result = await premium.grant(777, until=T0 + timedelta(days=7), issued_at=T0)
        self.assertEqual(result['guild_id'], 777)
        result = await premium.revoke(777)
        self.assertEqual(result['subscription'], 'free')
        self.assertIsNone(result['subscription_end'])

    async def test_revocation_restores_free_limits_and_disables_premium_welcomer(self):
        await premium.grant(200, until=T0 + timedelta(days=1), issued_at=T0)
        settings = await storage.welcomer_settings.get(guild_id=200)
        await storage.welcomer_settings.update(id=settings['id'], autoroles=['1', '2', '3', '4'], autonick=True)
        await premium.revoke(200)
        settings = await storage.welcomer_settings.get(guild_id=200)
        self.assertEqual(settings['autoroles_limit'], 3)
        self.assertEqual(settings['autoroles'], ['1', '2', '3'])
        self.assertFalse(settings['autonick'])

    async def test_lifetime_and_longer_paid_tiers_are_preserved(self):
        for guild_id, end in ((200, None), (201, T0 + timedelta(days=30))):
            await storage.guilds.insert(guild_id=guild_id, subscription='golden_guild_premium', subscription_end=end)
            result = await premium.grant(guild_id, until=T0 + timedelta(hours=12), issued_at=T0)
            self.assertEqual(result['subscription'], 'golden_guild_premium')
            self.assertEqual(utc(result['subscription_end']) if end else None, end)

    async def test_expiration_sweep_does_not_erase_renewal(self):
        await premium.grant(200, until=T0 + timedelta(hours=12), issued_at=T0)
        with patch('kuroo.workflows.premium.now', return_value=T0 + timedelta(hours=12)):
            await premium.grant(200, until=T0 + timedelta(days=1), issued_at=T0 + timedelta(hours=12))
            await premium.expire()
        self.assertEqual((await storage.guilds.get(guild_id=200))['subscription'], premium.PREMIUM_TIER)
        with patch('kuroo.workflows.premium.now', return_value=T0 + timedelta(days=1)):
            await premium.expire()
        self.assertEqual((await storage.guilds.get(guild_id=200))['subscription'], 'free')

    async def test_vote_delivery_is_idempotent_and_reward_is_for_one_guild(self):
        await premium.record_vote('1234', 100, T0)
        with patch('kuroo.workflows.premium.now', return_value=T0 + timedelta(hours=1)):
            await premium.record_vote('1234', 100, T0 + timedelta(hours=1))
        self.assertEqual(await self.database.premium_votes.count_documents({}), 1)
        result = await premium.claim_vote(100, 200)
        self.assertEqual(utc(result['subscription_end']), T0 + timedelta(hours=12))
        with self.assertRaises(premium.PremiumError):
            await premium.claim_vote(100, 201)

    async def test_expired_vote_cannot_be_claimed(self):
        await premium.record_vote('1234', 100, T0)
        with patch('kuroo.workflows.premium.now', return_value=T0 + timedelta(hours=12)):
            with self.assertRaises(premium.PremiumError):
                await premium.claim_vote(100, 200)

    async def test_nonexpiring_legacy_codes_keep_original_workflow(self):
        code = await storage.redeem_codes.insert(code='LEGACY', code_type='subscription',
                                                code_value='user_no_prefix')
        with patch('kuroo.workflows.actions.change_user_subscription', new=AsyncMock()) as change:
            await premium.redeem(code['code'], 100, 200, bot=object())
        change.assert_awaited_once_with(unittest.mock.ANY, 100, 'user_no_prefix', None)

    async def test_legacy_server_codes_retain_existing_access_and_duration(self):
        code = await storage.redeem_codes.insert(code='LEGACY-GUILD', code_type='subscription',
                                                code_value='golden_guild_premium', valid_for_days=30)
        await storage.guilds.insert(guild_id=200, subscription='free')
        with patch('kuroo.workflows.actions.change_guild_subscription', new=AsyncMock()) as change:
            await premium.redeem(code['code'], 100, 200, bot=object())
        change.assert_awaited_once_with(unittest.mock.ANY, 200, 'golden_guild_premium', 30)

    async def test_noprefix_preference_survives_storage_reload(self):
        user = await storage.users.insert(user_id=100, type='admin')
        await storage.users.update(id=user['id'], admin_no_prefix=False)
        await self.cache_module.users_cache.initialize()
        self.assertFalse(cache.users['100']['admin_no_prefix'])
        self.assertIn(100, cache.admins)
        await storage.users.update(id=user['id'], type='user')
        self.assertNotIn(100, cache.admins)


class TopggWebhookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patches = [patch.object(BotConfigClass, 'TOPGG_WEBHOOK_SECRET', 'synthetic-webhook-secret'),
                        patch.object(BotConfigClass, 'DISCORD_CLIENT_ID', '42'),
                        patch('kuroo.surface.routes.topgg.get_bot', return_value=None)]
        for item in self.patches:
            item.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
        self.event = {'type': 'vote.create', 'data': {
            'id': '1234', 'created_at': datetime.now(timezone.utc).isoformat(),
            'project': {'type': 'bot', 'platform': 'discord', 'platform_id': '42'},
            'user': {'platform_id': '100'},
        }}

    async def asyncTearDown(self):
        await self.client.aclose()
        for item in reversed(self.patches):
            item.stop()

    async def post(self, event=None, stamp=None, signature=None):
        raw = json.dumps(event or self.event).encode()
        stamp = str(stamp or int(datetime.now(timezone.utc).timestamp()))
        digest = hmac.new(b'synthetic-webhook-secret', stamp.encode() + b'.' + raw, hashlib.sha256).hexdigest()
        return await self.client.post('/webhooks/topgg', content=raw,
                                      headers={'x-topgg-signature': signature or f't={stamp},v1={digest}'})

    async def test_valid_signature_records_discord_identity(self):
        with patch('kuroo.surface.routes.topgg.premium.record_vote', new=AsyncMock()) as record:
            response = await self.post()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(record.await_args.args[:2], ('1234', 100))

    async def test_invalid_or_old_signature_cannot_issue_premium(self):
        with patch('kuroo.surface.routes.topgg.premium.record_vote', new=AsyncMock()) as record:
            self.assertEqual((await self.post(signature='t=1,v1=invalid')).status_code, 401)
            self.assertEqual((await self.post(stamp=1)).status_code, 401)
            record.assert_not_awaited()

    async def test_other_bot_and_test_events_cannot_issue_premium(self):
        with patch('kuroo.surface.routes.topgg.premium.record_vote', new=AsyncMock()) as record:
            self.event['data']['project']['platform_id'] = '43'
            self.assertEqual((await self.post()).status_code, 400)
            self.event['data']['project']['platform_id'] = '42'
            self.event['type'] = 'webhook.test'
            self.assertEqual((await self.post()).status_code, 200)
            record.assert_not_awaited()

    async def test_malformed_payload_and_disabled_webhook(self):
        self.assertEqual((await self.post({'type': 'vote.create', 'data': []})).status_code, 400)
        with patch.object(BotConfigClass, 'TOPGG_WEBHOOK_SECRET', ''):
            self.assertEqual((await self.post()).status_code, 503)

    async def test_old_source_token_generator_is_disabled(self):
        self.assertEqual((await self.client.post('/redeem/generate/')).status_code, 410)


if __name__ == '__main__':
    unittest.main()
