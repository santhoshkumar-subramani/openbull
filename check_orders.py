import asyncio
from backend.database import async_session
from sqlalchemy import text

async def check():
    async with async_session() as db:
        res = await db.execute(text("SELECT orderid, symbol, status, filled_quantity FROM sandbox_orders ORDER BY id DESC LIMIT 5"))
        print("Sandbox orders:", res.fetchall())
        
asyncio.run(check())
