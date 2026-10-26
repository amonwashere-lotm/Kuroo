"""Components V2 rendering for every Discord message transport.

Command code may still use Embed as an in-memory document. Only component
payloads cross the network. Keep discord.py pinned: the two serializer hooks
below are internal APIs, covered by transport integration tests.
"""
from collections import OrderedDict
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
from io import BytesIO
import json
import sys

import discord
import discord.http
import discord.interactions
import discord.webhook.async_
import discord.webhook.sync

V2_FLAG = 1 << 15
CONTENT_ID = 9000001
EMBEDS_ID = 9000002
_previous = ContextVar("kuroo_previous_message", default=None)
_installed = False
_rendered = ContextVar("kuroo_rendered_edit", default=None)
_recent_edits = OrderedDict()



def _text(value):
    return {"type": 10, "content": str(value)}


def _container(items, component_id=None):
    result = {"type": 17, "components": items}
    if component_id is not None:
        result["id"] = component_id
    return result


def _embed_components(embed):
    """Preserve document text, links and media; deliberately omit color."""
    parts = []
    author = embed.get("author", {})
    if author.get("name"):
        name = author["name"]
        parts.append(f"[{name}]({author['url']})" if author.get("url") else f"**{name}**")
    if embed.get("title"):
        title = embed["title"]
        parts.append(f"## [{title}]({embed['url']})" if embed.get("url") else f"## {title}")
    if embed.get("description"):
        parts.append(embed["description"])
    for field in embed.get("fields", []):
        parts.append(f"**{field['name']}**\n{field['value']}")
    footer = embed.get("footer", {}).get("text")
    if footer:
        parts.append(f"-# {footer}")
    if embed.get("timestamp"):
        parts.append(f"-# {embed['timestamp']}")
    items = [_text("\n\n".join(parts))] if parts else []
    thumbnail = embed.get("thumbnail", {}).get("url")
    if thumbnail:
        # Sections require 1–3 text displays and one accessory.
        items = [{"type": 9, "components": items or [_text("\u200b")],
                  "accessory": {"type": 11, "media": {"url": thumbnail}}}]
    image = embed.get("image", {}).get("url")
    if image:
        items.append({"type": 12, "items": [{"media": {"url": image}}]})
    return items


def _walk(items):
    for item in items:
        yield item
        yield from _walk(item.get("components", []))
        if "accessory" in item:
            yield item["accessory"]


def _remove_accents(items):
    for item in _walk(items):
        item.pop("accent_color", None)


def _without_controls(items):
    """Keep native visual content when a partial edit removes/replaces controls."""
    kept = []
    for item in items:
        if item['type'] in (1, 2, 3, 5, 6, 7, 8):
            continue
        if item['type'] == 9 and item.get('accessory', {}).get('type') == 2:
            kept.extend(_without_controls(item['components']))
            continue
        if 'components' in item:
            item['components'] = _without_controls(item['components'])
            if not item['components']:
                continue
        kept.append(item)
    return kept


def _snapshot(message):
    if message is None:
        return None
    if isinstance(message, dict):
        return deepcopy(message)
    return {
        "_key": (message.channel.id, message.id),
        "content": message.content,
        "embeds": [embed.to_dict() for embed in message.embeds],
        "components": [component.to_dict() for component in message.components],
        "attachments": [attachment.to_dict() for attachment in message.attachments],
        "flags": message.flags.value,
    }


def _is_native(items):
    return any(item["type"] != 1 for item in items)


