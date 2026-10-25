import asyncio
from io import BytesIO
from contextlib import closing
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

import aiohttp
import discord
from discord.ext import commands
import discord.http
import discord.interactions
import discord.webhook.async_

from kuroo.workflows.components import (
    CONTENT_ID, EMBEDS_ID, V2_FLAG, _previous, _recent_edits, _walk, install, message_payload,
)


def body(params):
    return params.payload or json.loads(params.multipart[0]['value'])


def server_message(payload):
    payload = dict(payload)
    payload['attachments'] = [dict(a, size=10, url='https://example.com/file', proxy_url='https://example.com/file') for a in payload.get('attachments', [])]
    result = dict(id='456', channel_id='123', author={'id': '42', 'username': 'Kuroo',
                'discriminator': '0', 'avatar': None, 'bot': True},
                content='', embeds=[], attachments=[], timestamp='2026-10-25T00:00:00+00:00',
                edited_timestamp=None, type=0)
    result.update(payload)
    return result


class RendererTests(unittest.TestCase):
    def assert_v2(self, payload):
        self.assertTrue(payload['flags'] & V2_FLAG)
        self.assertNotIn('content', payload)
        self.assertNotIn('embeds', payload)
        self.assertNotIn('accent_color', json.dumps(payload))

    def test_text_embed_and_media_have_no_accent(self):
        embed = discord.Embed(title='Log', description='Description', color=0xff0000,
                              url='https://example.com')
        embed.add_field(name='User', value='Name')
        embed.set_author(name='Author', url='https://example.com/author')
        embed.set_footer(text='Footer')
        embed.set_thumbnail(url='https://example.com/thumb.png')
        embed.set_image(url='attachment://image.png')
        payload = message_payload({'content': 'Mention', 'embeds': [embed.to_dict()]})
        self.assert_v2(payload)
        text = json.dumps(payload)
        for value in ('Log', 'Description', 'User', 'Name', 'Footer', 'Author', 'image.png', 'thumb.png'):
            self.assertIn(value, text)

    def test_partial_controls_edit_retains_text_and_embed(self):
        previous = message_payload({'content': 'Hello', 'embeds': [{'description': 'Report'}],
                                    'components': [{'type': 1, 'components': [{'type': 2, 'custom_id': 'next'}]}]})
        payload = message_payload({'components': []}, previous)
        self.assert_v2(payload)
        self.assertIn('Hello', json.dumps(payload))
        self.assertIn('Report', json.dumps(payload))
        self.assertNotIn('next', json.dumps(payload))

    def test_embed_edit_preserves_content_and_controls(self):
        previous = message_payload({'content': 'Ping', 'embeds': [{'description': 'Old'}],
                                    'components': [{'type': 1, 'components': [{'type': 2, 'custom_id': 'next'}]}]})
        payload = message_payload({'embeds': [{'description': 'New'}]}, previous)
        self.assertIn('Ping', json.dumps(payload))
        self.assertIn('next', json.dumps(payload))
        self.assertIn('New', json.dumps(payload))
        self.assertNotIn('Old', json.dumps(payload))

    def test_clear_document_preserves_other_document(self):
        previous = message_payload({'content': 'Hello', 'embeds': [{'description': 'Report'}]})
        payload = message_payload({'content': None}, previous)
        self.assertNotIn('Hello', json.dumps(payload))
        self.assertIn('Report', json.dumps(payload))

    def test_native_layout_replaces_old_layout_and_removes_nested_accents(self):
        previous = message_payload({'content': 'Old'})
        payload = message_payload({'components': [{'type': 17, 'accent_color': 123,
                                   'components': [{'type': 10, 'content': 'New'}]}]}, previous)
        self.assert_v2(payload)
        self.assertNotIn('Old', json.dumps(payload))

    def test_remove_native_controls_retains_visual_content(self):
        previous = message_payload({'components': [{'type': 17, 'components': [
            {'type': 10, 'content': 'Help'},
            {'type': 1, 'components': [{'type': 2, 'custom_id': 'next'}]},
            {'type': 9, 'components': [{'type': 10, 'content': 'Section'}],
             'accessory': {'type': 2, 'custom_id': 'accessory'}},
        ]}]})
        payload = message_payload({'components': []}, previous)
        self.assertIn('Help', json.dumps(payload))
        self.assertIn('Section', json.dumps(payload))
        self.assertNotIn('custom_id', json.dumps(payload))

    def test_native_layout_with_content_and_embed_keeps_all_documents(self):
        payload = message_payload({'content': 'Hello', 'embeds': [{'title': 'Report'}],
                                   'components': [{'type': 17, 'components': [{'type': 10, 'content': 'Native'}]}]})
        for value in ('Hello', 'Report', 'Native'):
            self.assertIn(value, json.dumps(payload))

    def test_legacy_message_conversion_clears_old_wire_fields(self):
        payload = message_payload({'components': []}, {'content': 'Old text', 'embeds': [{'title': 'Old embed'}]})
        self.assertIsNone(payload['content'])
        self.assertEqual(payload['embeds'], [])
        self.assertIn('Old text', json.dumps(payload['components']))
        self.assertIn('Old embed', json.dumps(payload['components']))

    def test_ephemeral_and_suppress_flags_are_preserved(self):
        payload = message_payload({'content': 'Private', 'flags': 64 | 4})
        self.assertEqual(payload['flags'], V2_FLAG | 64 | 4)

    def test_file_and_media_are_visible(self):
        payload = message_payload({'attachments': [{'id': 0, 'filename': 'report.html'}],
                                   'embeds': [{'image': {'url': 'attachment://image.png'}}]})
        self.assertIn('attachment://report.html', json.dumps(payload))
        self.assertEqual(json.dumps(payload).count('attachment://image.png'), 1)

    def test_removed_file_is_not_referenced(self):
        previous = message_payload({'attachments': [{'id': 0, 'filename': 'report.html'}]})
        payload = message_payload({'attachments': []}, previous)
        self.assertNotIn('report.html', json.dumps(payload))

    def test_invalid_v2_features_fail_explicitly(self):
        with self.assertRaises(ValueError):
            message_payload({'poll': {'question': 'Test'}})

    def test_input_data_is_not_mutated(self):
        original = {'embeds': [{'description': 'Hello', 'color': 123}]}
        message_payload(original)
        self.assertEqual(original['embeds'][0]['color'], 123)


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = discord.Client(intents=discord.Intents.none())
        await self.client._async_setup_hook()
        self.channel = self.client.get_partial_messageable(123)
        self.sent = []
        _recent_edits.clear()

        async def send(channel_id, *, params):
            payload = body(params)
            self.sent.append(payload)
            return server_message(payload)

        async def edit(channel_id, message_id, *, params):
            payload = body(params)
            self.sent.append(payload)
            return server_message(payload)

        self.client.http.get_message = AsyncMock(side_effect=lambda *args: server_message(self.sent[-1]))
        self.client.http.send_message = AsyncMock(side_effect=send)
        self.client.http.edit_message = AsyncMock(side_effect=edit)

    async def asyncTearDown(self):
        await self.client.close()

    async def test_channel_send_reply_and_partial_edit(self):
        view = discord.ui.View(timeout=None)
        button = discord.ui.Button(label='Next', custom_id='next')
        view.add_item(button)
        message = await self.channel.send('Hello', embed=discord.Embed(description='Report'), view=view)
        self.assertTrue(message.flags.components_v2)
        self.assertEqual(message.content, '')
        self.assertEqual(message.embeds, [])
        self.assertIs(self.client._connection._view_store._views[message.id][(2, 'next')], button)
        # Round-trip through Discord's own component parser before editing.
        discord.ui.LayoutView.from_message(message)
        button.disabled = True
        message = await message.edit(view=view)
        self.assertIn('Hello', json.dumps(self.sent[-1]))
        self.assertIn('Report', json.dumps(self.sent[-1]))
        self.assertTrue(self.sent[-1]['components'][-1]['components'][0]['disabled'])
        await message.reply('Reply')
        self.assertTrue(self.sent[-1]['flags'] & V2_FLAG)
        self.assertIn('message_reference', self.sent[-1])
        self.assertIsNone(_previous.get())

    async def test_timeout_on_old_message_keeps_latest_page(self):
        message = await self.channel.send(embed=discord.Embed(description='Page one'))
        await message.edit(embed=discord.Embed(description='Page two'))
        await message.edit(view=None)
        self.assertIn('Page two', json.dumps(self.sent[-1]))
        self.assertNotIn('Page one', json.dumps(self.sent[-1]))

    async def test_file_send_produces_v2_multipart_and_reference(self):
        with closing(discord.File(BytesIO(b'transcript'), filename='transcript.html')) as file:
            message = await self.channel.send('Transcript', file=file)
        self.assertIn('attachment://transcript.html', json.dumps(self.sent[-1]))
        self.assertTrue(message.flags.components_v2)

    async def test_long_document_is_attached_without_losing_text(self):
        text = 'Report line\n' * 600
        with discord.http.handle_message_parameters(embed=discord.Embed(description=text)) as params:
            payload = body(params)
            self.assertTrue(payload['flags'] & V2_FLAG)
            self.assertLessEqual(sum(len(i['content']) for i in _walk(payload['components']) if i['type'] == 10), 4000)
            self.assertEqual(params.files[0].fp.read().decode(), text)
            self.assertIn('attachment://kuroo-details.txt', json.dumps(payload))

    async def test_long_report_can_be_replaced_by_a_short_page(self):
        message = await self.channel.send(embed=discord.Embed(description='Old page ' * 600))
        await message.edit(embed=discord.Embed(description='New page'))
        self.assertNotIn('Old page', json.dumps(self.sent[-1]['components']))
        self.assertIn('New page', json.dumps(self.sent[-1]['components']))

    async def test_overflow_preserves_existing_upload(self):
        with closing(discord.File(BytesIO(b'data'), filename='data.csv')) as file:
            with discord.http.handle_message_parameters('x' * 5000, file=file) as params:
                payload = body(params)
                self.assertEqual(len(params.files), 2)
                self.assertIn('attachment://data.csv', json.dumps(payload))
                self.assertEqual([a['id'] for a in payload['attachments']], [0, 1])
                self.assertEqual([p['name'] for p in params.multipart], ['payload_json', 'files[0]', 'files[1]'])

    async def test_webhook_send_opts_into_components(self):
        adapter = discord.webhook.async_.AsyncWebhookAdapter()
        adapter.request = AsyncMock(return_value=server_message(message_payload({'content': 'Webhook'})))
        token = discord.webhook.async_.async_context.set(adapter)
        try:
            async with aiohttp.ClientSession() as session:
                webhook = discord.Webhook.partial(123, 'synthetic-token', session=session, client=self.client)
                await webhook.send('Webhook', wait=True)
            kwargs = adapter.request.call_args.kwargs
            self.assertEqual(kwargs['params']['with_components'], 1)
            self.assertTrue(kwargs['payload']['flags'] & V2_FLAG)
            self.assertNotIn('content', kwargs['payload'])
        finally:
            discord.webhook.async_.async_context.reset(token)

    async def test_interaction_and_webhook_serializers(self):
        params = discord.interactions.interaction_message_response_params(
            type=4, content='Private response', flags=discord.MessageFlags(ephemeral=True))
        self.assertEqual(body(params)['data']['flags'], V2_FLAG | 64)
        self.assertNotIn('content', body(params)['data'])
        params = discord.webhook.async_.handle_message_parameters('Follow-up', embed=discord.Embed(title='Result'))
        self.assertTrue(body(params)['flags'] & V2_FLAG)
        self.assertNotIn('embeds', body(params))

    async def test_interaction_edit_keeps_original_documents(self):
        token = _previous.set(message_payload({'content': 'Page', 'embeds': [{'title': 'Report'}]}))
        try:
            params = discord.interactions.interaction_message_response_params(type=7, view=None)
            self.assertIn('Page', json.dumps(body(params)))
            self.assertIn('Report', json.dumps(body(params)))
        finally:
            _previous.reset(token)

    async def test_modal_and_defer_are_untouched(self):
        for response_type in (5, 6, 8, 9):
            params = discord.interactions.interaction_message_response_params(type=response_type)
            self.assertNotIn('flags', body(params)['data'])
            self.assertNotIn('components', body(params)['data'])

    async def test_native_view_callbacks_remain_registered(self):
        view = discord.ui.LayoutView(timeout=None)
        button = discord.ui.Button(label='Next', custom_id='native-next')
        container = discord.ui.Container(discord.ui.TextDisplay('Help'), discord.ui.ActionRow(button), accent_color=123)
        view.add_item(container)
        message = await self.channel.send(view=view)
        self.assertNotIn('accent_color', json.dumps(self.sent[-1]))
        self.assertIs(self.client._connection._view_store._views[message.id][(2, 'native-next')], button)

    async def test_install_is_idempotent(self):
        original = discord.http.handle_message_parameters
        install()
        self.assertIs(original, discord.http.handle_message_parameters)


if __name__ == '__main__':
    unittest.main()
