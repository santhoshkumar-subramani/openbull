import asyncio
from backend.utils.event_bus import bus
from backend.events.position_events import PositionOpenedEvent
from backend.subscribers import register_all
from backend.database import async_session

register_all()

event = PositionOpenedEvent(
    user_id=1,
    position_data={
        "action": "BUY",
        "symbol": "NIFTY_TEST",
        "quantity": 50,
        "average_price": 20000.50,
        "execution_time": "2026-06-30 10:00:00",
        "margin_blocked": 1000.0
    }
)
bus.publish(event)
print("Event published")
import time
time.sleep(2)
print("Done sleeping")
