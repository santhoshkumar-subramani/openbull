import asyncio
from httpx import AsyncClient, ASGITransport
from backend.main import app
from backend.database import SessionLocal
from backend.models.user import User

async def main():
    # Mock user dependency
    async def override_get_current_user():
        return User(id=1, username="test")
        
    from backend.dependencies import get_current_user
    app.dependency_overrides[get_current_user] = override_get_current_user
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/strategies/3/positions?run_id=7")
        print(response.json())

asyncio.run(main())
