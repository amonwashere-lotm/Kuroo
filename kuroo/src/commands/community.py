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

    async def cog_check(self, ctx):
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if ctx.command.root_parent is None and ctx.command.name in (
                'balance', 'daily', 'work', 'deposit', 'withdraw', 'shop', 'buy', 'inventory', 'equip'):
            if not (await community.settings(ctx.guild.id)).get('economy', True):
                raise commands.DisabledCommand('Economy commands are disabled in this server.')
        return checks.check_blacklist_predicate(ctx) and checks.check_ignore_predicate(ctx)

    async def cog_command_error(self, ctx, error):
        original = getattr(error, 'original', error)
        if isinstance(original, community.CommunityError):
            await ctx.send(str(original), ephemeral=True)
        elif isinstance(error, commands.DisabledCommand):
            await ctx.send(str(error), ephemeral=True)
        elif isinstance(error, commands.CheckFailure) and not isinstance(error, commands.NoPrivateMessage):
            await ctx.send('This command requires Manage Server permission or bot-admin access. Global economy edits require bot-admin access.', ephemeral=True)
        else:
            raise error

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.guild is None or message.author.bot or message.webhook_id or len(message.content.strip()) < 3:
            return
        ctx = await self.bot.get_context(message)
        if ctx.valid or not checks.check_blacklist_predicate(ctx) or not checks.check_ignore_predicate(ctx):
            return
        try:
            settings = await community.settings(message.guild.id)
            if (not settings.get('leveling', True)
                    or message.channel.id in settings.get('xp_ignored_channels', [])
                    or any(role.id in settings.get('xp_ignored_roles', []) for role in getattr(message.author, 'roles', []))):
                return
            result = await community.award_xp(message.guild.id, message.author.id)
            channel = message.guild.get_channel(settings.get('level_channel', 0))
            if result and result['leveled_up'] and channel:
                template = settings.get('level_message', community.LEVEL_DEFAULTS['level_message'])
                announcement = template.format(user=discord.utils.escape_markdown(message.author.display_name),
                                               server=discord.utils.escape_markdown(message.guild.name),
                                               level=result['level'], xp=result['xp'])
                await channel.send(announcement, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            logger.error('Community XP update failed; no message content was logged.')

    @commands.hybrid_command(name='rank', help='View your server rank and XP card', extras={'category': 'Leveling'})
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

    @commands.hybrid_command(name='lb', aliases=['leaderboard'], help='View the server XP or global wallet leaderboard', extras={'category': 'Leveling'})
    @commands.cooldown(2, 10, commands.BucketType.user)
    async def leaderboard(self, ctx, kind: Literal['levels', 'coins'] = 'levels', page: commands.Range[int, 1, 1000] = 1):
        await ctx.defer()
        if kind == 'coins' and not (await community.settings(ctx.guild.id)).get('economy', True):
            raise community.CommunityError('Economy commands are disabled in this server.')
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

    @commands.hybrid_command(name='leveling', help='Manage Server: turn message XP on or off', extras={'category': 'Leveling'})
    @manager()
    async def leveling(self, ctx, mode: Literal['on', 'off']):
        await ctx.defer(ephemeral=True)
        await community.configure(ctx.guild.id, leveling=mode == 'on')
        await ctx.send(f'Message XP is now {mode}.', ephemeral=True)

    @commands.hybrid_command(name='levelchannel', help='Manage Server: set the level-up channel; omit it to disable announcements', extras={'category': 'Leveling'})
    @manager()
    async def levelchannel(self, ctx, channel: discord.TextChannel = None):
        await self._level_channel(ctx, channel)

    async def _level_channel(self, ctx, channel):
        await ctx.defer(ephemeral=True)
        await community.configure(ctx.guild.id, level_channel=channel.id if channel else 0)
        await ctx.send(f'Level-up announcements: {channel.mention if channel else "disabled"}.', ephemeral=True)

    @commands.hybrid_group(name='levelconfig', invoke_without_command=True, fallback='show',
                           help='Manage Server: view and configure message XP and announcements', extras={'category': 'Leveling'})
    @manager()
    async def levelconfig(self, ctx):
        await ctx.defer(ephemeral=True)
        config = await community.level_settings(ctx.guild.id)
        channels = ', '.join(f'<#{value}>' for value in config['xp_ignored_channels']) or 'None'
        roles = ', '.join(f'<@&{value}>' for value in config['xp_ignored_roles']) or 'None'
        await ctx.send(f"## Leveling settings\nMessage XP: **{'on' if config['leveling'] else 'off'}**"
                       f"\nXP per message: **{config['xp_amount']}** · Cooldown: **{config['xp_cooldown']} seconds**"
                       f"\nAnnouncements: {f'<#{config["level_channel"]}>' if config['level_channel'] else 'disabled'}"
                       f"\nMessage: {config['level_message']}\nExcluded channels: {channels}\nExcluded roles: {roles}"
                       '\nUse `/levelconfig announcements` to choose an announcement channel.', ephemeral=True,
                       allowed_mentions=discord.AllowedMentions.none())

    @levelconfig.command(name='rate', help='Set XP per message and the persistent cooldown in seconds')
    @manager()
    async def level_rate(self, ctx, amount: commands.Range[int, 1, 1000], cooldown: commands.Range[int, 5, 3600]):
        await ctx.defer(ephemeral=True)
        await community.configure_levels(ctx.guild.id, xp_amount=amount, xp_cooldown=cooldown)
        await ctx.send(f'Message XP: **{amount}** every **{cooldown} seconds**.', ephemeral=True)

    @levelconfig.command(name='announcements', help='Set the level-up channel; omit it to disable announcements')
    @manager()
    async def level_announcements(self, ctx, channel: discord.TextChannel = None):
        await self._level_channel(ctx, channel)

    @levelconfig.command(name='message', help='Set the level announcement; placeholders: {user}, {level}, {xp}, {server}')
    @manager()
    async def level_message(self, ctx, *, template: str):
        await ctx.defer(ephemeral=True)
        await community.configure_levels(ctx.guild.id, level_message=template)
        await ctx.send('Level announcement message updated.', ephemeral=True)

    @levelconfig.command(name='ignorechannel', help='Exclude or re-enable a channel for message XP')
    @manager()
    async def level_exclude_channel(self, ctx, channel: discord.TextChannel, action: Literal['exclude', 'include']):
        await ctx.defer(ephemeral=True)
        await community.xp_exclusion(ctx.guild.id, 'channels', channel.id, remove=action == 'include')
        await ctx.send(f'{channel.mention}: message XP {"enabled" if action == "include" else "excluded"}.', ephemeral=True)

    @levelconfig.command(name='ignorerole', help='Exclude or re-enable a role for message XP')
    @manager()
    async def level_exclude_role(self, ctx, role: discord.Role, action: Literal['exclude', 'include']):
        await ctx.defer(ephemeral=True)
        await community.xp_exclusion(ctx.guild.id, 'roles', role.id, remove=action == 'include')
        await ctx.send(f'{role.mention}: message XP {"enabled" if action == "include" else "excluded"}.',
                       ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @commands.hybrid_group(name='xp', invoke_without_command=True, fallback='show',
                           help='View server XP; managers can add, remove or set XP', extras={'category': 'Leveling'})
    async def xp(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        await ctx.defer()
        result = await community.rank(ctx.guild.id, member.id)
        await ctx.send(f'{discord.utils.escape_markdown(member.display_name)}: **{result["xp"]:,} XP** · Level **{result["level"]}**.')

    @xp.command(name='edit', help='Manage Server: add, remove or set one member’s server XP')
    @manager()
    async def xp_edit(self, ctx, member: discord.Member, action: Literal['add', 'remove', 'set'],
                      amount: commands.Range[int, 0, community.MAX_AMOUNT]):
        await ctx.defer(ephemeral=True)
        result = await community.edit_xp(ctx.guild.id, member.id, amount, mode=action)
        await ctx.send(f'{discord.utils.escape_markdown(member.display_name)} now has **{result["xp"]:,} XP** · Level **{result["level"]}**.', ephemeral=True)

    @commands.hybrid_group(name='economy', invoke_without_command=True, fallback='show',
                           help='View economy settings and manage rewards', extras={'category': 'Economy'})
    async def economy(self, ctx):
        await ctx.defer()
        config = await community.economy_settings()
        enabled = (await community.settings(ctx.guild.id)).get('economy', True)
        await ctx.send(f"## Global economy\nEnabled here: **{'on' if enabled else 'off'}**"
                       f"\nDaily: **{config['daily_reward']:,}** coins every **{config['daily_cooldown']} seconds**"
                       f"\nWork: **{config['work_min']:,}–{config['work_max']:,}** coins every **{config['work_cooldown']} seconds**"
                       '\nWallets and reward cooldowns are shared across servers. Bot admins manage global rewards and balances.')

    @economy.command(name='toggle', help='Manage Server: enable or disable economy commands here')
    @manager()
    async def economy_toggle(self, ctx, mode: Literal['on', 'off']):
        await ctx.defer(ephemeral=True)
        await community.configure(ctx.guild.id, economy=mode == 'on')
        await ctx.send(f'Economy commands are {mode} in this server.', ephemeral=True)

    @economy.command(name='rewards', help='Bot admins: set global daily or work reward ranges and cooldown seconds')
    @checks.is_admin()
    async def economy_rewards(self, ctx, kind: Literal['daily', 'work'], minimum: commands.Range[int, 1, 1000000],
                              maximum: commands.Range[int, 1, 1000000], cooldown: commands.Range[int, 60, 604800]):
        await ctx.defer(ephemeral=True)
        await community.configure_rewards(kind, minimum, maximum, cooldown)
        await ctx.send(f'Global {kind} reward settings updated. Existing claim times are retained.', ephemeral=True)

    @economy.command(name='balance', help='Bot admins: add, remove or set global wallet coins')
    @checks.is_admin()
    async def economy_balance(self, ctx, member: discord.User, action: Literal['add', 'remove', 'set'],
                              amount: commands.Range[int, 0, community.MAX_AMOUNT]):
        await ctx.defer(ephemeral=True)
        record = await community.admin_balance(member.id, amount, mode=action)
        await ctx.send(f'{discord.utils.escape_markdown(member.display_name)}: **{record["balance"]:,.0f}** wallet coins.', ephemeral=True)

    @economy.command(name='resetcooldown', help='Bot admins: reset a user’s global daily or work cooldown')
    @checks.is_admin()
    async def economy_reset(self, ctx, member: discord.User, kind: Literal['daily', 'work', 'all']):
        await ctx.defer(ephemeral=True)
        await community.reset_earn_cooldown(member.id, kind)
        await ctx.send(f'Reset {kind} reward cooldowns for {discord.utils.escape_markdown(member.display_name)}.', ephemeral=True)

    @commands.hybrid_command(name='balance', aliases=['bal', 'wallet'], help='Check global wallet and bank coins', extras={'category': 'Economy'})
    async def balance(self, ctx, member: discord.Member = None):
        await ctx.defer()
        member = member or ctx.author
        record = await community.wallet(member.id)
        await ctx.send(f'## {discord.utils.escape_markdown(member.display_name)} · Coins\nWallet: **{record.get("balance", 0):,.0f}**\nBank: **{record.get("bank", 0):,.0f}**')

    @commands.hybrid_command(name='daily', help='Claim your daily coin reward; see economy show for current rates', extras={'category': 'Economy'})
    async def daily(self, ctx):
        await ctx.defer()
        record, reward = await community.earn(ctx.author.id, 'daily')
        await ctx.send(f'You claimed **{reward:,} coins**. Wallet: **{record["balance"]:,.0f}**.')

    @commands.hybrid_command(name='work', help='Work for coins; see economy show for current rates', extras={'category': 'Economy'})
    async def work(self, ctx):
        await ctx.defer()
        record, reward = await community.earn(ctx.author.id, 'work')
        await ctx.send(f'Your shift earned **{reward:,} coins**. Wallet: **{record["balance"]:,.0f}**.')

    @commands.hybrid_command(name='deposit', aliases=['dep'], help='Move a positive whole coin amount into your bank', extras={'category': 'Economy'})
    async def deposit(self, ctx, amount: commands.Range[int, 1, community.MAX_AMOUNT]):
        await ctx.defer()
        record = await community.move_bank(ctx.author.id, amount)
        await ctx.send(f'Deposited **{amount:,} coins**. Bank: **{record.get("bank", 0):,.0f}**.')

    @commands.hybrid_command(name='withdraw', aliases=['with'], help='Move a positive whole coin amount out of your bank', extras={'category': 'Economy'})
    async def withdraw(self, ctx, amount: commands.Range[int, 1, community.MAX_AMOUNT]):
        await ctx.defer()
        record = await community.move_bank(ctx.author.id, amount, withdraw=True)
        await ctx.send(f'Withdrew **{amount:,} coins**. Wallet: **{record["balance"]:,.0f}**.')

    @commands.hybrid_command(name='shop', help='Browse cosmetic titles for your rank card', extras={'category': 'Economy'})
    async def shop(self, ctx):
        lines = [f'`{key}` · **{value["name"]}** · {value["cost"]:,} coins' for key, value in community.SHOP.items()]
        await ctx.send('## Rank card titles\n' + '\n'.join(lines) + '\n\nUse `/buy item` then `/equip item`. Titles are cosmetic.')

    @commands.hybrid_command(name='buy', help='Buy a cosmetic rank card title using wallet coins', extras={'category': 'Economy'})
    async def buy(self, ctx, item: Literal['night-owl', 'aurora', 'after-hours']):
        await ctx.defer()
        record = await community.buy(ctx.author.id, item)
        await ctx.send(f'You now own **{community.SHOP[item]["name"]}**. Use `/equip {item}`. Wallet: **{record["balance"]:,.0f}**.')

    @commands.hybrid_command(name='inventory', aliases=['inv'], help='View the rank card titles you own', extras={'category': 'Economy'})
    async def inventory(self, ctx):
        await ctx.defer()
        record = await community.wallet(ctx.author.id)
        owned = [f'`{key}` · {community.SHOP[key]["name"]}' for key in record.get('owned_titles', []) if key in community.SHOP]
        await ctx.send('## Your titles\n' + ('\n'.join(owned) or 'No titles yet. Visit `/shop`.') + f'\nEquipped: `{record.get("card_title", "none")}`')

    @commands.hybrid_command(name='equip', help='Equip an owned title on your rank card, or choose none', extras={'category': 'Economy'})
    async def equip(self, ctx, item: Literal['night-owl', 'aurora', 'after-hours', 'none']):
        await ctx.defer()
        await community.equip(ctx.author.id, item)
        await ctx.send(f'Rank card title: **{community.SHOP.get(item, {}).get("name", "none")}**.')

    @commands.hybrid_command(name='welcomecards', help='Manage Server: enable Pillow welcome cards in a channel, or turn them off', extras={'category': 'MemberCards'})
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

    @commands.hybrid_command(name='leave', help='Manage Server: configure Pillow goodbye cards; off disables them', extras={'category': 'MemberCards'})
    @manager()
    async def leave(self, ctx, mode: Literal['on', 'off'] = 'on', channel: discord.TextChannel = None):
        await ctx.defer(ephemeral=True)
        if mode == 'on':
            channel = channel or ctx.channel
            if not isinstance(channel, discord.TextChannel):
                raise community.CommunityError('Choose a server text channel for goodbye cards.')
        await community.configure(ctx.guild.id, leave_channel=channel.id if mode == 'on' else 0)
        await ctx.send(f'Goodbye cards are {mode}.', ephemeral=True)

    @commands.hybrid_command(name='welcomepreview', help='Preview the modern Pillow welcome card', extras={'category': 'MemberCards'})
    @manager()
    async def welcomepreview(self, ctx):
        await ctx.defer(ephemeral=True)
        await send_card(ctx, await cards.member_image(ctx.author), 'kuroo-welcome.png', 'Welcome card preview')

    @commands.hybrid_command(name='leavepreview', help='Preview the modern Pillow goodbye card', extras={'category': 'MemberCards'})
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