def message_payload(payload, previous=None, *, overflow_files=None):
    """Render a message or edit, preserving omitted fields on partial edits.

    Reserved container IDs distinguish text and embed documents after Discord
    returns a message. Legacy View instances remain registered for callbacks;
    their action rows are valid inside a V2 message without moving UI objects.
    """
    result = deepcopy(payload)
    previous = previous or {}
    old_items = deepcopy(previous.get("components", []))
    supplied = result.get("components", [])
    native_view = _is_native(supplied)
    if native_view:
        items = supplied
    else:
        # Body containers stay intact when an old View changes only its controls.
        items = _without_controls(old_items) if "components" in result else [item for item in old_items if item["type"] != 1]
        if "content" in result or (not old_items and previous.get("content")):
            items = [item for item in items if item.get("id") != CONTENT_ID]
            content = result.get("content", previous.get("content"))
            if content:
                items.insert(0, _container([_text(content)], CONTENT_ID))
        if "embeds" in result or (not old_items and previous.get("embeds")):
            items = [item for item in items if item.get("id") != EMBEDS_ID]
            embeds = result.get("embeds", previous.get("embeds", []))
            docs = [item for embed in embeds for item in _embed_components(embed)]
            if docs:
                items.append(_container(docs, EMBEDS_ID))
        controls = supplied if "components" in result else [i for i in old_items if i["type"] == 1]
        items.extend(controls)
    # Explicit text alongside a native layout is also rendered, never discarded.
    if native_view:
        if result.get("content"):
            items.insert(0, _container([_text(result["content"])], CONTENT_ID))
        docs = [item for embed in result.get("embeds", []) for item in _embed_components(embed)]
        if docs:
            items.append(_container(docs, EMBEDS_ID))
    _remove_accents(items)

    # Files are hidden by Discord in V2 unless referenced by a media/file component.
    referenced = {item.get("media", {}).get("url") for item in _walk(items)}
    referenced.update(item.get("file", {}).get("url") for item in _walk(items))
    referenced.update(entry.get("media", {}).get("url")
                      for item in _walk(items) for entry in item.get("items", []))
    for attachment in result.get("attachments", []):
        url = f"attachment://{attachment['filename']}"
        if url not in referenced:
            items.append({"type": 13, "file": {"url": url}})
    # A removed attachment must not leave a dangling File component on an edit.
    if "attachments" in result:
        names = {f"attachment://{a['filename']}" for a in result['attachments']}
        items = [i for i in items if i['type'] != 13 or i['file']['url'] in names]

    displays = [item for item in _walk(items) if item['type'] == 10]
    total = sum(len(item['content']) for item in displays)
    if total > 4000 or sum(1 for _ in _walk(items)) > 40:
        # Keep long reports accessible without sending an invalid Discord payload.
        full_text = '\n\n'.join(item['content'] for item in displays)
        if overflow_files is None:
            raise ValueError("Message exceeds Components V2 limits; use the file-capable transport")
        overflow_files.append(discord.File(BytesIO(full_text.encode()), filename='kuroo-details.txt'))
        controls = [i for i in _walk(items) if i['type'] == 1]
        visible_files = [i for i in items if i['type'] == 13
                         and i['file']['url'] != 'attachment://kuroo-details.txt']
        groups = {}
        for item in items:
            text = '\n\n'.join(i['content'] for i in _walk([item]) if i['type'] == 10)
            if text:
                key = item.get('id') if item.get('id') in (CONTENT_ID, EMBEDS_ID) else None
                groups.setdefault(key, []).append(text)
        # Keep document IDs so later edits can replace each preview normally.
        allowance = 2800 // max(len(groups), 1)
        previews = [_container([_text('\n\n'.join(text)[:allowance] +
                    '\n\nFull message attached: kuroo-details.txt')], key)
                    for key, text in groups.items()]
        nested_visuals = [media for item in items if item['type'] == 17
                          for media in _walk(item.get('components', [])) if media['type'] == 12]
        if nested_visuals:
            if not previews:
                previews = [_container([])]
            previews[0]['components'] = nested_visuals + previews[0]['components']
        # Preserve top-level media for callers that intentionally place it there.
        visuals = [item for item in items if item['type'] == 12]
        items = visuals + previews + controls + visible_files
        items.append({'type': 13, 'file': {'url': 'attachment://kuroo-details.txt'}})
    if not items:
        items = [_container([_text('\u200b')])]
    result['components'] = items
    result['flags'] = result.get('flags', previous.get('flags', 0)) | V2_FLAG
    result.pop('content', None)
    result.pop('embeds', None)
    result.pop('tts', None)
    if result.get('poll') or result.get('sticker_ids'):
        raise ValueError('Components V2 does not support polls or stickers')
    result.pop('poll', None)
    result.pop('sticker_ids', None)
    # Existing pre-migration messages must clear the old body on conversion.
    if previous.get('content') or previous.get('embeds'):
        result['content'] = None
        result['embeds'] = []
    return result


