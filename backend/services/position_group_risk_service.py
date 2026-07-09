"""Grouped-position risk monitor.

Evaluates optional group-level stop-loss/profit-booking thresholds and closes
only mapped positions when a threshold (or manual close request) triggers.

Design goals:
- One positions fetch per user per cycle.
- Group-scoped liquidation only.
- Sell-first close ordering to reduce margin pressure.
- Retries every monitor cycle (2-3s) until all mapped legs are flat.
- Auto-disable risk controls only after complete flatten confirmation.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.database import async_session
from backend.models.position_groups import PositionGroup
from backend.services.order_service import place_order_with_auth
from backend.services.positions_service import get_positions_with_auth
from backend.services.trading_mode_service import get_trading_mode
from backend.services.market_data_cache import get_ltp_value, is_data_fresh
from backend.strategy.live_auth import resolve_live_auth
from backend.utils.redis_client import KEY_PREFIX, get_redis

logger = logging.getLogger(__name__)

_POLL_SECONDS = 0.5
_REST_SYNC_SECONDS = 10.0
_MAX_RETRIES = 20
_ORDER_DEDUPE_TTL_SECONDS = 8
_GROUP_CYCLE_LOCK_TTL_SECONDS = 2

_task: Optional[asyncio.Task] = None
_running: bool = False
_cached_positions: dict[int, dict[str, dict[str, Any]]] = {}
_last_rest_sync: dict[int, float] = {}


def _position_key(symbol: str, exchange: str, product: str) -> str:
    return f"{symbol}-{exchange}-{product}"


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _close_action(quantity: int) -> str:
    return "SELL" if quantity > 0 else "BUY"


def _is_risk_enabled(group: PositionGroup) -> bool:
    return (
        (group.stop_loss_enabled and group.stop_loss_mtm is not None)
        or (group.profit_target_enabled and group.profit_target_mtm is not None)
    )


async def _acquire_slot(key: str, ttl_seconds: int) -> bool:
    """Acquire a short-lived Redis NX lock slot. Fail-open on Redis errors."""
    try:
        full_key = f"{KEY_PREFIX}{key}"
        created = await get_redis().set(full_key, "1", ex=ttl_seconds, nx=True)
        return bool(created)
    except Exception:
        # Fail-open: closing positions is safer than blocking on cache failures.
        return True


def start() -> None:
    """Start background monitor task."""
    global _task, _running
    if _running:
        return
    _running = True
    _task = asyncio.create_task(_loop(), name="position-group-risk-monitor")


async def stop() -> None:
    """Stop background monitor task."""
    global _task, _running
    _running = False
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Error stopping position-group-risk monitor")
    _task = None


async def _loop() -> None:
    logger.info("Position-group risk monitor started")
    try:
        while _running:
            try:
                await _scan_once()
            except Exception:
                logger.exception("Position-group risk monitor cycle failed")
            await asyncio.sleep(_POLL_SECONDS)
    finally:
        logger.info("Position-group risk monitor stopped")


async def _scan_once() -> None:
    async with async_session() as db:
        groups = await _load_candidate_groups(db)
        if not groups:
            return

        mode = await get_trading_mode(db)
        # End read transaction before network I/O begins.
        await db.commit()
        by_user: dict[int, list[PositionGroup]] = {}
        for group in groups:
            by_user.setdefault(group.user_id, []).append(group)

        for user_id, user_groups in by_user.items():
            await _process_user_groups(db, mode, user_id, user_groups)

        await db.commit()


async def _load_candidate_groups(db: AsyncSession) -> list[PositionGroup]:
    stmt = (
        select(PositionGroup)
        .options(selectinload(PositionGroup.mappings))
        .where(
            or_(
                and_(
                    PositionGroup.stop_loss_enabled == True,  # noqa: E712
                    PositionGroup.stop_loss_mtm.is_not(None),
                ),
                and_(
                    PositionGroup.profit_target_enabled == True,  # noqa: E712
                    PositionGroup.profit_target_mtm.is_not(None),
                ),
                PositionGroup.risk_force_close_requested == True,  # noqa: E712
                PositionGroup.risk_status == "closing",
            )
        )
    )
    return list((await db.execute(stmt)).scalars().all())


async def _process_user_groups(
    db: AsyncSession,
    mode: str,
    user_id: int,
    groups: list[PositionGroup],
) -> None:
    auth_token = ""
    broker = ""
    config: Optional[dict[str, Any]] = None

    if mode != "sandbox":
        ctx = await resolve_live_auth(db, user_id=user_id)
        if ctx is None:
            for group in groups:
                if group.risk_status in {"closing", "triggered"} or group.risk_force_close_requested:
                    _mark_group_failed(
                        group,
                        "No active broker auth session. Re-authenticate and retry.",
                    )
            return
        auth_token = ctx.auth_token
        broker = ctx.broker
        config = ctx.config
        # End auth-resolution transaction before broker network calls.
        await db.commit()

    import time
    now = time.time()
    last_sync = _last_rest_sync.get(user_id, 0)
    needs_sync = (now - last_sync) >= _REST_SYNC_SECONDS

    # Force immediate REST sync if any group is actively closing or manual close was requested
    if any(g.risk_status == "closing" or g.risk_force_close_requested for g in groups):
        needs_sync = True

    if needs_sync:
        logger.info(f"Performing REST position sync for user {user_id} (last_sync: {now - last_sync:.1f}s ago)")
        ok, payload, _status = await run_in_threadpool(
            get_positions_with_auth,
            auth_token,
            broker,
            config,
            user_id,
        )
        if not ok:
            _last_rest_sync[user_id] = now
            message = payload.get("message", "Failed to fetch positions") if isinstance(payload, dict) else "Failed to fetch positions"
            for group in groups:
                if group.risk_status == "closing" or group.risk_force_close_requested:
                    _mark_group_failed(group, message)
            return

        positions = payload.get("data") if isinstance(payload, dict) else []
        if not isinstance(positions, list):
            positions = []

        position_map: dict[str, dict[str, Any]] = {}
        for pos in positions:
            key = _position_key(
                str(pos.get("symbol", "")),
                str(pos.get("exchange", "")),
                str(pos.get("product", "")),
            )
            if not key or key == "--":
                continue
            position_map[key] = pos
            
        _cached_positions[user_id] = position_map
        _last_rest_sync[user_id] = now

    position_map = _cached_positions.get(user_id, {})
    ws_healthy = is_data_fresh(max_age_seconds=5.0)
    if not ws_healthy:
        logger.debug(f"WebSocket data is stale/unhealthy for user {user_id}. Falling back to REST P&L.")

    for group in groups:
        await _process_group(db, group, user_id, auth_token, broker, config, position_map, ws_healthy)


def _calculate_live_pnl(pos: dict[str, Any], symbol: str, exchange: str, ws_healthy: bool) -> float:
    rest_pnl = _as_float(pos.get("pnl"), 0.0)
    qty = _as_float(pos.get("quantity"), 0.0)
    
    if qty == 0 or not ws_healthy:
        return rest_pnl
        
    ltp = get_ltp_value(symbol, exchange)
    if ltp is None or ltp <= 0:
        return rest_pnl
        
    avg_price = _as_float(pos.get("average_price"), 0.0)
    if avg_price <= 0:
        return rest_pnl
        
    realized_pnl = _as_float(pos.get("realized_pnl"), 0.0)
    lot_size = _as_float(pos.get("lot_size") or pos.get("multiplier") or pos.get("ls"), 1.0)
    
    if qty > 0:
        unrealized = (ltp - avg_price) * qty * lot_size
    else:
        unrealized = (avg_price - ltp) * abs(qty) * lot_size
        
    live_pnl = realized_pnl + unrealized
    logger.debug(f"Live PnL for {symbol}: {live_pnl:.2f} (LTP: {ltp}, Avg: {avg_price}, Qty: {qty}, Realized: {realized_pnl})")
    return live_pnl


async def _process_group(
    db: AsyncSession,
    group: PositionGroup,
    user_id: int,
    auth_token: str,
    broker: str,
    config: Optional[dict[str, Any]],
    position_map: dict[str, dict[str, Any]],
    ws_healthy: bool,
) -> None:
    mapped_positions: list[dict[str, Any]] = []
    group_mtm = 0.0
    
    for mapping in group.mappings:
        key = _position_key(mapping.symbol, mapping.exchange, mapping.product)
        pos = position_map.get(key)
        if pos is not None:
            mapped_positions.append(pos)
            group_mtm += _calculate_live_pnl(pos, mapping.symbol, mapping.exchange, ws_healthy)

    group.risk_last_mtm = group_mtm

    # If the group has no active positions (qty != 0) but risk controls are still active,
    # it means the positions were closed (e.g. manually or via slipped auto-close).
    # We should immediately mark the group as succeeded and disable the triggers.
    active_positions = [p for p in mapped_positions if int(_as_float(p.get("quantity"), 0.0)) != 0]
    if _is_risk_enabled(group) and len(mapped_positions) > 0 and not active_positions:
        group.risk_status = "succeeded"
        group.risk_last_error = None
        group.risk_pending_symbols = []
        group.risk_retry_count = 0
        group.risk_force_close_requested = False
        group.stop_loss_enabled = False
        group.profit_target_enabled = False
        return

    if group.risk_status == "closing":
        await _attempt_close_cycle(
            group,
            user_id=user_id,
            auth_token=auth_token,
            broker=broker,
            config=config,
            mapped_positions=mapped_positions,
        )
        return

    trigger_reason: Optional[str] = None
    if group.risk_force_close_requested:
        trigger_reason = "manual"
    elif group.stop_loss_enabled and group.stop_loss_mtm is not None:
        sl = abs(_as_float(group.stop_loss_mtm))
        if group_mtm <= -sl:
            trigger_reason = "stop_loss"
    if trigger_reason is None and group.profit_target_enabled and group.profit_target_mtm is not None:
        target = _as_float(group.profit_target_mtm)
        if group_mtm >= target:
            trigger_reason = "profit_target"

    if trigger_reason is None:
        if _is_risk_enabled(group):
            if group.risk_status != "monitoring":
                group.risk_status = "monitoring"
                group.risk_last_error = None
                group.risk_retry_count = 0
        elif group.risk_status not in {"failed", "succeeded"}:
            group.risk_status = "idle"
            group.risk_last_error = None
            group.risk_retry_count = 0
        group.risk_pending_symbols = []
        return

    # Claim close cycle if not already in progress.
    claimed = await _claim_closing(group.id, user_id)
    if not claimed:
        return

    group.risk_status = "closing"
    group.risk_last_trigger_reason = trigger_reason
    group.risk_last_triggered_at = datetime.now(timezone.utc)
    group.risk_last_mtm = group_mtm
    group.risk_retry_count = 0
    group.risk_last_error = None
    group.risk_pending_symbols = []
    group.risk_force_close_requested = False
    await _attempt_close_cycle(
        group,
        user_id=user_id,
        auth_token=auth_token,
        broker=broker,
        config=config,
        mapped_positions=mapped_positions,
    )


async def _claim_closing(group_id: int, user_id: int) -> bool:
    """Acquire per-group trigger claim across workers using Redis NX."""
    return await _acquire_slot(
        f"group-risk-claim:{user_id}:{group_id}",
        ttl_seconds=10,
    )


async def _attempt_close_cycle(
    group: PositionGroup,
    *,
    user_id: int,
    auth_token: str,
    broker: str,
    config: Optional[dict[str, Any]],
    mapped_positions: list[dict[str, Any]],
) -> None:
    cycle_lock = await _acquire_slot(
        f"group-risk-cycle:{user_id}:{group.id}",
        ttl_seconds=_GROUP_CYCLE_LOCK_TTL_SECONDS,
    )
    if not cycle_lock:
        return

    # Fully flat: finish and disable risk controls.
    active_positions = [p for p in mapped_positions if int(_as_float(p.get("quantity"), 0.0)) != 0]
    if len(mapped_positions) > 0 and not active_positions:
        group.risk_status = "succeeded"
        group.risk_last_error = None
        group.risk_pending_symbols = []
        group.risk_retry_count = 0
        group.risk_force_close_requested = False
        group.stop_loss_enabled = False
        group.profit_target_enabled = False
        return

    retries = int(group.risk_retry_count or 0)
    if retries >= _MAX_RETRIES:
        pending = [
            _position_key(str(p.get("symbol", "")), str(p.get("exchange", "")), str(p.get("product", "")))
            for p in active_positions
        ]
        _mark_group_failed(
            group,
            f"Auto-close retries exhausted ({_MAX_RETRIES}).",
            pending_symbols=pending,
        )
        return

    # Close shorts first (quantity < 0), then longs.
    ordered = sorted(active_positions, key=lambda p: 0 if int(_as_float(p.get("quantity"), 0.0)) < 0 else 1)

    attempt_failures = 0
    pending_symbols: list[str] = []
    group.risk_last_error = None
    placed_any_order = False
    
    for pos in ordered:
        symbol = str(pos.get("symbol", ""))
        exchange = str(pos.get("exchange", ""))
        product = str(pos.get("product", ""))
        qty = int(_as_float(pos.get("quantity"), 0.0))
        if not symbol or not exchange or not product or qty == 0:
            continue

        key = _position_key(symbol, exchange, product)
        pending_symbols.append(key)

        # Skip duplicate close orders within a short TTL window.
        close_slot = await _acquire_slot(
            f"group-risk-close:{user_id}:{group.id}:{key}",
            ttl_seconds=_ORDER_DEDUPE_TTL_SECONDS,
        )
        if not close_slot:
            continue
            
        placed_any_order = True

        action = _close_action(qty)
        order_data = {
            "symbol": symbol,
            "exchange": exchange,
            "action": action,
            "quantity": str(abs(qty)),
            "pricetype": "MARKET",
            "product": product,
            "price": "0",
            "trigger_price": "0",
            "strategy": f"Group Auto Close: {group.name}",
        }

        ok, response, _status = await run_in_threadpool(
            place_order_with_auth,
            order_data,
            auth_token,
            broker,
            config,
            user_id,
        )
        if not ok:
            attempt_failures += 1
            msg = response.get("message") if isinstance(response, dict) else "Order rejected"
            logger.warning(
                "Group auto-close rejected user=%s group=%s symbol=%s: %s",
                user_id,
                group.id,
                symbol,
                msg,
            )

    group.risk_status = "closing"
    if placed_any_order:
        group.risk_retry_count = retries + 1
    group.risk_pending_symbols = pending_symbols
    group.risk_force_close_requested = False
    if attempt_failures > 0:
        group.risk_last_error = f"{attempt_failures} close order(s) rejected on attempt {retries + 1}."


def _mark_group_failed(
    group: PositionGroup,
    message: str,
    pending_symbols: Optional[list[str]] = None,
) -> None:
    group.risk_status = "failed"
    group.risk_last_error = message
    group.risk_force_close_requested = False
    if pending_symbols is not None:
        group.risk_pending_symbols = pending_symbols
