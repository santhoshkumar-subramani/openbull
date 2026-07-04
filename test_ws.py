import asyncio
import websockets

async def connect():
    uri = "ws://127.0.0.1:8000/ws/strategy/3"
    try:
        async with websockets.connect(uri) as websocket:
            print("Connected!")
            await asyncio.sleep(5)
            print("Done")
    except Exception as e:
        print(f"Failed: {e}")

asyncio.run(connect())
