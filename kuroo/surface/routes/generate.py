"""The old shared-token generator is disabled: only bot admin commands issue codes."""
from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.post('/redeem/generate/')
async def generate_redeem():
    raise HTTPException(410, 'Use the bot-admin-only /premiumcode command.')
