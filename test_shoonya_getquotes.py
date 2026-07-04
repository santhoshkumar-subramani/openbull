import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from backend.config import get_settings
from backend.strategy.live_auth import resolve_live_auth
from backend.broker.shoonya.api.data import _post


def fetch_quote_with_fallback(payload: dict, jkey: str) -> dict:
    res = _post("GetQuotesMF", payload, jkey)
    if res.get("stat") == "Ok" and str(res.get("token")) == str(payload.get("token")):
        return res
    return _post("GetQuotes", payload, jkey)

async def main():
    engine = create_async_engine(get_settings().database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession)
    async with session_factory() as db:
        # Assuming user_id=1 for the owner
        auth_ctx = await resolve_live_auth(db, user_id=1, broker="Shoonya")
        if not auth_ctx:
            print("No auth context")
            return
            
        uid = auth_ctx.auth_token.split(':')[0]
        jkey = auth_ctx.auth_token.split(':')[1]
        
        payload = {"uid": uid, "exch": "BFO", "token": "820396"}
        res = fetch_quote_with_fallback(payload, jkey)
        print("BFO 820396:", res)

asyncio.run(main())
