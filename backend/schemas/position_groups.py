from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class PositionGroupUpdate(BaseModel):
    name: str


class PositionGroupRiskUpdate(BaseModel):
    stop_loss_enabled: bool = False
    stop_loss_mtm: Optional[float] = Field(default=None, gt=0)
    profit_target_enabled: bool = False
    profit_target_mtm: Optional[float] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_thresholds_when_enabled(self) -> "PositionGroupRiskUpdate":
        if self.stop_loss_enabled and self.stop_loss_mtm is None:
            raise ValueError("stop_loss_mtm is required when stop_loss_enabled=true")
        if self.profit_target_enabled and self.profit_target_mtm is None:
            raise ValueError("profit_target_mtm is required when profit_target_enabled=true")
        return self


class PositionGroupRiskState(BaseModel):
    stop_loss_enabled: bool
    stop_loss_mtm: Optional[float]
    profit_target_enabled: bool
    profit_target_mtm: Optional[float]

    risk_status: str
    risk_last_mtm: Optional[float]
    risk_last_trigger_reason: Optional[str]
    risk_last_triggered_at: Optional[datetime]
    risk_last_error: Optional[str]
    risk_retry_count: int
    risk_pending_symbols: List[str] = Field(default_factory=list)
    risk_force_close_requested: bool

    @field_validator("risk_pending_symbols", mode="before")
    @classmethod
    def _normalize_pending_symbols(cls, value):
        return value or []

    model_config = ConfigDict(from_attributes=True)


class PositionGroupOut(PositionGroupBase):
    id: int
    created_at: datetime
    updated_at: datetime
    stop_loss_enabled: bool
    stop_loss_mtm: Optional[float]
    profit_target_enabled: bool
    profit_target_mtm: Optional[float]
    risk_status: str
    risk_last_mtm: Optional[float]
    risk_last_trigger_reason: Optional[str]
    risk_last_triggered_at: Optional[datetime]
    risk_last_error: Optional[str]
    risk_retry_count: int
    risk_pending_symbols: List[str] = Field(default_factory=list)
    risk_force_close_requested: bool
    mappings: List[PositionGroupMappingOut] = []

    @field_validator("risk_pending_symbols", mode="before")
    @classmethod
    def _normalize_pending_symbols(cls, value):
        return value or []

    model_config = ConfigDict(from_attributes=True)
