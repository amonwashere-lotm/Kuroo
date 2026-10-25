"""Bot admin controls and server voting rewards, using the shared V2 renderer."""
from datetime import timedelta
from types import SimpleNamespace

import discord
from discord.ext import commands

from kuroo.src.checks import checks
from kuroo.workflows import premium


def server_id(value):
    if not value.isdigit() or not 0 < int(value) < 2**64:
        raise premium.PremiumError('Enter a valid Discord server ID.')
    return int(value)


def can_claim(member):
    return checks.check_is_admin_predicate(member) or member.guild_permissions.manage_guild


class Premium(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cog_info = SimpleNamespace(name='Premium', category='Extra',
                                       description='Premium codes, server grants and vote rewards',
                                       hidden=False, emoji=bot.emoji.PREMIUM)

    async def cog_command_error(self, ctx, error):
        original = getattr(error, 'original', error)
        if isinstance(original, premium.PremiumError):
            await ctx.send(str(original), ephemeral=True)
        elif isinstance(error, commands.CheckFailure):
            await ctx.send('Only bot admins can use this command.', ephemeral=True)
        else:
            raise error

    @commands.hybrid_command(name='premiumcode', aliases=['genpremium'], help='Bot admins: generate a 1-day premium code, redeemable for 12 hours')
    @checks.is_admin()
    async def premiumcode(self, ctx):
        await ctx.defer(ephemeral=True)
        record = await premium.create_code()
        message = (f"## One-day server premium\nCode: ||`{record['code']}`||\n"
                   f"Redeem before <t:{int(record['expires_at'].timestamp())}:F>.\n"
                   'Use `/redeem` in the target server. The reward lasts 24 hours from redemption.')
        if ctx.interaction:
            await ctx.send(message, ephemeral=True)
        else:
            try:
                await ctx.author.send(message)
            except discord.HTTPException:
                await ctx.send('I could not DM the code. Enable DMs or use `/premiumcode` for a private response.')
                return
            await ctx.send('Premium code sent to your DMs.')

    @commands.hybrid_command(name='premiumgrant', help='Bot admins: grant premium to any server ID for a number of days')
    @checks.is_admin()
    async def premiumgrant(self, ctx, guild_id: str, days: commands.Range[int, 1, 3650] = 1):
        await ctx.defer(ephemeral=True)
        issued = premium.now()
        record = await premium.grant(server_id(guild_id), until=issued + timedelta(days=days), issued_at=issued)
        end = record.get('subscription_end')
        expiry = f'<t:{int(end.timestamp())}:F>' if end else 'Lifetime'
        await ctx.send(f'Server `{guild_id}` has premium. Expires: {expiry}.', ephemeral=True)

    @commands.hybrid_command(name='premiumrevoke', help='Bot admins: revoke premium from any server ID')
    @checks.is_admin()
    async def premiumrevoke(self, ctx, guild_id: str):
        await ctx.defer(ephemeral=True)
        await premium.revoke(server_id(guild_id))
        await ctx.send(f'Premium revoked for server `{guild_id}`.', ephemeral=True)

    @commands.hybrid_command(name='votepremium', help='Claim your latest Top.gg vote for 12 hours of server premium')
    @commands.guild_only()
    @commands.cooldown(2, 15, commands.BucketType.user)
    async def votepremium(self, ctx):
        if not can_claim(ctx.author):
            await ctx.send('Manage Server permission is required to claim server premium.', ephemeral=True)
            return
        if not self.bot.BotConfig.TOPGG_WEBHOOK_SECRET:
            await ctx.send('Vote rewards are not available yet. Please contact a bot admin.', ephemeral=True)
            return
        await ctx.defer(ephemeral=True)
        record = await premium.claim_vote(ctx.author.id, ctx.guild.id)
        end = record.get('subscription_end')
        expiry = f'<t:{int(end.timestamp())}:F>' if end else 'Lifetime'
        await ctx.send(f'Server premium unlocked. Expires: {expiry}.', ephemeral=True)
