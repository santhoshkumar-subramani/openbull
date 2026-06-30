import asyncio
import logging
from backend.database import async_session
from sqlalchemy import text

async def check():
    async with async_session() as db:
        res = await db.execute(text("SELECT key, value FROM settings WHERE key='trading_mode'"))
        print("Settings table:", res.fetchall())
        
asyncio.run(check())
