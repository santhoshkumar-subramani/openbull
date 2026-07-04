import asyncio
from httpx import AsyncClient, ASGITransport
from backend.main import app
from backend.models.user import User

async def main():
    async def override_get_current_user():
        return User(id=1, username="test")
        
    from backend.dependencies import get_current_user
    app.dependency_overrides[get_current_user] = override_get_current_user
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/web/strategy/3/orders?run_id=7")
        print("Status:", response.status_code)
        print("Response:", response.json())

asyncio.run(main())
