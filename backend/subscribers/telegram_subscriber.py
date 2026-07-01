import asyncio
import logging
from backend.events.position_events import PositionOpenedEvent, PositionClosedEvent
from backend.services.telegram_alert_service import TelegramAlertService
from backend.database import async_session

logger = logging.getLogger(__name__)

def handle_position_opened(event: PositionOpenedEvent):
    """EventBus subscriber callback for position.opened"""
    logger.info(f"Telegram subscriber handling position opened for user {event.user_id}")
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_send_opened(event))
    except RuntimeError:
        import backend.utils.global_loop as gl
        if gl.MAIN_LOOP:
            asyncio.run_coroutine_threadsafe(_send_opened(event), gl.MAIN_LOOP)
        else:
            logger.error("No running event loop and MAIN_LOOP is not set.")

async def _send_opened(event: PositionOpenedEvent):
    async with async_session() as db:
        await TelegramAlertService.format_and_send_position_opened(db, event.user_id, event.position_data)

def handle_position_closed(event: PositionClosedEvent):
    """EventBus subscriber callback for position.closed"""
    logger.info(f"Telegram subscriber handling position closed for user {event.user_id}")
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_send_closed(event))
    except RuntimeError:
        import backend.utils.global_loop as gl
        if gl.MAIN_LOOP:
            asyncio.run_coroutine_threadsafe(_send_closed(event), gl.MAIN_LOOP)
        else:
            logger.error("No running event loop and MAIN_LOOP is not set.")

async def _send_closed(event: PositionClosedEvent):
    async with async_session() as db:
        await TelegramAlertService.format_and_send_position_closed(db, event.user_id, event.position_data)
