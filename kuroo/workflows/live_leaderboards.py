"""Update one durable Components V2 Pillow message per guild and period."""
import asyncio
from collections import defaultdict
from contextlib import closing
from io import BytesIO

import discord

from kuroo.console.logging import logger
from kuroo.workflows import cards, community, leveling


class LiveLeaderboards:
    def __init__(self, bot):
        self.bot = bot
        self.locks = defaultdict(asyncio.Lock)
        self.signatures = {}

    @staticmethod
    def check_channel(guild, channel):
        if channel is None or channel.guild.id != guild.id or guild.me is None:
            raise community.CommunityError('Choose an available text channel in this server.')
        permissions = channel.permissions_for(guild.me)
        if not all(getattr(permissions, name) for name in ('view_channel', 'send_messages', 'attach_files', 'read_message_history')):
            raise community.CommunityError('I need View Channel, Send Messages, Attach Files and Read Message History in that channel.')

    async def render(self, guild, period, rows):
        rendered, lines = [], []
        for index, row in enumerate(rows):
            user = guild.get_member(row['user_id']) or self.bot.get_user(row['user_id'])
            name = user.display_name if user else f'User {row["user_id"]}'
            rendered.append(dict(row, name=name))
            lines.append(f'{index + 1}. {discord.utils.escape_markdown(name)} — {row["xp"]:,} XP')
        raw = await asyncio.to_thread(cards.leaderboard_card, guild=guild.name, rows=rendered, period=period)
        start = community.period_start(period)
        caption = (f'## {period.title()} XP leaderboard\n'
                   f'Period starts <t:{int(start.timestamp())}:f> · UTC\n'
                   + ('\n'.join(lines) or 'No XP earned this period yet.')
                   + f'\n-# Refreshes every minute · Updated <t:{int(community.now().timestamp())}:R>')
        return raw, caption

    async def write(self, channel, guild, period, rows, message=None):
        raw, caption = await self.render(guild, period, rows)
        view = discord.ui.LayoutView()
        container = discord.ui.Container()
        container.add_item(discord.ui.TextDisplay(caption))
        container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(media='attachment://kuroo-live.png',
                                                                           description=f'{period.title()} earned XP leaderboard')))
        view.add_item(container)
        try:
            with closing(discord.File(BytesIO(raw), filename='kuroo-live.png')) as file:
                if message is not None:
                    return await message.edit(view=view, attachments=[file], allowed_mentions=discord.AllowedMentions.none())
                return await channel.send(view=view, file=file, allowed_mentions=discord.AllowedMentions.none())
        finally:
            view.stop()

    async def delete_message(self, config):
        if not config:
            return
        guild = self.bot.get_guild(config['guild_id'])
        channel = guild.get_channel(config['channel_id']) if guild else None
        if channel:
            try:
                message = await channel.fetch_message(config['message_id'])
                if message.author.id == self.bot.user.id:
                    await message.delete()
            except discord.HTTPException:
                pass

    async def configure(self, guild, channel, period):
        async with self.locks[(guild.id, period)]:
            await leveling.require_period(guild.id, period)
            self.check_channel(guild, channel)
            previous = await leveling.board(guild.id, period)
            rows = await community.period_leaderboard(guild.id, period)
            message = await self.write(channel, guild, period, rows)
            try:
                await leveling.save_board(guild.id, period, channel.id, message.id)
            except Exception:
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
                raise
            self.signatures.pop((guild.id, period), None)
            await self.delete_message(previous)
            return message

    async def remove(self, guild_id, period):
        async with self.locks[(guild_id, period)]:
            previous = await leveling.remove_board(guild_id, period)
            self.signatures.pop((guild_id, period), None)
            await self.delete_message(previous)

    async def refresh(self, guild_id, period):
        async with self.locks[(guild_id, period)]:
            config = await leveling.board(guild_id, period)
            if config is None:
                return
            await leveling.require_period(guild_id, period)
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                return
            channel = guild.get_channel(config['channel_id'])
            self.check_channel(guild, channel)
            rows = await community.period_leaderboard(guild_id, period)
            # Fetch even when scores are unchanged, to recover deleted messages.
            try:
                message = await channel.fetch_message(config['message_id'])
                if message.author.id != self.bot.user.id:
                    message = None
            except discord.NotFound:
                message = None
            signature = (community.period_start(period), guild.name,
                         tuple((row['user_id'], row['xp'], row['level'],
                                getattr(guild.get_member(row['user_id']) or self.bot.get_user(row['user_id']), 'display_name', ''))
                               for row in rows))
            if message is not None and self.signatures.get((guild_id, period)) == signature:
                return
            try:
                updated = await self.write(channel, guild, period, rows, message)
            except discord.NotFound:
                # A moderator may delete the post after our fetch.
                updated = await self.write(channel, guild, period, rows)
            if message is None or updated.id != config['message_id']:
                await leveling.save_board(guild_id, period, channel.id, updated.id)
            self.signatures[(guild_id, period)] = signature

    async def refresh_all(self):
        async for config in leveling.boards():
            try:
                await self.refresh(config['guild_id'], config['period'])
            except community.CommunityError:
                # Expired premium and missing channel permissions pause only this board.
                continue
            except Exception as error:
                logger.warning(f'Live leaderboard refresh failed ({type(error).__name__}); it will retry next minute.')
