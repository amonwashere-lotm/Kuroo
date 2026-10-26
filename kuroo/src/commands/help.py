"""Live command discovery and paginated Components V2 help."""
import traceback
import sys

import discord
from discord import ui
from discord.ext import commands

from kuroo.console.logging import logger
from kuroo.engine.Bot import AutoShardedBot
from kuroo.src.checks import checks
from kuroo.style import color
from kuroo.workflows.command_catalog import CommandCatalog

PAGE_SIZE = 10


class CogInfo:
    def __init__(self, name, category, description, hidden, emoji):
        self.name, self.category, self.description = name, category, description
        self.hidden, self.emoji = hidden, emoji


class Help(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cog_info = CogInfo('Help', 'Extra', 'Discover commands and usage', False, bot.emoji.HELP)
        self._banner_loaded = False

    @commands.hybrid_command(name='help', help='Discover all commands, or look up a command by name', aliases=['h'])
    @checks.ignore_check()
    @checks.blacklist_check()
    @commands.cooldown(10, 60, commands.BucketType.user)
    async def help(self, ctx, *, command: str = None):
        if ctx.interaction and not ctx.interaction.response.is_done():
            await ctx.defer()
        if not self._banner_loaded:
            banner = self.bot.BotConfig.BOT_BANNER_URL
            if not banner:
                try:
                    profile = await self.bot.fetch_user(self.bot.user.id)
                    banner = profile.banner.url if profile.banner else None
                except discord.HTTPException:
                    banner = None
            self.bot.help_banner_url = banner
            self._banner_loaded = True
        if command:
            catalog = CommandCatalog(self.bot, ctx.guild)
            prefix = self.bot.cache.guilds.get(str(ctx.guild.id) if ctx.guild else '', {}).get('prefix') or self.bot.BotConfig.PREFIX
            query = command.removeprefix(prefix).strip()
            # Resolve group aliases as well as ordinary aliases through discord.py.
            registered = self.bot.get_command(query)
            entry = catalog.find(registered.qualified_name if registered else query)
            if entry is None:
                await ctx.send('Command not found. Use `/help` to browse the current command list.', ephemeral=True)
                return
            view = CommandView(self.bot, ctx, entry=entry)
        else:
            view = HomeView(self.bot, ctx)
        view.message = await ctx.send(view=view)

    @commands.hybrid_command(name='synccommands', help='Bot admins: publish registered slash commands to Discord', hidden=True)
    @checks.is_admin()
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def synccommands(self, ctx):
        await ctx.defer(ephemeral=True)
        try:
            synced = await self.bot.tree.sync()
        except discord.HTTPException as error:
            logger.error(f'Manual slash sync failed (HTTP {error.status}, code {error.code}).')
            await ctx.send('Discord rejected command sync. Check startup logs and the bot application configuration.', ephemeral=True)
            return
        self.bot._slash_synced = True
        await ctx.send(f'Synced {len(synced)} global slash commands. '
                       f'{len(list(self.bot.walk_commands()))} prefix command paths are loaded. '
                       'Try `/help`, `/rank`, `/lb`, or `/balance`.', ephemeral=True)


class BaseHelpView(ui.LayoutView):
    def __init__(self, bot, ctx, all_app_commands=None, reported=False, timeout=120):
        super().__init__(timeout=timeout)
        self.bot, self.ctx, self.reported = bot, ctx, reported
        self.message = None
        self.catalog = CommandCatalog(bot, ctx.guild)

    def container(self):
        container = ui.Container()
        banner = getattr(self.bot, 'help_banner_url', None)
        if banner:
            container.add_item(ui.MediaGallery(discord.MediaGalleryItem(media=banner)))
        return container

    def icon(self, name):
        return str(getattr(self.bot.emoji, name, self.bot.emoji.CATEGORY)).strip()

    def category_icon(self, key):
        return str(self.catalog.categories[key].emoji or self.icon('CATEGORY')).strip()

    def get_language(self):
        guild_id = str(self.ctx.guild.id) if self.ctx.guild else ''
        return (self.bot.cache.guilds.get(guild_id, {}).get('language') or 'en').lower()

    def tr(self, english, hindi):
        return hindi if self.get_language() == 'hi' else english

    @property
    def prefix(self):
        guild_id = str(self.ctx.guild.id) if self.ctx.guild else ''
        return self.bot.cache.guilds.get(guild_id, {}).get('prefix') or self.bot.BotConfig.PREFIX

    async def interaction_check(self, interaction):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(self.tr('Run your own `/help` to use these controls.', 'In controls ke liye apna `/help` chalayein.'), ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for item in self.walk_children():
            if isinstance(item, (ui.Button, ui.Select)):
                item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    async def show(self, interaction, view):
        view.message = interaction.message
        await interaction.response.edit_message(view=view)
        self.stop()

    def page(self, items, page, size=PAGE_SIZE):
        self.pages = max(1, (len(items) + size - 1) // size)
        self.current_page = max(0, min(page, self.pages - 1))
        return items[self.current_page * size:(self.current_page + 1) * size]

    def navigation(self, container, *, back=True):
        row = ui.ActionRow()
        if self.pages > 1:
            row.add_item(PageButton(-1, self.current_page == 0, self.icon('PREVIOUS')))
            row.add_item(PageButton(1, self.current_page == self.pages - 1, self.icon('NEXT')))
        if back:
            row.add_item(BackButton(self.icon('BACK')))
        container.add_item(row)


class HomeView(BaseHelpView):
    def __init__(self, bot, ctx, all_app_commands=None, reported=False, *, page=0):
        super().__init__(bot, ctx, reported=reported)
        categories = self.page(list(self.catalog.categories.values()), page)
        container = self.container()
        container.add_item(ui.TextDisplay(f'# {self.icon("HELP")} {self.bot.user.display_name}\n-# {self.tr("Help menu", "Help menu")}'))
        container.add_item(ui.TextDisplay(
            f'{self.tr("Prefix", "Prefix")}: `{self.prefix}` · {self.tr("Commands", "Commands")}: **{len(self.catalog.entries)}**\n'
            + self.tr('Choose a category, browse all commands, or use `/help command:rank`.', 'Category chunein, sab commands dekhein, ya `/help command:rank` use karein.')
        ))
        if categories:
            container.add_item(ui.TextDisplay('\n'.join(f'{self.category_icon(c.key)} **{c.name}** · {len(self.catalog.in_category(c.key))} commands' for c in categories)))
            container.add_item(CategorySelectRow(self, categories))
        else:
            container.add_item(ui.TextDisplay('No public commands are loaded.'))
        row = ui.ActionRow()
        row.add_item(AllCommandsButton(self.icon('COMMANDS')))
        row.add_item(ReportButton(self.bot.emoji.ERROR, bot))
        if reported:
            row.children[1].disabled = True
        for label, url in [('Invite', bot.urls.INVITE), ('Support', bot.urls.SUPPORT_SERVER), ('Vote', bot.urls.VOTE)]:
            row.add_item(ui.Button(label=label, url=url, emoji=self.icon({'Invite': 'INVITE', 'Support': 'SUPPORT', 'Vote': 'VOTE'}[label])))
        container.add_item(row)
        if self.pages > 1:
            container.add_item(ui.TextDisplay(f'Page {self.current_page + 1}/{self.pages}'))
            self.navigation(container, back=False)
        self.add_item(container)

    def at_page(self, page):
        return HomeView(self.bot, self.ctx, reported=self.reported, page=page)


class AllCommandsButton(ui.Button):
    def __init__(self, emoji=None):
        super().__init__(label='All Commands', style=discord.ButtonStyle.primary, emoji=emoji)

    async def callback(self, interaction):
        if await self.view.interaction_check(interaction):
            await self.view.show(interaction, AllCommandsView(self.view.bot, self.view.ctx, reported=self.view.reported))


class CategorySelectRow(ui.ActionRow):
    def __init__(self, view, categories):
        super().__init__()
        self.keys = [category.key for category in categories]
        select = ui.Select(placeholder=view.tr('Choose a category', 'Category chunein'), options=[
            discord.SelectOption(label=category.name[:100], value=str(index), description=one_line(category.description, 100), emoji=view.category_icon(category.key))
            for index, category in enumerate(categories)
        ])
        select.callback = self.select_category
        self.add_item(select)

    async def select_category(self, interaction):
        if await self.view.interaction_check(interaction):
            key = self.keys[int(interaction.data['values'][0])]
            await self.view.show(interaction, CategoryView(self.view.bot, self.view.ctx, category=key, reported=self.view.reported))


def one_line(value, limit):
    return ' '.join(str(value).split())[:limit] or 'View commands'


class AllCommandsView(BaseHelpView):
    def __init__(self, bot, ctx, all_app_commands=None, reported=False, *, page=0, category=None):
        super().__init__(bot, ctx, reported=reported)
        self.category = category
        entries = self.catalog.in_category(category) if category else self.catalog.entries
        self.entries = self.page(entries, page)
        title = self.catalog.categories[category].name if category in self.catalog.categories else self.tr('All Commands', 'Sabhi Commands')
        container = self.container()
        container.add_item(ui.TextDisplay(f'# {self.category_icon(category) if category else self.icon("COMMANDS")} {title}\n-# {len(entries)} commands · Page {self.current_page + 1}/{self.pages}'))
        for entry in self.entries:
            methods = ' · '.join(('Prefix' if entry.prefix else '', 'Slash' if entry.slash else '')).strip(' ·')
            container.add_item(ui.TextDisplay(f'{self.category_icon(entry.category)} **`{entry.name}`** · {methods}\n{one_line(entry.description, 100)}'))
        if self.entries:
            container.add_item(CommandSelectRow(self))
        else:
            container.add_item(ui.TextDisplay('No public commands are loaded in this category.'))
        self.navigation(container)
        self.add_item(container)

    def at_page(self, page):
        return AllCommandsView(self.bot, self.ctx, reported=self.reported, page=page, category=self.category)


class CategoryView(AllCommandsView):
    pass


class CommandSelectRow(ui.ActionRow):
    def __init__(self, view):
        super().__init__()
        self.names = [entry.name for entry in view.entries]
        select = ui.Select(placeholder=view.tr('Choose a command', 'Command chunein'), options=[
            discord.SelectOption(label=entry.name[:100], value=str(index), description=one_line(entry.description, 100), emoji=view.category_icon(entry.category))
            for index, entry in enumerate(view.entries)
        ])
        select.callback = self.select_command
        self.add_item(select)

    async def select_command(self, interaction):
        if not await self.view.interaction_check(interaction):
            return
        name = self.names[int(interaction.data['values'][0])]
        entry = CommandCatalog(self.view.bot, self.view.ctx.guild).find(name)
        if entry is None:
            await interaction.response.send_message('This command was unloaded. Open `/help` again.', ephemeral=True)
            return
        await self.view.show(interaction, CommandView(self.view.bot, self.view.ctx, entry=entry, reported=self.view.reported))


class CommandView(BaseHelpView):
    def __init__(self, bot, ctx, all_app_commands=None, *, entry, reported=False):
        super().__init__(bot, ctx, reported=reported)
        self.entry = entry
        self.category = entry.category
        container = self.container()
        body = f'# {self.category_icon(entry.category)} {entry.name}\n{entry.description}\n\n**{self.tr("Usage", "Istemaal")}:**\n'
        body += '\n'.join(f'`{usage}`' for usage in entry.usages(self.prefix))
        if entry.aliases:
            body += '\n\n**Aliases:** ' + ', '.join(f'`{self.prefix}{alias}`' for alias in entry.aliases)
        descendants = [value.name for value in self.catalog.entries if value.name.startswith(entry.name + ' ')]
        if descendants:
            body += '\n\n**Subcommands:**\n' + '\n'.join(f'`{name}`' for name in descendants)
        container.add_item(ui.TextDisplay(body))
        row = ui.ActionRow()
        row.add_item(BackButton(self.icon('BACK')))
        container.add_item(row)
        self.add_item(container)


class PageButton(ui.Button):
    def __init__(self, delta, disabled, emoji=None):
        super().__init__(label='Previous' if delta < 0 else 'Next', style=discord.ButtonStyle.secondary, disabled=disabled, emoji=emoji)
        self.delta = delta

    async def callback(self, interaction):
        if await self.view.interaction_check(interaction):
            await self.view.show(interaction, self.view.at_page(self.view.current_page + self.delta))


class BackButton(ui.Button):
    def __init__(self, emoji=None):
        super().__init__(label='Back', style=discord.ButtonStyle.secondary, emoji=emoji)

    async def callback(self, interaction):
        if await self.view.interaction_check(interaction):
            if isinstance(self.view, CommandView):
                view = CategoryView(self.view.bot, self.view.ctx, category=self.view.category, reported=self.view.reported)
            else:
                view = HomeView(self.view.bot, self.view.ctx, reported=self.view.reported)
            await self.view.show(interaction, view)


class ReportButton(ui.Button["BaseHelpView"]):
    def __init__(self, emoji, bot):
        super().__init__(label="Report", style=discord.ButtonStyle.red, emoji=emoji)
        self.bot = bot

    async def callback(self, interaction: discord.Interaction):
        if not await self.view.interaction_check(interaction):
            return
        modal = ReportModal(self.bot)
        await interaction.response.send_modal(modal)
        await modal.wait()
        if modal.submitted:
            self.view.reported = True
            self.disabled = True
            await interaction.message.edit(view=self.view)


class ReportModal(ui.Modal, title="Submit Report"):
    report_title_field = ui.TextInput(
        placeholder="Report Title",
        label="Report Title",
        required=True,
        style=discord.TextStyle.short,
    )
    report_description_field = ui.TextInput(
        placeholder="Report Description",
        label="Report Description",
        required=True,
        style=discord.TextStyle.long,
    )
    report_attachment_field = ui.TextInput(
        placeholder="Separate the links with comma",
        label="Report Attachment links",
        required=False,
        style=discord.TextStyle.long,
    )

    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.submitted = False

    async def on_submit(self, interaction: discord.Interaction):
        title = self.report_title_field.value
        description = self.report_description_field.value
        attachments = (
            self.report_attachment_field.value.split(",")
            if self.report_attachment_field.value
            else []
        )
        if not title or not description:
            return await interaction.response.send_message(
                embed=discord.Embed(
                    description="Title and Description are required", color=color.red
                ),
                ephemeral=True,
                delete_after=10,
            )
        embed = discord.Embed(title=title, description=description, color=color.black)
        if attachments:
            embed.add_field(
                name="Attachments links", value="\n".join(attachments), inline=False
            )
        embed.set_footer(
            text=f"Reported by {interaction.user.display_name} | {interaction.user.id}",
            icon_url=interaction.user.display_avatar.url,
        )
        embed.set_author(
            name=f"{interaction.user.display_name}",
            icon_url=interaction.user.display_avatar.url,
        )
        channel = self.bot.get_channel(self.bot.channels.report_channel)
        if channel:
            await channel.send(embed=embed)
        else:
            logger.error(
                f"Report channel not found. Channel ID: {self.bot.channels.report_channel}"
            )
        await interaction.response.send_message(
            embed=discord.Embed(
                description="Report submitted successfully", color=color.green
            ),
            ephemeral=True,
        )
        self.submitted = True
        self.stop()

    async def on_error(
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        logger.error(
            f"Error in file {__file__} at line {traceback.extract_tb(sys.exc_info()[2])[0][1]}: {error}"
        )



async def setup(bot: AutoShardedBot):
    await bot.add_cog(Help(bot))
