from fastapi import APIRouter, Depends, Request, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.dependencies import get_db
from backend.models.telegram import TelegramConfig
from backend.services.telegram_alert_service import TelegramAlertService

webhook_router = APIRouter(prefix="/telegram", tags=["telegram-webhook"])

async def process_telegram_update(update_data: dict, db: AsyncSession):
    if "message" not in update_data or "text" not in update_data["message"]:
        return

    chat_id = str(update_data["message"]["chat"]["id"])
    text = update_data["message"]["text"]

    # Verify this chat_id exists in our DB and is active
    result = await db.execute(
        select(TelegramConfig).where(
            TelegramConfig.chat_id == chat_id, 
            TelegramConfig.is_active == True
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        return

    if text.startswith("/pnl"):
        # Fetch actual PnL logic here
        pnl_message = "📊 *Daily PnL Report*\n\n*M2M:* +$1,250.00\n*Available Funds:* $50,000.00"
        await TelegramAlertService._send_message_async(config.bot_token, chat_id, pnl_message)
        
    elif text.startswith("/menu"):
        menu_message = "🤖 *OpenBull Bot Menu*\n\n/pnl - View Daily PnL\n/positions - View Open Positions\n/stop - Disable Alerts"
        await TelegramAlertService._send_message_async(config.bot_token, chat_id, menu_message)

@webhook_router.post("/webhook/{secret_token}")
async def telegram_webhook(secret_token: str, request: Request, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    # You should validate secret_token against an environment variable
    update_data = await request.json()
    background_tasks.add_task(process_telegram_update, update_data, db)
    return {"status": "ok"}
