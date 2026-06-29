from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from backend.database import get_db
from backend.models.telegram import TelegramConfig
from backend.schemas.telegram import TelegramConfigCreate, TelegramConfigUpdate, TelegramConfigResponse
from backend.dependencies import get_current_user
from backend.services.telegram_alert_service import TelegramAlertService
from backend.models.user import User

router = APIRouter(prefix="/telegram", tags=["telegram"])

@router.get("/config", response_model=TelegramConfigResponse)
def get_telegram_config(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    config = db.query(TelegramConfig).filter(TelegramConfig.user_id == current_user.id).first()
    if not config:
        raise HTTPException(status_code=404, detail="Telegram configuration not found")
    return config

@router.post("/config", response_model=TelegramConfigResponse, status_code=status.HTTP_201_CREATED)
def create_or_update_telegram_config(
    payload: TelegramConfigCreate, 
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    config = db.query(TelegramConfig).filter(TelegramConfig.user_id == current_user.id).first()
    
    if config:
        config.bot_token = payload.bot_token
        config.chat_id = payload.chat_id
        config.is_active = payload.is_active
    else:
        config = TelegramConfig(
            user_id=current_user.id,
            bot_token=payload.bot_token,
            chat_id=payload.chat_id,
            is_active=payload.is_active
        )
        db.add(config)
        
    db.commit()
    db.refresh(config)
    return config

@router.patch("/toggle", response_model=TelegramConfigResponse)
def toggle_telegram_bot(
    is_active: bool, 
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    config = db.query(TelegramConfig).filter(TelegramConfig.user_id == current_user.id).first()
    if not config:
        raise HTTPException(status_code=404, detail="Telegram configuration not found")
        
    config.is_active = is_active
    db.commit()
    db.refresh(config)
    
    if is_active:
        TelegramAlertService.dispatch_alert(db, current_user.id, "✅ OpenBull Bot Notifications Enabled")
        
    return config

@router.post("/test")
def test_telegram_alert(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    TelegramAlertService.dispatch_alert(db, current_user.id, "🧪 *Test Alert*\n\nYour Telegram integration with OpenBull is working perfectly!")
    return {"message": "Test alert dispatched. Check your Telegram."}
