import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def main():
    engine = create_async_engine("postgresql+asyncpg://postgres:123456@localhost:5432/openbull")
    async with engine.begin() as conn:
        res = await conn.execute(text("SELECT id, name, index_trigger, vix_condition FROM sm_strategy WHERE name = 'Sensex_Strategy_001'"))
        for row in res:
            print(f"ID: {row[0]}, NAME: {row[1]}")
            print(f"INDEX: {row[2]}")
            print(f"VIX: {row[3]}")
        
asyncio.run(main())
