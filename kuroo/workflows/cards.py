"""Local Pillow cards: charcoal surfaces, soft gradients and no remote templates."""
import asyncio
from functools import lru_cache
from io import BytesIO
from pathlib import Path
import unicodedata

import discord
import aiohttp
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from kuroo.workflows.community import level_floor

FONT = Path(__file__).resolve().parents[1] / 'fonts' / 'DejaVuSans-Bold.ttf'
WHITE = '#F3F3F8'
MUTED = '#A1A2B3'
LAVENDER = '#C2B5FF'


@lru_cache(maxsize=24)
def font(size):
    return ImageFont.truetype(str(FONT), size)


def clean(text):
    # Avoid line/control characters changing layout; names are always clipped to fit.
    return ''.join(c for c in str(text) if not unicodedata.category(c).startswith('C') and c not in '\n\r')


def text(draw, xy, value, size=22, fill=WHITE, width=None):
    value = clean(value)
    face = font(size)
    original = value
    if width:
        while value and draw.textlength(value, font=face) > width:
            value = value[:-1]
        if value != original:
            while value and draw.textlength(value + '…', font=face) > width:
                value = value[:-1]
            value = value.rstrip() + '…'
    draw.text(xy, value, font=face, fill=fill)


@lru_cache(maxsize=4)
def _background(width, height):
    image = Image.new('RGB', (width, height))
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            violet = max(0, 1 - ((x - width * .88) / (width * .8)) ** 2 - ((y - height * .2) / height) ** 2)
            teal = max(0, 1 - ((x - width * .13) / width) ** 2 - ((y - height) / (height * .9)) ** 2)
            pixels[x, y] = (int(9 + 13 * violet), int(10 + 6 * violet + 7 * teal), int(14 + 23 * violet + 7 * teal))
    return image


def canvas(width, height, label):
    image = _background(width, height).copy()
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((20, 20, width - 21, height - 21), radius=26, outline='#34333F', width=1)
    text(draw, (48, 43), 'K U R O O', 17, LAVENDER)
    text(draw, (width - 270, 43), label.upper(), 14, MUTED, 220)
    draw.line((48, 80, width - 48, 80), fill='#30303B')
    return image, draw


def avatar(image, raw, xy, size):
    if raw:
        try:
            with Image.open(BytesIO(raw)) as opened:
                if opened.width * opened.height > 4_000_000:
                    raise ValueError('Avatar too large')
                picture = ImageOps.fit(opened.convert('RGB'), (size, size))
        except (OSError, UnidentifiedImageError, ValueError, Image.DecompressionBombError):
            picture = None
    else:
        picture = None
    if picture is None:
        picture = Image.new('RGB', (size, size), '#353244')
        local = ImageDraw.Draw(picture)
        local.ellipse((size*.32, size*.18, size*.68, size*.54), fill='#B5A5D6')
        local.ellipse((size*.15, size*.59, size*.85, size*1.18), fill='#B5A5D6')
    mask = Image.new('L', (size, size))
    ImageDraw.Draw(mask).ellipse((0, 0, size-1, size-1), fill=255)
    image.paste(picture, xy, mask)
    ImageDraw.Draw(image).ellipse((xy[0]-3, xy[1]-3, xy[0]+size+2, xy[1]+size+2), outline='#9387B2', width=2)


def png(image):
    stream = BytesIO()
    image.save(stream, format='PNG', optimize=True)
    return stream.getvalue()


