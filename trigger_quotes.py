import asyncio
import sys
sys.path.append("/home/santhosh/dev/openbull")

from backend.strategy import live_quotes, state as state_module

async def run():
    print("Triggering one tick...")
    await live_quotes._one_tick(3)
    await asyncio.sleep(2)
    state = await state_module.get_run_state(7)
    print("State:", state.get("condition_monitoring"))

asyncio.run(run())
