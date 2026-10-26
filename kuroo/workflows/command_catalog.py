"""Discover public commands from the live prefix and application registries."""
from dataclasses import dataclass

from discord import app_commands


@dataclass
class Category:
    key: str
    name: str
    description: str
    emoji: str | None = None
    section: str = 'Extra'


@dataclass
class Entry:
    name: str
    category: str
    prefix: object = None
    slash: object = None

    @property
    def description(self):
        return (getattr(self.prefix, 'help', None) or getattr(self.slash, 'description', None)
                or 'No description provided.')

    @property
    def aliases(self):
        parent = getattr(getattr(self.prefix, 'parent', None), 'qualified_name', '')
        return [f'{parent} {alias}'.strip() for alias in getattr(self.prefix, 'aliases', [])]

    def usages(self, prefix):
        result = []
        if self.prefix is not None:
            signature = self.prefix.usage or self.prefix.signature
            result.append(f'{prefix}{self.name} {signature}'.rstrip())
        if self.slash is not None:
            options = ' '.join(f'<{p.name}>' if p.required else f'[{p.name}]' for p in self.slash.parameters)
            result.append(f'/{self.name} {options}'.rstrip())
        return result


def hidden(command, cog):
    info = getattr(cog, 'cog_info', None)
    if info and getattr(info, 'hidden', False):
        return True
    while command is not None:
        wrapped = getattr(command, 'wrapped', command)
        if getattr(wrapped, 'hidden', False) or getattr(wrapped, 'extras', {}).get('hidden', False):
            return True
        command = command.parent
    return False


class CommandCatalog:
    def __init__(self, bot, guild=None):
        self.categories = {}
        entries = {}
        suppressed = set()

        def category(cog, command):
            # Commands may opt into a product category; descendants inherit it.
            current = command
            key = None
            while current is not None:
                key = getattr(getattr(current, 'wrapped', current), 'extras', {}).get('category')
                if key:
                    break
                current = current.parent
            key = key or (cog.qualified_name if cog else 'General')
            if key == 'Welcomer':
                key = 'MemberCards'
            if key not in self.categories:
                info = getattr(cog, 'cog_info', None)
                themes = {
                    'Leveling': ('Leveling', 'Server ranks, XP controls and level announcements', 'POSITION'),
                    'Economy': ('Economy', 'Wallets, banking, rewards and the title shop', 'ECONOMY'),
                    'MemberCards': ('Welcome & Leave', 'Welcome messages and member cards', 'WELCOME'),
                    'Utils': ('Utilities & Info', 'Useful tools, profiles and server information', 'UTILS'),
                    'More': ('General & Tools', 'Everyday commands and personal settings', 'GENERAL'),
                    'Voice': ('Voice Rooms', 'Voice channels and room management', 'MICROPHONE'),
                    'Security': ('Server Security', 'Protect your server and configure safeguards', 'SECURITY'),
                }
                name, description, icon = themes.get(key, (getattr(info, 'name', key),
                    getattr(info, 'description', None) or getattr(cog, 'description', '') or 'Commands', None))
                icons = getattr(bot, 'emoji', None)
                emoji = getattr(icons, icon, None) if icon else getattr(info, 'emoji', None)
                if not emoji or not str(emoji).strip().startswith(('<:', '<a:')):
                    emoji = getattr(icons, 'CATEGORY', None)
                self.categories[key] = Category(
                    key, name, description, emoji, getattr(info, 'category', 'Extra'),
                )
            return key

        for command in bot.walk_commands():
            if hidden(command, command.cog):
                suppressed.add(command.qualified_name)
                continue
            entries[command.qualified_name] = Entry(command.qualified_name, category(command.cog, command), prefix=command)

        slash_commands = {c.qualified_name: c for c in bot.tree.walk_commands() if isinstance(c, app_commands.Command)}
        if guild is not None:
            slash_commands.update({c.qualified_name: c for c in bot.tree.walk_commands(guild=guild) if isinstance(c, app_commands.Command)})
        for name, command in slash_commands.items():
            cog = command.binding
            if name in suppressed or hidden(command, cog):
                continue
            if name in entries:
                entries[name].slash = command
            else:
                entries[name] = Entry(name, category(cog, command), slash=command)
        self.entries = sorted(entries.values(), key=lambda entry: entry.name.casefold())
        self.categories = dict(sorted(self.categories.items(), key=lambda pair: pair[1].name.casefold()))

    def in_category(self, key):
        return [entry for entry in self.entries if entry.category == key]

    def find(self, query):
        query = query.strip().removeprefix('/').casefold()
        return next((entry for entry in self.entries if query == entry.name.casefold()
                     or query in [alias.casefold() for alias in entry.aliases]), None)
