import asyncio
import logging
import time
from backend.database import async_session
from backend.sandbox import order_manager, position_manager, execution_engine
from backend.subscribers import register_all
from backend.utils.event_bus import bus

logging.basicConfig(level=logging.INFO)

def test_fill():
    register_all()
    
    # Place a dummy sandbox order
    order = order_manager.create_order(
        user_id=1,
        symbol="NIFTY_TEST",
        exchange="NFO",
        action="BUY",
        quantity=50,
        price=100.0,
        pricetype="MARKET",
        product="MIS",
        margin_blocked=5000.0
    )
    print("Placed order", order.orderid)
    
    # Force fill it
    execution_engine._try_fill_order(order, 100.0)
    
    time.sleep(2)

if __name__ == "__main__":
    test_fill()
