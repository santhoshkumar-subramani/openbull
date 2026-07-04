import asyncio
import sys
import time

sys.path.append("/home/santhosh/dev/openbull")

from backend.strategy import tick_feed, engine, state as state_module
from backend.database import async_session
from sqlalchemy import select
from backend.models.strategy_module import SmStrategyRun

async def run():
    print("Sending ticks...")
    # SENSEX Tick
    tick_feed._on_tick({
        "symbol": "SENSEX",
        "exchange": "BSE",
        "data": {
            "ltp": 77290.0,
            "close": 76922.64,
            "volume": 0
        }
    })
    
    # INDIAVIX Tick
    tick_feed._on_tick({
        "symbol": "INDIAVIX",
        "exchange": "NSE_INDEX",
        "data": {
            "ltp": 12.64,
            "volume": 0
        }
    })
    
    await asyncio.sleep(2)
    state = await state_module.get_run_state(7)
    print("State:", state.get("condition_monitoring"))

asyncio.run(run())
