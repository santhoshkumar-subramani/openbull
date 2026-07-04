import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from backend.config import get_settings
from backend.broker.shoonya.api.data import _post
from sqlalchemy import text


def fetch_quote_with_fallback(payload: dict, jkey: str) -> dict:
    res = _post("GetQuotesMF", payload, jkey)
    if res.get("stat") == "Ok" and str(res.get("token")) == str(payload.get("token")):
        return res
    return _post("GetQuotes", payload, jkey)

async def main():
    engine = create_async_engine(get_settings().database_url)
    async with engine.begin() as conn:
        res = await conn.execute(text("SELECT auth_token FROM sm_broker_connection WHERE user_id = 1 AND broker_name = 'Shoonya'"))
        row = res.fetchone()
        auth_token = row[0]
        
    uid = auth_token.split(':')[0]
    jkey = auth_token.split(':')[1]
    
    payload = {"uid": uid, "exch": "BFO", "token": "820396"}
    res = fetch_quote_with_fallback(payload, jkey)
    print("BFO 820396:", res)
    
    payload2 = {"uid": uid, "exch": "BSE", "token": "820396"}
    res2 = fetch_quote_with_fallback(payload2, jkey)
    print("BSE 820396:", res2)

asyncio.run(main())
