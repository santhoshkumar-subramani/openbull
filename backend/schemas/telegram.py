from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class TelegramConfigBase(BaseModel):
    bot_token: str
    chat_id: str
    is_active: bool = False

class TelegramConfigCreate(TelegramConfigBase):
    pass

class TelegramConfigUpdate(BaseModel):
    bot_token: Optional[str] = None
    chat_id: Optional[str] = None
    is_active: Optional[bool] = None

class TelegramConfigResponse(TelegramConfigBase):
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
