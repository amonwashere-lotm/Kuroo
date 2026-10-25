"""Leveling, economy and membership cards, delivered through Components V2."""
import asyncio
from contextlib import closing
from io import BytesIO
from types import SimpleNamespace
from typing import Literal

import discord
from discord.ext import commands

import storage.welcomer_settings
from kuroo.console.logging import logger
from kuroo.src.checks import checks
from kuroo.workflows import cards, community


def manager():
    async def predicate(ctx):
        return checks.check_is_admin_predicate(ctx.author) or (ctx.guild is not None and ctx.author.guild_permissions.manage_guild)
    return commands.check(predicate)


async def send_card(destination, data, filename, caption):
    view = discord.ui.LayoutView()
    view.add_item(discord.ui.TextDisplay(caption))
    view.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(media=f'attachment://{filename}', description=caption[:1024])))
    with closing(discord.File(BytesIO(data), filename=filename)) as file:
        await destination.send(view=view, file=file, allowed_mentions=discord.AllowedMentions.none())


class Community(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cog_info = SimpleNamespace(name='Community', category='Extra', description='Levels, rank cards, coins and member cards',
                                       hidden=False, emoji='🌙')
        self.xp_cooldowns = commands.CooldownMapping.from_cooldown(1, 60, commands.BucketType.member)

    async def cog_check(self, ctx):
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        return checks.check_blacklist_predicate(ctx) and checks.check_ignore_predicate(ctx)

    async def cog_command_error(self, ctx, error):
        original = getattr(error, 'original', error)
        if isinstance(original, community.CommunityError):
            await ctx.send(str(original), ephemeral=True)
        elif isinstance(error, commands.CheckFailure) and not isinstance(error, commands.NoPrivateMessage):
            await ctx.send('Manage Server permission is required, or this command is unavailable here.', ephemeral=True)
        else:
            raise error

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.guild is None or message.author.bot or message.webhook_id or len(message.content.strip()) < 3:
            return
        ctx = await self.bot.get_context(message)
        if ctx.valid or not checks.check_blacklist_predicate(ctx) or not checks.check_ignore_predicate(ctx):
            return
        if self.xp_cooldowns.get_bucket(message).update_rate_limit():
            return
        try:
            settings = await community.settings(message.guild.id)
            if not settings.get('leveling', True):
                return
            result = await community.award_xp(message.guild.id, message.author.id)
            channel = message.guild.get_channel(settings.get('level_channel', 0))
            if result and result['leveled_up'] and channel:
                await channel.send(f'{discord.utils.escape_markdown(message.author.display_name)} reached **level {result["level"]}**!',
                                   allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            logger.error('Community XP update failed; no message content was logged.')

    @commands.hybrid_command(name='rank', help='View your server rank and XP card')
    @commands.cooldown(2, 10, commands.BucketType.user)
    async def rank_command(self, ctx, member: discord.Member = None):
        await ctx.defer()
        member = member or ctx.author
        result = await community.rank(ctx.guild.id, member.id)
        wallet = await community.wallet(member.id)
        title = community.SHOP.get(wallet.get('card_title'), {}).get('name')
        raw = await cards.avatar_data(member)
        data = await asyncio.to_thread(cards.rank_card, name=member.display_name, guild=ctx.guild.name,
                                       avatar_bytes=raw, title=title, **result)
        caption = f'{discord.utils.escape_markdown(member.display_name)} · Level {result["level"]} · {result["xp"]:,} XP'
        await send_card(ctx, data, 'kuroo-rank.png', caption)

    @commands.hybrid_command(name='lb', aliases=['leaderboard'], help='View the server XP or global wallet leaderboard')
    @commands.cooldown(2, 10, commands.BucketType.user)
    async def leaderboard(self, ctx, kind: Literal['levels', 'coins'] = 'levels', page: commands.Range[int, 1, 1000] = 1):
        await ctx.defer()
        rows, pages = await community.leaderboard(ctx.guild.id, page, coins=kind == 'coins')
        if page > pages:
            raise community.CommunityError(f'There are only {pages} pages.')
        rendered = []
        accessible = []
        for row in rows:
            user = ctx.guild.get_member(row['user_id']) or self.bot.get_user(row['user_id'])
            name = user.display_name if user else f'User {row["user_id"]}'
            rendered.append(dict(row, name=name, level=community.level_for_xp(row.get('xp', 0))))
            score = f'{row.get("balance", 0):,.0f} coins' if kind == 'coins' else f'{row["xp"]:,} XP'
            accessible.append(f'{(page-1)*10 + len(accessible)+1}. {discord.utils.escape_markdown(name)} — {score}')
        data = await asyncio.to_thread(cards.leaderboard_card, guild=ctx.guild.name, rows=rendered, page=page, pages=pages, coins=kind == 'coins')
        await send_card(ctx, data, 'kuroo-leaderboard.png', f'## {"Global wallet" if kind == "coins" else "Server XP"} leaderboard · {page}/{pages}\n' + ('\n'.join(accessible) or 'No entries yet.'))

    @commands.hybrid_command(name='leveling', help='Manage Server: turn message XP on or off')
    @manager()
    async def leveling(self, ctx, mode: Literal['on', 'off']):
        await ctx.defer(ephemeral=True)
        await community.configure(ctx.guild.id, leveling=mode == 'on')
        await ctx.send(f'Message XP is now {mode}.', ephemeral=True)

    @commands.hybrid_command(name='levelchannel', help='Manage Server: set the level-up channel; omit it to disable announcements')
    @manager()
    async def levelchannel(self, ctx, channel: discord.TextChannel = None):
        await ctx.defer(ephemeral=True)
        await community.configure(ctx.guild.id, level_channel=channel.id if channel else 0)
        await ctx.send(f'Level-up announcements: {channel.mention if channel else "disabled"}.', ephemeral=True)

    @commands.hybrid_command(name='balance', aliases=['bal', 'wallet'], help='Check global wallet and bank coins')
    async def balance(self, ctx, member: discord.Member = None):
        await ctx.defer()
        member = member or ctx.author
        record = await community.wallet(member.id)
        await ctx.send(f'## {discord.utils.escape_markdown(member.display_name)} · Coins\nWallet: **{record.get("balance", 0):,.0f}**\nBank: **{record.get("bank", 0):,.0f}**')

    @commands.hybrid_command(name='daily', help='Claim 250 coins once every 24 hours')
    async def daily(self, ctx):
        await ctx.defer()
        record, reward = await community.earn(ctx.author.id, 'daily')
        await ctx.send(f'You claimed **{reward:,} coins**. Wallet: **{record["balance"]:,.0f}**.')

    @commands.hybrid_command(name='work', help='Earn 80–160 coins once an hour')
    async def work(self, ctx):
        await ctx.defer()
        record, reward = await community.earn(ctx.author.id, 'work')
        await ctx.send(f'Your shift earned **{reward:,} coins**. Wallet: **{record["balance"]:,.0f}**.')

    @commands.hybrid_command(name='deposit', aliases=['dep'], help='Move a positive whole coin amount into your bank')
    async def deposit(self, ctx, amount: commands.Range[int, 1, community.MAX_AMOUNT]):
        await ctx.defer()
        record = await community.move_bank(ctx.author.id, amount)
        await ctx.send(f'Deposited **{amount:,} coins**. Bank: **{record.get("bank", 0):,.0f}**.')

    @commands.hybrid_command(name='withdraw', aliases=['with'], help='Move a positive whole coin amount out of your bank')
    async def withdraw(self, ctx, amount: commands.Range[int, 1, community.MAX_AMOUNT]):
        await ctx.defer()
        record = await community.move_bank(ctx.author.id, amount, withdraw=True)
        await ctx.send(f'Withdrew **{amount:,} coins**. Wallet: **{record["balance"]:,.0f}**.')

    @commands.hybrid_command(name='shop', help='Browse cosmetic titles for your rank card')
    async def shop(self, ctx):
        lines = [f'`{key}` · **{value["name"]}** · {value["cost"]:,} coins' for key, value in community.SHOP.items()]
        await ctx.send('## Rank card titles\n' + '\n'.join(lines) + '\n\nUse `/buy item` then `/equip item`. Titles are cosmetic.')

    @commands.hybrid_command(name='buy', help='Buy a cosmetic rank card title using wallet coins')
    async def buy(self, ctx, item: Literal['night-owl', 'aurora', 'after-hours']):
        await ctx.defer()
        record = await community.buy(ctx.author.id, item)
        await ctx.send(f'You now own **{community.SHOP[item]["name"]}**. Use `/equip {item}`. Wallet: **{record["balance"]:,.0f}**.')

    @commands.hybrid_command(name='inventory', aliases=['inv'], help='View the rank card titles you own')
    async def inventory(self, ctx):
        await ctx.defer()
        record = await community.wallet(ctx.author.id)
        owned = [f'`{key}` · {community.SHOP[key]["name"]}' for key in record.get('owned_titles', []) if key in community.SHOP]
        await ctx.send('## Your titles\n' + ('\n'.join(owned) or 'No titles yet. Visit `/shop`.') + f'\nEquipped: `{record.get("card_title", "none")}`')

    @commands.hybrid_command(name='equip', help='Equip an owned title on your rank card, or choose none')
    async def equip(self, ctx, item: Literal['night-owl', 'aurora', 'after-hours', 'none']):
        await ctx.defer()
        await community.equip(ctx.author.id, item)
        await ctx.send(f'Rank card title: **{community.SHOP.get(item, {}).get("name", "none")}**.')

    @commands.hybrid_command(name='welcomecards', help='Manage Server: enable Pillow welcome cards in a channel, or turn them off')
    @manager()
    async def welcomecards(self, ctx, mode: Literal['on', 'off'] = 'on', channel: discord.TextChannel = None):
        await ctx.defer(ephemeral=True)
        record = await storage.welcomer_settings.get(guild_id=ctx.guild.id)
        if not record:
            record = await storage.welcomer_settings.insert(guild_id=ctx.guild.id)
        values = {'id': record['id'], 'welcome_card': mode == 'on'}
        if mode == 'on':
            channel = channel or ctx.channel
            if not isinstance(channel, discord.TextChannel):
                raise community.CommunityError('Choose a server text channel for welcome cards.')
            values.update(welcome=True, welcome_channel=channel.id)
        await storage.welcomer_settings._store.update(values)
        await ctx.send(f'Welcome cards are {mode}. Existing welcome text and media settings are retained.', ephemeral=True)

    @commands.hybrid_command(name='leave', help='Manage Server: configure Pillow goodbye cards; off disables them')
    @manager()
    async def leave(self, ctx, mode: Literal['on', 'off'] = 'on', channel: discord.TextChannel = None):
        await ctx.defer(ephemeral=True)
        if mode == 'on':
            channel = channel or ctx.channel
            if not isinstance(channel, discord.TextChannel):
                raise community.CommunityError('Choose a server text channel for goodbye cards.')
        await community.configure(ctx.guild.id, leave_channel=channel.id if mode == 'on' else 0)
        await ctx.send(f'Goodbye cards are {mode}.', ephemeral=True)

    @commands.hybrid_command(name='welcomepreview', help='Preview the modern Pillow welcome card')
    @manager()
    async def welcomepreview(self, ctx):
        await ctx.defer(ephemeral=True)
        await send_card(ctx, await cards.member_image(ctx.author), 'kuroo-welcome.png', 'Welcome card preview')

    @commands.hybrid_command(name='leavepreview', help='Preview the modern Pillow goodbye card')
    @manager()
    async def leavepreview(self, ctx):
        await ctx.defer(ephemeral=True)
        await send_card(ctx, await cards.member_image(ctx.author, leaving=True), 'kuroo-leave.png', 'Goodbye card preview')

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        try:
            settings = await community.settings(member.guild.id)
            channel = member.guild.get_channel(settings.get('leave_channel', 0))
            if channel:
                await send_card(channel, await cards.member_image(member, leaving=True), 'kuroo-leave.png',
                                f'Goodbye, {discord.utils.escape_markdown(member.display_name)}.')
        except (discord.HTTPException, OSError):
            logger.warning('Could not deliver a goodbye card; check channel permissions.')
