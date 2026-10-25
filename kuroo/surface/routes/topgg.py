"""Top.gg v1 vote.create webhook (HMAC SHA-256 over timestamp.rawBody)."""
from datetime import datetime, timezone
import hashlib
import hmac
import json
import time

from fastapi import APIRouter, HTTPException, Request

from kuroo.config.config import BotConfigClass
from kuroo.surface.runtime import get_bot
from kuroo.workflows import premium

router = APIRouter()


def verify(raw, header, secret):
    try:
        parts = dict(part.strip().split('=', 1) for part in header.split(','))
        stamp = parts['t']
        if abs(time.time() - int(stamp)) > 300:
            return False
        expected = hmac.new(secret.encode(), stamp.encode() + b'.' + raw, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, parts['v1'])
    except (KeyError, ValueError, TypeError):
        return False


@router.post('/webhooks/topgg')
async def receive_vote(request: Request):
    config = BotConfigClass
    if not config.TOPGG_WEBHOOK_SECRET:
        raise HTTPException(503, 'Top.gg webhook is not configured.')
    raw = await request.body()
    if not verify(raw, request.headers.get('x-topgg-signature', ''), config.TOPGG_WEBHOOK_SECRET):
        raise HTTPException(401, 'Invalid Top.gg signature.')
    try:
        event = json.loads(raw)
        data = event['data']
        project = data['project']
        bot = get_bot()
        bot_id = str(bot.user.id) if bot and bot.user else config.DISCORD_CLIENT_ID
        if not bot_id:
            raise HTTPException(503, 'Configure DISCORD_CLIENT_ID before receiving votes.')
        if (project['type'] != 'bot' or project['platform'] != 'discord'
                or str(project['platform_id']) != bot_id):
            raise HTTPException(400, 'Vote is for another project.')
        if event['type'] == 'webhook.test':
            return {'ok': True, 'test': True}
        if event['type'] != 'vote.create':
            raise HTTPException(400, 'Unsupported Top.gg event.')
        vote_id = data['id']
        user_id = int(data['user']['platform_id'])
        if not isinstance(vote_id, str) or not vote_id.isdigit() or not 0 < user_id < 2**64:
            raise ValueError('Invalid vote/user ID')
        created_at = datetime.fromisoformat(data['created_at'].replace('Z', '+00:00'))
        if created_at.tzinfo is None:
            raise ValueError('Vote timestamp must include a timezone')
        await premium.record_vote(vote_id, user_id, created_at)
    except (KeyError, ValueError, TypeError, AttributeError):
        raise HTTPException(400, 'Invalid vote payload.') from None
    return {'ok': True}
