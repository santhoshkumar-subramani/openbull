import logging
import os
from fastapi import APIRouter, Depends, Request, BackgroundTasks, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.dependencies import get_db
from backend.models.telegram import TelegramConfig
from backend.services.telegram_alert_service import TelegramAlertService
from backend.security import decrypt_value
from backend.database import async_session

logger = logging.getLogger(__name__)

webhook_router = APIRouter(prefix="/api/telegram", tags=["telegram-webhook"])

async def process_telegram_update(update_data: dict):
    """Background task to process telegram updates in a separate db session."""
    if "message" not in update_data or "text" not in update_data["message"]:
        return

    chat_id = str(update_data["message"]["chat"]["id"])
    text = update_data["message"]["text"]

    async with async_session() as db:
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

        bot_token = decrypt_value(config.bot_token_encrypted)

        if text.startswith("/pnl"):
            try:
                from backend.strategy.live_auth import resolve_live_auth
                from backend.services.positions_service import get_positions_with_auth
                
                auth_ctx = await resolve_live_auth(db, user_id=config.user_id)
                if not auth_ctx:
                    await TelegramAlertService._send_message_async(bot_token, chat_id, "❌ *Error*\n\nBroker authentication not found. Please login from the dashboard first.")
                    return
                
                success, response_data, status_code = get_positions_with_auth(
                    auth_ctx.auth_token, auth_ctx.broker, auth_ctx.config, user_id=config.user_id
                )
                
                if success and "data" in response_data:
                    positions = response_data["data"]
                    total_mtm = 0.0
                    for pos in positions:
                        # Depending on sandbox or live, keys might vary
                        mtm = pos.get("urmtm") or pos.get("mtm") or pos.get("unrealized_pnl") or 0.0
                        total_mtm += float(mtm)
                    
                    mtm_fmt = f"{total_mtm:,.2f}"
                    emoji = "🟩" if total_mtm >= 0 else "🟥"
                    
                    pnl_message = f"📊 *Daily PnL Report*\n\n{emoji} *M2M (Unrealized):* {mtm_fmt}\n*Positions:* {len(positions)}"
                    await TelegramAlertService._send_message_async(bot_token, chat_id, pnl_message)
                else:
                    await TelegramAlertService._send_message_async(bot_token, chat_id, "❌ *Error*\n\nFailed to fetch positions.")
            except Exception as e:
                logger.error("Error generating PnL for Telegram: %s", e)
                await TelegramAlertService._send_message_async(bot_token, chat_id, "❌ *Error*\n\nAn unexpected error occurred while fetching PnL.")
            
        elif text.startswith("/positions"):
            try:
                from backend.strategy.live_auth import resolve_live_auth
                from backend.services.positions_service import get_positions_with_auth
                
                auth_ctx = await resolve_live_auth(db, user_id=config.user_id)
                if not auth_ctx:
                    await TelegramAlertService._send_message_async(bot_token, chat_id, "❌ *Error*\n\nBroker authentication not found.")
                    return
                
                success, response_data, status_code = get_positions_with_auth(
                    auth_ctx.auth_token, auth_ctx.broker, auth_ctx.config, user_id=config.user_id
                )
                
                if success and "data" in response_data:
                    positions = response_data["data"]
                    open_positions = [p for p in positions if float(p.get("quantity") or p.get("netqty") or p.get("qty") or 0) != 0]
                    
                    if not open_positions:
                        await TelegramAlertService._send_message_async(bot_token, chat_id, "ℹ️ You have no open positions.")
                        return
                        
                    lines = ["📋 *Open Positions*\n"]
                    for p in open_positions:
                        sym = p.get("symbol", "UNKNOWN")
                        qty = float(p.get("quantity") or p.get("netqty") or p.get("qty") or 0)
                        mtm = float(p.get("urmtm") or p.get("mtm") or p.get("unrealized_pnl") or 0.0)
                        action = "LONG" if qty > 0 else "SHORT"
                        emoji = "🟩" if mtm >= 0 else "🟥"
                        
                        lines.append(f"*{sym}* ({action} {abs(qty)})")
                        lines.append(f"└ {emoji} MTM: {mtm:,.2f}")
                        lines.append("")
                        
                    await TelegramAlertService._send_message_async(bot_token, chat_id, "\n".join(lines))
                else:
                    await TelegramAlertService._send_message_async(bot_token, chat_id, "❌ *Error*\n\nFailed to fetch positions.")
            except Exception as e:
                logger.error("Error generating positions for Telegram: %s", e)
                await TelegramAlertService._send_message_async(bot_token, chat_id, "❌ *Error*\n\nAn unexpected error occurred.")
                
        elif text.startswith("/stop"):
            try:
                config.is_active = False
                await db.commit()
                await TelegramAlertService._send_message_async(bot_token, chat_id, "🚫 *Alerts Disabled*\n\nYour Telegram alerts have been successfully deactivated. You can re-enable them from the OpenBull Dashboard.")
            except Exception as e:
                logger.error("Error stopping Telegram alerts: %s", e)
                await TelegramAlertService._send_message_async(bot_token, chat_id, "❌ *Error*\n\nFailed to disable alerts.")
                
        elif text.startswith("/menu"):
            menu_message = "🤖 *OpenBull Bot Menu*\n\n/pnl - View Daily PnL\n/positions - View Open Positions\n/stop - Disable Alerts"
            await TelegramAlertService._send_message_async(bot_token, chat_id, menu_message)


@webhook_router.post("/webhook/{secret_token}")
async def telegram_webhook(secret_token: str, request: Request, background_tasks: BackgroundTasks):
    expected_token = os.getenv("TELEGRAM_WEBHOOK_SECRET")
    if expected_token and secret_token != expected_token:
        # If a secret is configured but doesn't match, reject.
        # If not configured, we allow it (for dev environments).
        logger.warning("Unauthorized telegram webhook attempt")
        raise HTTPException(status_code=403, detail="Forbidden")

    update_data = await request.json()
    # Add to background tasks WITHOUT passing the request-scoped DB session
    background_tasks.add_task(process_telegram_update, update_data)
    return {"status": "ok"}
