from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from backend.models.user import User
from backend.dependencies import get_db, get_current_user
from backend.models.position_groups import PositionGroup, PositionGroupMapping
from backend.schemas.position_groups import (
    PositionGroupCreate,
    PositionGroupUpdate,
    PositionGroupOut,
    PositionGroupRiskState,
    PositionGroupRiskUpdate,
    PositionGroupMappingCreate,
    PositionGroupMappingOut,
)

router = APIRouter(prefix="/web/position-groups", tags=["position_groups"])


@router.get("", response_model=List[PositionGroupOut])
async def get_position_groups(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """Get all position groups and their mappings for the current user."""
    result = await db.execute(
        select(PositionGroup)
        .options(selectinload(PositionGroup.mappings))
        .where(PositionGroup.user_id == current_user.id)
        .order_by(PositionGroup.id)
    )
    groups = result.scalars().all()
    return groups


@router.post("", response_model=PositionGroupOut)
async def create_position_group(
    group_in: PositionGroupCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new position group."""
    new_group = PositionGroup(user_id=current_user.id, name=group_in.name)
    db.add(new_group)
    await db.commit()
    await db.refresh(new_group)
    
    result = await db.execute(
        select(PositionGroup)
        .options(selectinload(PositionGroup.mappings))
        .where(PositionGroup.id == new_group.id)
    )
    return result.scalar_one()


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_position_group(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a position group and its mappings."""
    result = await db.execute(
        select(PositionGroup).where(
            PositionGroup.id == group_id, PositionGroup.user_id == current_user.id
        )
    )
    group = result.scalar_one_or_none()
    
    if not group:
        raise HTTPException(status_code=404, detail="Position group not found")

    await db.delete(group)
    await db.commit()
    return None


@router.patch("/{group_id}", response_model=PositionGroupOut)
async def update_position_group(
    group_id: int,
    group_in: PositionGroupUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a position group."""
    result = await db.execute(
        select(PositionGroup)
        .options(selectinload(PositionGroup.mappings))
        .where(
            PositionGroup.id == group_id, PositionGroup.user_id == current_user.id
        )
    )
    group = result.scalar_one_or_none()
    
    if not group:
        raise HTTPException(status_code=404, detail="Position group not found")

    group.name = group_in.name
    await db.commit()
    await db.refresh(group)
    return group


@router.post("/{group_id}/positions", response_model=PositionGroupMappingOut)
async def assign_position_to_group(
    group_id: int,
    mapping_in: PositionGroupMappingCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Assign a position to a group."""
    result = await db.execute(
        select(PositionGroup).where(
            PositionGroup.id == group_id, PositionGroup.user_id == current_user.id
        )
    )
    group = result.scalar_one_or_none()
    
    if not group:
        raise HTTPException(status_code=404, detail="Position group not found")

    # A position can only belong to one group at a time. Remove it from any existing group first.
    existing_result = await db.execute(
        select(PositionGroupMapping).where(
            PositionGroupMapping.user_id == current_user.id,
            PositionGroupMapping.symbol == mapping_in.symbol,
            PositionGroupMapping.exchange == mapping_in.exchange,
            PositionGroupMapping.product == mapping_in.product,
        )
    )
    existing_mapping = existing_result.scalar_one_or_none()
    if existing_mapping:
        await db.delete(existing_mapping)

    new_mapping = PositionGroupMapping(
        user_id=current_user.id,
        group_id=group_id,
        symbol=mapping_in.symbol,
        exchange=mapping_in.exchange,
        product=mapping_in.product,
    )
    db.add(new_mapping)
    try:
        await db.commit()
        await db.refresh(new_mapping)
        return new_mapping
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=400, detail="Could not assign position to group."
        )


@router.post("/unassign", status_code=status.HTTP_204_NO_CONTENT)
async def unassign_position(
    mapping_in: PositionGroupMappingCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Unassign a position from its group."""
    result = await db.execute(
        select(PositionGroupMapping).where(
            PositionGroupMapping.user_id == current_user.id,
            PositionGroupMapping.symbol == mapping_in.symbol,
            PositionGroupMapping.exchange == mapping_in.exchange,
            PositionGroupMapping.product == mapping_in.product,
        )
    )
    mapping = result.scalar_one_or_none()
    
    if mapping:
        await db.delete(mapping)
        await db.commit()
    return None


@router.patch("/{group_id}/risk", response_model=PositionGroupRiskState)
async def update_position_group_risk(
    group_id: int,
    payload: PositionGroupRiskUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update optional stop-loss / profit-booking settings for one group."""
    result = await db.execute(
        select(PositionGroup).where(
            PositionGroup.id == group_id,
            PositionGroup.user_id == current_user.id,
        )
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Position group not found")

    group.stop_loss_enabled = bool(payload.stop_loss_enabled)
    group.stop_loss_mtm = payload.stop_loss_mtm if payload.stop_loss_enabled else None
    group.profit_target_enabled = bool(payload.profit_target_enabled)
    group.profit_target_mtm = payload.profit_target_mtm if payload.profit_target_enabled else None

    # Reset stale failure state whenever user saves risk settings.
    if group.risk_status in {"failed", "succeeded"}:
        group.risk_status = "idle"
    group.risk_last_error = None
    group.risk_retry_count = 0
    group.risk_pending_symbols = []
    group.risk_force_close_requested = False

    await db.commit()
    await db.refresh(group)
    return PositionGroupRiskState.model_validate(group)


@router.post("/{group_id}/close-now", response_model=PositionGroupRiskState)
async def close_group_now(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Request immediate high-priority close for this group's mapped positions."""
    result = await db.execute(
        select(PositionGroup).where(
            PositionGroup.id == group_id,
            PositionGroup.user_id == current_user.id,
        )
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Position group not found")

    group.risk_force_close_requested = True
    if group.risk_status in {"idle", "monitoring", "failed", "succeeded"}:
        group.risk_status = "triggered"
    group.risk_last_trigger_reason = "manual"
    group.risk_last_error = None
    group.risk_retry_count = 0
    group.risk_pending_symbols = []

    await db.commit()
    await db.refresh(group)
    return PositionGroupRiskState.model_validate(group)
