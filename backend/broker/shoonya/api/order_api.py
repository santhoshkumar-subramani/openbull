"""
Shoonya order API — place, modify, cancel orders and fetch order/trade/position/holdings data.

Key differences from Zerodha/Dhan:
  - Shoonya rejects raw MARKET / SL-M order types via API.
    transform_data() applies Market Price Protection (MPP) converting them to
    LMT / SL-LMT before submission.
  - All REST payloads use jData form-encoding with a Bearer token header.
  - Shoonya uses compact single-char keys: uid, actid, tsym, exch, qty, prc, etc.
"""

import json
import logging
import threading
import time

from backend.broker.shoonya.mapping.transform_data import (
    map_product_type,
    reverse_map_product_type,
    transform_data,
    transform_modify_order_data,
)
from backend.broker.upstox.mapping.order_data import (
    get_brsymbol_from_cache,
    get_symbol_from_brsymbol_cache,
)
from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.shoonya.com/NorenWClientAPI"

# --- Per-Symbol Smart Order Lock ---
_symbol_locks: dict[str, threading.Lock] = {}
_symbol_locks_lock = threading.Lock()

# --- Position Book Cache (1-second TTL) ---
_position_cache: dict[str, dict] = {}
_position_cache_lock = threading.Lock()
_POSITION_CACHE_TTL = 1.0


def _api_headers(auth_token: str) -> dict:
    return {
        "Content-Type": "text/plain",
        "Authorization": f"Bearer {auth_token}",
    }


def _post_jdata(endpoint: str, auth_token: str, payload: dict) -> dict:
    """POST to Shoonya endpoint with jData form encoding."""
    client = get_httpx_client()
    payload_str = "jData=" + json.dumps(payload)
    response = client.post(
        f"{_BASE_URL}{endpoint}",
        content=payload_str,
        headers=_api_headers(auth_token),
    )
    try:
        return response.json()
    except Exception as e:
        logger.error("JSON decode error from Shoonya %s: %s", endpoint, e)
        raise


def _get_symbol_lock(symbol: str, exchange: str, product: str) -> threading.Lock:
    key = f"{symbol}:{exchange}:{product}"
    with _symbol_locks_lock:
        if key not in _symbol_locks:
            _symbol_locks[key] = threading.Lock()
        return _symbol_locks[key]


def _get_cached_positions(auth_token: str, user_id: str) -> list | dict:
    with _position_cache_lock:
        now = time.monotonic()
        cached = _position_cache.get(auth_token)
        if cached and (now - cached["timestamp"]) < _POSITION_CACHE_TTL:
            return cached["data"]

    positions_data = get_positions(auth_token, {"client_id": user_id})

    with _position_cache_lock:
        _position_cache[auth_token] = {
            "data": positions_data,
            "timestamp": time.monotonic(),
        }
    return positions_data


def _invalidate_position_cache(auth_token: str) -> None:
    with _position_cache_lock:
        _position_cache.pop(auth_token, None)


# ---- Book endpoints ----

def get_order_book(auth_token: str, config: dict | None = None) -> dict | list:
    """Fetch the order book."""
    config = config or {}
    user_id = config.get("client_id", "")
    payload = {"uid": user_id, "actid": user_id}
    return _post_jdata("/OrderBook", auth_token, payload)


def get_trade_book(auth_token: str, config: dict | None = None) -> dict | list:
    """Fetch the trade book."""
    config = config or {}
    user_id = config.get("client_id", "")
    payload = {"uid": user_id, "actid": user_id}
    return _post_jdata("/TradeBook", auth_token, payload)


def get_positions(auth_token: str, config: dict | None = None) -> dict | list:
    """Fetch current positions (net)."""
    config = config or {}
    user_id = config.get("client_id", "")
    payload = {"uid": user_id, "actid": user_id}
    return _post_jdata("/PositionBook", auth_token, payload)


def get_holdings(auth_token: str, config: dict | None = None) -> dict | list:
    """Fetch demat holdings (CNC product)."""
    config = config or {}
    user_id = config.get("client_id", "")
    payload = {"uid": user_id, "actid": user_id, "prd": "C"}
    return _post_jdata("/Holdings", auth_token, payload)


# ---- Open position helper ----

def get_open_position(
    symbol: str, exchange: str, product: str, auth_token: str, config: dict
) -> str:
    """Return net quantity string for a position (or '0' if not found)."""
    brsymbol = get_brsymbol_from_cache(symbol, exchange) or symbol
    user_id = config.get("client_id", "")
    positions_data = _get_cached_positions(auth_token, user_id)

    if not positions_data or (
        isinstance(positions_data, dict) and positions_data.get("stat") == "Not_Ok"
    ):
        return "0"

    if isinstance(positions_data, list):
        for pos in positions_data:
            if (
                pos.get("tsym") == brsymbol
                and pos.get("exch") == exchange
                and pos.get("prd") == product
            ):
                return pos.get("netqty", "0")

    return "0"


