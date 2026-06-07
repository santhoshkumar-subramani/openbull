import asyncio
from sqlalchemy import text
from backend.database import async_session_factory

async def get_shoonya_key():
    async with async_session_factory() as session:
        result = await session.execute(text("SELECT api_key FROM broker_configs WHERE broker_name = 'shoonya'"))
        row = result.fetchone()
        if row:
            print("API_KEY:", row[0])
            return row[0]
        else:
            print("No Shoonya API key found.")
            return None

if __name__ == "__main__":
    asyncio.run(get_shoonya_key())
