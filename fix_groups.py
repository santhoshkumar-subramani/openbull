import asyncio
from backend.database import async_session
from sqlalchemy import select, update
from backend.models.position_groups import PositionGroup
async def run():
    async with async_session() as db:
        await db.execute(
            update(PositionGroup)
            .where(PositionGroup.risk_status == "succeeded")
            .values(risk_status="idle")
        )
        await db.commit()
        print("Updated")
asyncio.run(run())
