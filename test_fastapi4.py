import asyncio
from httpx import AsyncClient
from backend.models.user import User
from backend.security import create_access_token

async def main():
    token = create_access_token(data={"sub": "1"})
    headers = {"Authorization": f"Bearer {token}"}
    
    async with AsyncClient(base_url="http://127.0.0.1:8000") as ac:
        response = await ac.get("/web/strategy/3/positions?run_id=7", headers=headers)
        print("Status:", response.status_code)
        print("Response:", response.json())

asyncio.run(main())
