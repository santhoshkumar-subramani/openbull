from datetime import datetime
from typing import List

from pydantic import BaseModel, ConfigDict


class PositionGroupMappingBase(BaseModel):
    symbol: str
    exchange: str
    product: str


class PositionGroupMappingCreate(PositionGroupMappingBase):
    pass


class PositionGroupMappingOut(PositionGroupMappingBase):
    id: int
    group_id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PositionGroupBase(BaseModel):
    name: str


class PositionGroupCreate(PositionGroupBase):
    pass


class PositionGroupOut(PositionGroupBase):
    id: int
    created_at: datetime
    updated_at: datetime
    mappings: List[PositionGroupMappingOut] = []

    model_config = ConfigDict(from_attributes=True)