# ---- Order placement ----

def place_order_api(data: dict, auth_token: str, config: dict | None = None) -> tuple:
    """Place an order via Shoonya. Returns (response, response_data, order_id)."""
    config = config or {}
    user_id = config.get("client_id", "")

    newdata = transform_data(data, auth_token, config)
    newdata["uid"] = user_id
    newdata["actid"] = user_id

    payload_str = "jData=" + json.dumps(newdata)
    headers = _api_headers(auth_token)

    client = get_httpx_client()
    response = client.post(
        f"{_BASE_URL}/PlaceOrder", content=payload_str, headers=headers
    )

    try:
        response_data = response.json()
    except Exception:
        response_data = {"stat": "Not_Ok", "emsg": "Invalid JSON from broker"}

    response.status = response.status_code

    if response_data.get("stat") == "Ok":
        order_id = response_data.get("norenordno")
    else:
        order_id = None
        logger.error(
            "Shoonya PlaceOrder error: %s", response_data.get("emsg", "Unknown error")
        )

    return response, response_data, order_id


def place_smartorder_api(
    data: dict, auth_token: str, config: dict | None = None
) -> tuple:
    """Smart order: compare desired position_size to current, place delta order."""
    config = config or {}
    res = None
    response_data = {
        "status": "error",
        "message": "No action required or invalid parameters",
    }
    order_id = None

    try:
        symbol = data.get("symbol")
        exchange = data.get("exchange")
        product = data.get("product")

        if not all([symbol, exchange, product]):
            return res, response_data, order_id

        symbol_lock = _get_symbol_lock(symbol, exchange, product)

        with symbol_lock:
            position_size = int(data.get("position_size", "0"))
            current_position = int(
                get_open_position(
                    symbol, exchange, map_product_type(product), auth_token, config
                )
            )

            action = None
            quantity = 0

            if position_size == 0 and current_position == 0:
                action = data.get("action", "BUY").upper()
                quantity = int(data.get("quantity", "0"))
            elif position_size == 0 and current_position > 0:
                action = "SELL"
                quantity = abs(current_position)
            elif position_size == 0 and current_position < 0:
                action = "BUY"
                quantity = abs(current_position)
            elif current_position == 0:
                action = "BUY" if position_size > 0 else "SELL"
                quantity = abs(position_size)
            else:
                if position_size > current_position:
                    action = "BUY"
                    quantity = position_size - current_position
                elif position_size < current_position:
                    action = "SELL"
                    quantity = current_position - position_size

            if action and quantity > 0:
                order_data = data.copy()
                order_data["action"] = action
                order_data["quantity"] = str(quantity)
                res, response_data, order_id = place_order_api(
                    order_data, auth_token, config
                )
                _invalidate_position_cache(auth_token)
                return res, response_data, order_id
            else:
                response_data = {
                    "status": "success",
                    "message": "No action needed. Position already matched.",
                }
                return res, response_data, order_id

    except Exception as e:
        logger.error("Error in Shoonya place_smartorder_api: %s", e)
        return res, {"status": "error", "message": str(e)}, order_id

    return res, response_data, order_id


# ---- Modify / Cancel ----

def modify_order(data: dict, auth_token: str, config: dict | None = None) -> tuple[dict, int]:
    """Modify an existing Shoonya order."""
    config = config or {}
    user_id = config.get("client_id", "")

    transformed = transform_modify_order_data(data)
    transformed["uid"] = user_id

    payload_str = "jData=" + json.dumps(transformed)
    headers = _api_headers(auth_token)

    client = get_httpx_client()
    response = client.post(
        f"{_BASE_URL}/ModifyOrder", content=payload_str, headers=headers
    )

    try:
        response_data = response.json()
    except Exception:
        return {"status": "error", "message": "Invalid JSON from broker"}, 500

    if response_data.get("stat") == "Ok":
        return {"status": "success", "orderid": data.get("orderid", "")}, 200
    return {
        "status": "error",
        "message": response_data.get("emsg", "Failed to modify order"),
    }, response.status_code


def cancel_order(
    order_id: str, auth_token: str, config: dict | None = None
) -> tuple[dict, int]:
    """Cancel a specific Shoonya order."""
    config = config or {}
    user_id = config.get("client_id", "")

    payload = {"uid": user_id, "norenordno": order_id}
    payload_str = "jData=" + json.dumps(payload)
    headers = _api_headers(auth_token)

    client = get_httpx_client()
    try:
        response = client.post(
            f"{_BASE_URL}/CancelOrder", content=payload_str, headers=headers
        )
        data = response.json()
    except Exception as e:
        return {"status": "error", "message": f"Cancel order failed: {e}"}, 500

    if data.get("stat") == "Ok":
        return {"status": "success", "orderid": order_id}, 200
    return {
        "status": "error",
        "message": data.get("emsg", "Failed to cancel order"),
    }, response.status_code
