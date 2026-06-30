import httpx
import asyncio
import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.models.telegram import TelegramConfig
from backend.security import decrypt_value

logger = logging.getLogger(__name__)

class TelegramAlertService:
    TELEGRAM_API_URL = "https://api.telegram.org/bot"

    @staticmethod
    async def _get_config(db: AsyncSession, user_id: int) -> Optional[TelegramConfig]:
        result = await db.execute(select(TelegramConfig).where(TelegramConfig.user_id == user_id))
        return result.scalar_one_or_none()

    @staticmethod
    def _is_bot_active(config: Optional[TelegramConfig]) -> bool:
        return bool(config and config.is_active and config.bot_token_encrypted and config.chat_id)

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
                return True
        except httpx.HTTPError as e:
            logger.error(f"Failed to send Telegram alert: {e}")
            raise Exception(f"Failed to communicate with Telegram: {e}")
        except Exception as e:
            logger.error(f"Unexpected error sending Telegram alert: {e}")
            raise Exception(f"Unexpected error: {e}")

    @classmethod
    async def test_alert(cls, db: AsyncSession, user_id: int, message: str):
        """
        Sends a test alert synchronously (awaits) and raises an exception if it fails.
        """
        config = await cls._get_config(db, user_id)
        if not config or not config.bot_token_encrypted or not config.chat_id:
            raise Exception("Telegram bot token or chat ID is missing. Please configure first.")
        
        bot_token = decrypt_value(config.bot_token_encrypted)
        await cls._send_message_async(bot_token, config.chat_id, message)

    @classmethod
    async def dispatch_alert(cls, db: AsyncSession, user_id: int, message: str, wait: bool = False):
        """
        Dispatches an alert in the background. Does not block the main thread unless wait=True.
        """
        config = await cls._get_config(db, user_id)
        if not cls._is_bot_active(config):
            logger.debug(f"Telegram alerts disabled or not configured for user {user_id}")
            return
            
        bot_token = decrypt_value(config.bot_token_encrypted)
        
        if wait:
            await cls._send_message_async(bot_token, config.chat_id, message)
        else:
            # Create an asyncio background task
            asyncio.create_task(cls._send_message_async(bot_token, config.chat_id, message))

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

    @classmethod
    async def format_and_send_position_opened(cls, db: AsyncSession, user_id: int, position_data: dict):
        """
        Formats and dispatches a Position Opened alert.
        """
        action = position_data.get("action", "UNKNOWN").upper()
        emoji = "🟢" if action == "BUY" or action == "LONG" else "🔴" if action == "SELL" or action == "SHORT" else "⚪"
        symbol = position_data.get("symbol", "UNKNOWN")
        qty = position_data.get("quantity", 0)
        avg_price = position_data.get("average_price", 0.0)
        exec_time = position_data.get("execution_time", "N/A")
        
        # Format numbers
        qty_fmt = f"{qty:,}" if isinstance(qty, (int, float)) else qty
        price_fmt = f"{avg_price:,.2f}" if isinstance(avg_price, (int, float)) else avg_price
        
        lines = [
            f"{emoji} *Position Opened: {action}*",
            f"*{symbol}*",
            "",
            f"• *Quantity:* {qty_fmt}",
            f"• *Avg Price:* {price_fmt}",
            f"• *Time:* {exec_time}"
        ]
        
        # Good to have fields
        if "strategy_name" in position_data:
            lines.append(f"• *Strategy:* {position_data['strategy_name']}")
        if "broker_id" in position_data:
            lines.append(f"• *Broker:* {position_data['broker_id']}")
        if "margin_blocked" in position_data:
            margin = position_data["margin_blocked"]
            margin_fmt = f"{margin:,.2f}" if isinstance(margin, (int, float)) else margin
            lines.append(f"• *Margin:* {margin_fmt}")
        if "target" in position_data:
            lines.append(f"• *Target:* {position_data['target']}")
        if "stop_loss" in position_data:
            lines.append(f"• *SL:* {position_data['stop_loss']}")
            
        message = "\n".join(lines)
        await cls.dispatch_alert(db, user_id, message, wait=True)

    @classmethod
    async def format_and_send_position_closed(cls, db: AsyncSession, user_id: int, position_data: dict):
        """
        Formats and dispatches a Position Closed alert.
        """
        symbol = position_data.get("symbol", "UNKNOWN")
        qty = position_data.get("quantity", 0)
        avg_price = position_data.get("average_price", 0.0)
        realized_pnl = position_data.get("realized_pnl", 0.0)
        
        pnl_val = float(realized_pnl) if isinstance(realized_pnl, (int, float, str)) and str(realized_pnl).replace('.','',1).replace('-','',1).isdigit() else 0.0
        pnl_emoji = "✅" if pnl_val > 0 else "❌" if pnl_val < 0 else "➖"
        
        # Format numbers
        qty_fmt = f"{qty:,}" if isinstance(qty, (int, float)) else qty
        price_fmt = f"{avg_price:,.2f}" if isinstance(avg_price, (int, float)) else avg_price
        pnl_fmt = f"{pnl_val:,.2f}"
        
        lines = [
            f"{pnl_emoji} *POSITION CLOSED*",
            f"*{symbol}*",
            "",
            f"• *Closed Qty:* {qty_fmt}",
            f"• *Exit Price:* {price_fmt}",
            f"• *Realized PnL:* {pnl_fmt}"
        ]
        
        # Good to have fields
        if "roi_percent" in position_data:
            lines.append(f"• *ROI:* {position_data['roi_percent']}%")
        if "duration" in position_data:
            lines.append(f"• *Duration:* {position_data['duration']}")
        if "strategy_name" in position_data:
            lines.append(f"• *Strategy:* {position_data['strategy_name']}")
        if "updated_balance" in position_data:
            lines.append(f"• *Balance:* {position_data['updated_balance']}")
            
        message = "\n".join(lines)
        await cls.dispatch_alert(db, user_id, message, wait=True)
