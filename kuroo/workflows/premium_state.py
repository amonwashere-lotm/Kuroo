"""Expiry-aware views of the existing guild subscription schema."""
from datetime import datetime, timezone

TIERS = ('silver_guild_preminum', 'golden_guild_premium', 'diamond_guild_premium')


def utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class GuildRecord(dict):
    def get(self, key, default=None):
        if key == 'subscription':
            end = super().get('subscription_end')
            if end and utc(end) <= datetime.now(timezone.utc):
                return 'free'
        return super().get(key, default)

    def __getitem__(self, key):
        if key == 'subscription' and key in self:
            return self.get(key)
        return super().__getitem__(key)


class GuildCache(dict):
    def __setitem__(self, key, value):
        super().__setitem__(key, GuildRecord(value))
