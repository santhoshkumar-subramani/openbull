import asyncio
from backend.utils.redis_client import redis_client
import json

async def main():
    await redis_client.init()
    state = await redis_client.get_json("run:7:state")
    print(json.dumps(state, indent=2))
        
asyncio.run(main())
