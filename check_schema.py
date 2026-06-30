import asyncio
from backend.database import async_session
from sqlalchemy import text

async def check():
    async with async_session() as db:
        res = await db.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'sm_auth_tokens'"))
        print(res.fetchall())
        
asyncio.run(check())
