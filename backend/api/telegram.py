from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.dependencies import get_db, get_current_user
from backend.models.telegram import TelegramConfig
from backend.schemas.telegram import TelegramConfigCreate, TelegramConfigUpdate, TelegramConfigResponse
from backend.services.telegram_alert_service import TelegramAlertService
from backend.models.user import User
from backend.security import encrypt_value, decrypt_value

router = APIRouter(prefix="/api/telegram", tags=["telegram"])

def _to_response(config: TelegramConfig) -> dict:
    return {
        "id": config.id,
        "user_id": config.user_id,
        "bot_token": decrypt_value(config.bot_token_encrypted) if config.bot_token_encrypted else "",
        "chat_id": config.chat_id,
        "is_active": config.is_active,
        "created_at": config.created_at,
        "updated_at": config.updated_at
    }

@router.get("/config", response_model=TelegramConfigResponse)
async def get_telegram_config(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(TelegramConfig).where(TelegramConfig.user_id == current_user.id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Telegram configuration not found")
    return _to_response(config)

@router.post("/config", response_model=TelegramConfigResponse, status_code=status.HTTP_201_CREATED)
async def create_or_update_telegram_config(
    payload: TelegramConfigCreate, 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    result = await db.execute(select(TelegramConfig).where(TelegramConfig.user_id == current_user.id))
    config = result.scalar_one_or_none()
    
    if config:
        config.bot_token_encrypted = encrypt_value(payload.bot_token)
        config.chat_id = payload.chat_id
        config.is_active = payload.is_active
    else:
        config = TelegramConfig(
            user_id=current_user.id,
            bot_token_encrypted=encrypt_value(payload.bot_token),
            chat_id=payload.chat_id,
            is_active=payload.is_active
        )
        db.add(config)
        
    await db.commit()
    await db.refresh(config)
    return _to_response(config)

@router.patch("/toggle", response_model=TelegramConfigResponse)
async def toggle_telegram_bot(
    is_active: bool, 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    result = await db.execute(select(TelegramConfig).where(TelegramConfig.user_id == current_user.id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Telegram configuration not found")
        
    config.is_active = is_active
    await db.commit()
    await db.refresh(config)
    
    if is_active:
        await TelegramAlertService.dispatch_alert(db, current_user.id, "✅ OpenBull Bot Notifications Enabled")
        
    return _to_response(config)

@router.post("/test")
async def test_telegram_alert(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        await TelegramAlertService.test_alert(db, current_user.id, "🧪 *Test Alert*\n\nYour Telegram integration with OpenBull is working perfectly!")
        return {"message": "Test alert dispatched. Check your Telegram."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
