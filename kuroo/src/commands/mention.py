import discord
from discord.ext import commands
from discord.ui import LayoutView, Container, Section, TextDisplay, Separator, Thumbnail
from kuroo.src.checks import checks
from kuroo.config.config import BotConfigClass
BotConfig = BotConfigClass()

class MentionReply(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # Check if the bot was mentioned directly
        if message.content.strip() in (f"<@{self.bot.user.id}>", f"<@!{self.bot.user.id}>"):
            # Get prefix for this guild
            prefix = await self.bot.get_prefix(message)
            if isinstance(prefix, list):  # handle multiple prefixes
                prefix = next((p for p in prefix if p and not p.startswith("<@")), self.bot.BotConfig.PREFIX)
                
            # Create the display using Components V2
            section = Section(
                TextDisplay(
                    f"# **Hey! I am {self.bot.user.name}**\n"
                    f"> Start using me with `{prefix}help` or `/help`"
                ),
                accessory=Thumbnail(
                    media=discord.UnfurledMediaItem(url=self.bot.user.display_avatar.url),
                    description="Bot Mention Thumbnail"
                ),
                id=1
            )

            container = Container(
                section,
                Separator(),
                TextDisplay("-# Thanks for mentioning me!")
            )

            view = LayoutView(timeout=None)
            view.add_item(container)

            try:
                await message.channel.send(view=view)
            except discord.errors.Forbidden:
                print(f"Cannot send message to channel {message.channel.id}: Missing permissions")
            except Exception as e:
                print(f"Error sending message to channel {message.channel.id}: {e}")

async def setup(bot):
    await bot.add_cog(MentionReply(bot))