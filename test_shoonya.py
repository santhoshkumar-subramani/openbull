import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from backend.config import get_settings
from backend.broker.shoonya.api.data import _post
from backend.security import get_fernet_key
from cryptography.fernet import Fernet

async def main():
    engine = create_async_engine(get_settings().database_url)
    async with engine.begin() as conn:
        res = await conn.execute(text("SELECT api_key, extra_config FROM broker_configs WHERE user_id = 1 AND broker_name = 'shoonya'"))
        row = res.fetchone()
        
    fernet = Fernet(get_fernet_key())
    api_key = fernet.decrypt(row[0].encode()).decode()
    extra_config = row[1]
    uid = extra_config.get("userid", "")
    
    # Wait, the auth_token format is uid:jkey:jkey2?
    # For Shoonya, the auth_token is actually stored in LiveAuth, which is in memory?
    # But wait, Shoonya doesn't use LiveAuth in the DB?
    print("UID:", uid)

asyncio.run(main())
