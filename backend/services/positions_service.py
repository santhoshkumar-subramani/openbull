"""
Positions service - fetches and transforms position book data.
Dual-entry pattern: get_positions_with_auth() + get_positions()
"""

import importlib
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _format_decimal(value):
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    return value


def _format_position_data(position_data):
    """Format numeric values in position data."""
    quantity_fields = {"quantity", "qty", "netqty", "net_qty", "buyqty", "sellqty"}

    if isinstance(position_data, list):
        return [
            {
                key: (int(value) if value == int(value) else value)
                if (key.lower() in quantity_fields and isinstance(value, (int, float)))
                else (_format_decimal(value) if isinstance(value, (int, float)) else value)
                for key, value in item.items()
            }
            for item in position_data
        ]
    return position_data


def _import_broker_modules(broker_name: str) -> dict[str, Any] | None:
    """Dynamically import broker-specific position modules."""
    try:
        api_module = importlib.import_module(f"backend.broker.{broker_name}.api.order_api")
        mapping_module = importlib.import_module(f"backend.broker.{broker_name}.mapping.order_data")
        return {
            "get_positions": api_module.get_positions,
            "map_position_data": mapping_module.map_position_data,
            "transform_positions_data": mapping_module.transform_positions_data,
        }
    except (ImportError, AttributeError) as error:
        logger.error("Error importing broker modules: %s", error)
        return None


def get_positions_with_auth(
    auth_token: str, broker: str, config: dict | None = None, user_id: int | None = None
) -> tuple[bool, dict[str, Any], int]:
    """Get positions using provided auth token.

    Returns:
        (success, response_data, http_status_code)
    """
    if user_id is not None:
        try:
            from backend.services.trading_mode_service import get_trading_mode_sync

            if get_trading_mode_sync() == "sandbox":
                from backend.services.sandbox_service import get_positions as sbx_pos

                return sbx_pos(user_id)
        except Exception:
            logger.exception("sandbox dispatch failed; falling back to live")

    broker_funcs = _import_broker_modules(broker)
    if broker_funcs is None:
        return False, {"status": "error", "message": "Broker-specific module not found"}, 404

    try:
        positions_data = broker_funcs["get_positions"](auth_token)
        logger.info(f"Raw positions_data: {positions_data}")

        if isinstance(positions_data, dict) and positions_data.get("status") == "error":
            return (
                False,
                {"status": "error", "message": positions_data.get("message", "Error fetching positions")},
                500,
            )

        positions_data = broker_funcs["map_position_data"](positions_data)
        positions_data = broker_funcs["transform_positions_data"](positions_data)

        formatted_positions = _format_position_data(positions_data)
        
        # Dispatch background task to diff positions and emit EventBus events
        if user_id is not None:
            try:
                import asyncio
                loop = asyncio.get_running_loop()
                loop.create_task(_async_diff_positions(user_id, formatted_positions))
            except RuntimeError:
                import backend.utils.global_loop as gl
                if gl.MAIN_LOOP is not None:
                    import asyncio
                    asyncio.run_coroutine_threadsafe(
                        _async_diff_positions(user_id, formatted_positions), gl.MAIN_LOOP
                    )
                
        return True, {"status": "success", "data": formatted_positions}, 200

    except Exception as e:
        logger.exception("Error processing positions data: %s", e)
        return False, {"status": "error", "message": str(e)}, 500


async def _async_diff_positions(user_id: int, current_positions: list):
    """
    Diffs the newly fetched live position book against the cached previous state.
    Emits PositionOpenedEvent and PositionClosedEvent for live manual trades.
    """
    try:
        from backend.utils.redis_client import cache_get_json, cache_set_json
        from backend.utils.event_bus import bus
        from backend.events.position_events import PositionOpenedEvent, PositionClosedEvent
        from backend.strategy.time_utils import format_ist, now_utc
        import math
        
        redis_key = f"live_positions:{user_id}"
        old_positions = await cache_get_json(redis_key)
        
        # Cache the fresh snapshot for next tick (1 day TTL)
        await cache_set_json(redis_key, current_positions, 86400)
        
        if old_positions is None:
            # First fetch of the day, do not spam alerts for existing positions
            return
            
        old_map = {p.get("symbol"): p for p in old_positions if p.get("symbol")}
        new_map = {p.get("symbol"): p for p in current_positions if p.get("symbol")}
        
        # Check for Opened positions
        for sym, new_p in new_map.items():
            new_qty = float(new_p.get("quantity") or new_p.get("netqty") or new_p.get("qty") or 0)
            if new_qty != 0:
                old_p = old_map.get(sym)
                old_qty = float(old_p.get("quantity") or old_p.get("netqty") or old_p.get("qty") or 0) if old_p else 0
                
                # Note: We can also trigger on partial adds, but let's stick to fresh opens
                if old_qty == 0:
                    logger.info(f"[Position Diff] Detected new open for {sym} (old_qty: {old_qty}, new_qty: {new_qty})")
                    action = "BUY" if new_qty > 0 else "SELL"
                    # Average entry price could be buyavg/sellavg depending on side
                    avg_price = new_p.get("average_price") or new_p.get("buyavg") if action == "BUY" else new_p.get("sellavg")
                    bus.publish(PositionOpenedEvent(
                        user_id=user_id,
                        position_data={
                            "action": action,
                            "symbol": sym,
                            "quantity": abs(new_qty),
                            "average_price": avg_price or 0.0,
                            "execution_time": format_ist(now_utc())
                        }
                    ))
                else:
                    logger.debug(f"[Position Diff] Unchanged/partial open for {sym} (old_qty: {old_qty}, new_qty: {new_qty})")

        # Check for Closed positions
        for sym, old_p in old_map.items():
            old_qty = float(old_p.get("quantity") or old_p.get("netqty") or old_p.get("qty") or 0)
            if old_qty != 0:
                new_p = new_map.get(sym)
                new_qty = float(new_p.get("quantity") or new_p.get("netqty") or new_p.get("qty") or 0) if new_p else 0
                
                # If quantity drops to exactly zero
                if new_qty == 0:
                    logger.info(f"[Position Diff] Detected full close for {sym} (old_qty: {old_qty}, new_qty: {new_qty})")
                    realized_pnl = 0.0
                    # Try to fetch realized PnL from the broker's API response directly
                    if new_p:
                        realized_pnl = new_p.get("realized_pnl") or new_p.get("rpnl") or new_p.get("realizedprofit") or 0.0
                    else:
                        # Fallback: estimate from the last known MTM before it vanished
                        realized_pnl = old_p.get("realized_pnl") or old_p.get("rpnl") or old_p.get("unrealized_pnl") or old_p.get("urmtm") or 0.0
                    
                    exit_price = 0.0
                    if new_p:
                        exit_price = new_p.get("sellavg") if old_qty > 0 else new_p.get("buyavg")
                        
                    bus.publish(PositionClosedEvent(
                        user_id=user_id,
                        position_data={
                            "symbol": sym,
                            "quantity": abs(old_qty),
                            "average_price": exit_price or old_p.get("ltp") or 0.0,
                            "realized_pnl": realized_pnl
                        }
                    ))
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("Failed to diff live positions for telegram alerts: %s", e)


def get_positions(
    api_key: str | None = None,
    auth_token: str | None = None,
    broker: str | None = None,
    config: dict | None = None,
    user_id: int | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Get positions. Supports both API-key and direct auth token calls."""
    if auth_token and broker:
        return get_positions_with_auth(auth_token, broker, config, user_id=user_id)

    return (
        False,
        {"status": "error", "message": "auth_token and broker must be provided"},
        400,
    )