def rank_card(*, name, guild, xp, level, rank, avatar_bytes=None, title=None):
    image, draw = canvas(1100, 410, 'Community rank')
    avatar(image, avatar_bytes, (55, 125), 172)
    text(draw, (266, 112), title.upper() if title else 'YOUR COMMUNITY JOURNEY', 14, MUTED, 770)
    text(draw, (266, 143), name, 36, width=765)
    text(draw, (266, 195), guild, 18, MUTED, 765)
    text(draw, (266, 241), f'LEVEL {level:,}', 25, LAVENDER, 360)
    text(draw, (760, 246), f'RANK #{rank:,}' if rank else 'UNRANKED', 18, WHITE, 270)
    start, end = level_floor(level), level_floor(level + 1)
    ratio = max(0, min(1, (xp - start) / (end - start)))
    draw.rounded_rectangle((266, 291, 1045, 303), radius=6, fill='#383642')
    if ratio:
        draw.rounded_rectangle((266, 291, 266 + max(12, int(779 * ratio)), 303), radius=6, fill=LAVENDER)
    text(draw, (266, 320), f'{xp - start:,} / {end - start:,} XP TO NEXT LEVEL', 15, MUTED, 770)
    text(draw, (55, 365), f'{xp:,} TOTAL XP', 14, MUTED, 980)
    return png(image)


def leaderboard_card(*, guild, rows, page=1, pages=1, coins=False, period=None):
    image, draw = canvas(1100, 960, f'{period.title()} XP leaderboard' if period else 'Wallet leaderboard' if coins else 'Server leaderboard')
    text(draw, (55, 103), f'{period.title()} leaderboard' if period else 'The leaderboard', 38)
    text(draw, (55, 159), 'Global wallet coins' if coins else guild, 19, MUTED, 980)
    text(draw, (63, 218), 'RANK', 13, MUTED)
    text(draw, (160, 218), 'MEMBER', 13, MUTED)
    text(draw, (805, 218), 'COINS' if coins else 'XP EARNED' if period else 'LEVEL / XP', 13, MUTED)
    for index, row in enumerate(rows[:10]):
        y = 257 + index * 61
        if index == 0:
            draw.rounded_rectangle((48, y-8, 1052, y+45), radius=13, fill='#25232F')
        else:
            draw.line((55, y+48, 1045, y+48), fill='#2B2B35')
        position = (page-1)*10 + index + 1
        text(draw, (63, y+3), f'{position:02}', 22, LAVENDER if position <= 3 else MUTED)
        text(draw, (160, y+3), row['name'], 21, width=600)
        score = f"{row.get('balance', 0):,.0f}" if coins else f"{row['xp']:,} XP" if period else f"LV {row['level']}  /  {row['xp']:,}"
        text(draw, (805, y+7), score, 16, LAVENDER, 235)
    if not rows:
        text(draw, (170, 420), 'A fresh start.', 34)
        text(draw, (170, 476), 'Earn coins or chat to take your place here.', 20, MUTED, 800)
    text(draw, (55, 910), f'PAGE {page} / {pages}', 14, MUTED)
    text(draw, (690, 910), 'SMALL MOMENTS. REAL PROGRESS.', 13, MUTED, 355)
    return png(image)


def member_card(*, name, guild, count, leaving=False, avatar_bytes=None):
    image, draw = canvas(1100, 430, 'Until next time' if leaving else 'A new beginning')
    avatar(image, avatar_bytes, (62, 130), 185)
    text(draw, (295, 119), 'GOODBYE' if leaving else 'WELCOME', 15, LAVENDER)
    text(draw, (295, 155), name, 40, width=735)
    text(draw, (295, 217), 'Thanks for being part of the story.' if leaving else 'Your next chapter starts here.', 21, MUTED, 735)
    text(draw, (295, 273), guild, 24, WHITE, 735)
    draw.line((62, 357, 1038, 357), fill='#34323F')
    text(draw, (62, 380), f'{count:,} MEMBERS', 15, MUTED)
    text(draw, (680, 380), 'A LITTLE SPACE TO BELONG.', 14, MUTED, 360)
    return png(image)


async def avatar_data(member):
    try:
        return await asyncio.wait_for(member.display_avatar.with_size(256).with_static_format('png').read(), timeout=5)
    except (discord.HTTPException, asyncio.TimeoutError, aiohttp.ClientError, OSError):
        return None


async def member_image(member, *, leaving=False):
    raw = await avatar_data(member)
    return await asyncio.to_thread(member_card, name=member.display_name, guild=member.guild.name,
                                   count=member.guild.member_count or 0, leaving=leaving, avatar_bytes=raw)
