import httpx
import asyncio
import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.models.telegram import TelegramConfig

logger = logging.getLogger(__name__)

class TelegramAlertService:
    TELEGRAM_API_URL = "https://api.telegram.org/bot"

    @staticmethod
    async def _get_config(db: AsyncSession, user_id: int) -> Optional[TelegramConfig]:
        result = await db.execute(select(TelegramConfig).where(TelegramConfig.user_id == user_id))
        return result.scalar_one_or_none()

    @staticmethod
    def _is_bot_active(config: Optional[TelegramConfig]) -> bool:
        return bool(config and config.is_active and config.bot_token and config.chat_id)

    @classmethod
    async def _send_message_async(cls, bot_token: str, chat_id: str, text: str):
        url = f"{cls.TELEGRAM_API_URL}{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }
        
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                logger.info(f"Telegram alert sent to chat {chat_id}")
        except httpx.HTTPError as e:
            logger.error(f"Failed to send Telegram alert: {e}")
        except Exception as e:
            logger.error(f"Unexpected error sending Telegram alert: {e}")

    @classmethod
    async def dispatch_alert(cls, db: AsyncSession, user_id: int, message: str):
        """
        Dispatches an alert in the background. Does not block the main thread.
        """
        config = await cls._get_config(db, user_id)
        if not cls._is_bot_active(config):
            logger.debug(f"Telegram alerts disabled or not configured for user {user_id}")
            return
            
        # Create an asyncio background task
        asyncio.create_task(cls._send_message_async(config.bot_token, config.chat_id, message))

    @classmethod
    async def send_order_alert(cls, db: AsyncSession, user_id: int, order_details: dict):
        symbol = order_details.get("symbol", "UNKNOWN")
        side = order_details.get("side", "UNKNOWN")
        qty = order_details.get("qty", 0)
        status = order_details.get("status", "UNKNOWN")
        
        message = f"🚨 *Order Update*\n\n*Symbol:* {symbol}\n*Side:* {side}\n*Qty:* {qty}\n*Status:* {status}"
        await cls.dispatch_alert(db, user_id, message)

    @classmethod
    async def send_trade_alert(cls, db: AsyncSession, user_id: int, trade_details: dict):
        symbol = trade_details.get("symbol", "UNKNOWN")
        price = trade_details.get("price", 0)
        pnl = trade_details.get("pnl", 0)
        
        message = f"💰 *Trade Executed*\n\n*Symbol:* {symbol}\n*Execution Price:* {price}\n*Realized PnL:* {pnl}"
        await cls.dispatch_alert(db, user_id, message)