def _convert_parameters(params):
    multipart = list(params.multipart)
    payload = params.payload
    if payload is None:
        payload = json.loads(next(p['value'] for p in multipart if p['name'] == 'payload_json'))
    extra_files = []
    if 'type' in payload:
        # Only message responses/updates, never modals, defers or autocomplete.
        if payload['type'] not in (4, 7):
            return params
        payload['data'] = message_payload(payload['data'], _previous.get(), overflow_files=extra_files)
        body = payload['data']
    elif 'message' in payload:
        payload['message'] = message_payload(payload['message'], _previous.get(), overflow_files=extra_files)
        body = payload['message']
    else:
        payload = message_payload(payload, _previous.get(), overflow_files=extra_files)
        body = payload
    files = list(params.files or [])
    for file in extra_files:
        index = len(files)
        body.setdefault('attachments', []).append(file.to_dict(index))
        multipart.append({'name': f'files[{index}]', 'value': file.fp,
                          'filename': file.filename, 'content_type': 'application/octet-stream'})
        files.append(file)
    captured = _rendered.get()
    if captured is not None:
        captured.update(body)
    if multipart:
        multipart = [part for part in multipart if part['name'] != 'payload_json']
        multipart.insert(0, {'name': 'payload_json', 'value': json.dumps(payload)})
        payload = None
    return discord.http.MultipartParameters(payload=payload, multipart=multipart, files=files)


def _serializer(original):
    @wraps(original)
    def wrapped(*args, **kwargs):
        return _convert_parameters(original(*args, **kwargs))
    return wrapped


def _edit_context(original, get_message):
    @wraps(original)
    async def wrapped(self, *args, **kwargs):
        message = await get_message(self, *args, **kwargs)
        previous = _snapshot(message)
        token = _previous.set(previous)
        captured = {}
        rendered_token = _rendered.set(captured)
        try:
            result = await original(self, *args, **kwargs)
            # Callers often discard Message.edit's returned object. Remember the
            # successful body so a later view-only timeout cannot restore old text.
            if previous and captured and '_key' in previous:
                latest = dict(previous, **captured)
                latest.update(content='', embeds=[])
                key = previous['_key']
                _recent_edits[key] = latest
                _recent_edits.move_to_end(key)
                if len(_recent_edits) > 256:
                    _recent_edits.popitem(last=False)
            if isinstance(self, discord.Interaction) and isinstance(result, discord.Message):
                self._original_response = result
            return result
        finally:
            _rendered.reset(rendered_token)
            _previous.reset(token)
    return wrapped


async def _message(self, *args, **kwargs):
    key = (self.channel.id, self.id)
    if key in _recent_edits:
        return _recent_edits[key]
    # Once an entry is evicted, refresh the message instead of trusting an old
    # object. This also covers edits made before this process was restarted.
    return await self.channel.fetch_message(self.id)



async def _partial_message(self, *args, **kwargs):
    return await self.fetch()


async def _response_message(self, *args, **kwargs):
    return self._parent.message


async def _original_message(self, *args, **kwargs):
    try:
        return await self.original_response()
    except discord.NotFound:
        return None  # Deferred response has no message body yet.


async def _webhook_message(self, message_id, *args, **kwargs):
    return await self.fetch_message(message_id, thread=kwargs.get('thread', discord.utils.MISSING))


def _webhook_components(original):
    @wraps(original)
    def wrapped(*args, **kwargs):
        kwargs['with_components'] = True
        return original(*args, **kwargs)
    return wrapped


def install():
    """Install once, before any cogs or transports are loaded."""
    global _installed
    if _installed:
        return
    if discord.__version__ != '2.6.4':
        raise RuntimeError('Kuroo requires discord.py==2.6.4; install requirements.txt')
    # discord.py imports these helpers by value in several transport modules.
    for name in ('handle_message_parameters', 'interaction_message_response_params'):
        originals = {getattr(module, name) for module in tuple(sys.modules.values())
                     if module and getattr(module, '__name__', '').startswith('discord.')
                     and hasattr(module, name)}
        replacements = {original: _serializer(original) for original in originals}
        for module in tuple(sys.modules.values()):
            if module and getattr(module, '__name__', '').startswith('discord.'):
                original = getattr(module, name, None)
                if original in replacements:
                    setattr(module, name, replacements[original])
    for cls, method, getter in (
        (discord.Message, 'edit', _message),
        (discord.PartialMessage, 'edit', _partial_message),
        (discord.InteractionResponse, 'edit_message', _response_message),
        (discord.Interaction, 'edit_original_response', _original_message),
        (discord.Webhook, 'edit_message', _webhook_message),
    ):
        setattr(cls, method, _edit_context(getattr(cls, method), getter))
    for cls in (discord.webhook.async_.AsyncWebhookAdapter, discord.webhook.sync.WebhookAdapter):
        cls.execute_webhook = _webhook_components(cls.execute_webhook)
    _installed = True
