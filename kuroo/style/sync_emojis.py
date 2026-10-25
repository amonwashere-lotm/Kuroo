"""Resolve source icons to this application's emojis before loading cogs.

Uses discord.py's authenticated API/rate-limit handling. IDs are applied to the
live module, without editing source files or blocking the event loop.
"""
import re

import aiohttp
import discord

from kuroo.console.logging import logger
from kuroo.style import emoji

CUSTOM = re.compile(r'<(a?):(\w+):(\d+)>')
_resolved = {}


def apply_synced_emojis():
    for attribute, value in vars(emoji).copy().items():
        if isinstance(value, str):
            setattr(emoji, attribute, CUSTOM.sub(lambda match: _resolved.get(match.group(0), match.group(0)), value))


async def sync_application_emojis(bot):
    templates = {match.group(0): match.groups() for value in vars(emoji).values()
                 if isinstance(value, str) for match in CUSTOM.finditer(value)}
    try:
        existing = await bot.fetch_application_emojis()
    except (discord.HTTPException, aiohttp.ClientError, TimeoutError):
        logger.warning('Emoji sync could not read application emojis; keeping configured icons.')
        return
    by_id = {str(item.id): item for item in existing}
    by_name = {item.name: item for item in existing}
    uploaded, failed = 0, 0
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as session:
        for original, (animated, name, source_id) in sorted(templates.items()):
            resolved = by_id.get(source_id) or by_name.get(name)
            if resolved is None:
                extension = 'gif' if animated else 'png'
                try:
                    async with session.get(f'https://cdn.discordapp.com/emojis/{source_id}.{extension}') as response:
                        response.raise_for_status()
                        data = bytearray()
                        async for chunk in response.content.iter_chunked(16 * 1024):
                            data.extend(chunk)
                            if len(data) > 256 * 1024:
                                raise ValueError('Emoji image exceeds Discord limit')
                        raw = bytes(data)
                        if not raw:
                            raise ValueError('Empty emoji image')
                    resolved = await bot.create_application_emoji(name=name, image=raw)
                    by_name[name] = resolved
                    by_id[str(resolved.id)] = resolved
                    uploaded += 1
                except (aiohttp.ClientError, TimeoutError, discord.HTTPException, ValueError):
                    failed += 1
                    # An unavailable source icon must not break buttons or help parsing.
                    _resolved[original] = '✦'
                    continue
            _resolved[original] = str(resolved)
    apply_synced_emojis()
    logger.info(f'Application emoji sync: {len(templates) - failed} resolved, {uploaded} uploaded, {failed} unavailable.')
