"""
Shoonya (Finvasia / Noren) order API.

Covers place, modify, cancel, orderbook, tradebook, positions, and holdings.
All calls use the jData + jKey form-encoding convention.
"""

import json
import logging
import threading
import time

from backend.broker.shoonya.mapping.transform_data import (
    transform_data,
    transform_modify_order_data,
    map_product_type,
    reverse_map_product_type,
)
from backend.broker.upstox.mapping.order_data import (
    get_brsymbol_from_cache,
)
from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)

BASE_URL = "https://api.shoonya.com/NorenWClientAPI"

# --- Cache for position book to prevent rapid API polling ---
_position_cache: dict = {}
_position_cache_lock = threading.Lock()
_POSITION_CACHE_TTL = 1.0


def _get_cached_positions(auth: str) -> dict:
    with _position_cache_lock:
        now = time.monotonic()
        cached = _position_cache.get(auth)
        if cached and (now - cached["timestamp"]) < _POSITION_CACHE_TTL:
            return cached["data"]

    positions_data = get_positions(auth)
    with _position_cache_lock:
        _position_cache[auth] = {"data": positions_data, "timestamp": time.monotonic()}
    return positions_data


def _invalidate_position_cache(auth: str) -> None:
    with _position_cache_lock:
        _position_cache.pop(auth, None)


def _split_token(auth_token: str) -> tuple[str, str, str]:
    """Split combined ``userid:susertoken:actid``."""
    parts = auth_token.split(":") if auth_token else []
    if len(parts) >= 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], parts[1], parts[0]
    return "", auth_token or "", ""


def _post(endpoint: str, payload: dict, jkey: str) -> dict:
    """POST to Shoonya with jData + jKey form encoding."""
    payload_str = f"jData={json.dumps(payload)}&jKey={jkey}"
    client = get_httpx_client()
    response = client.post(
        f"{BASE_URL}/{endpoint}",
        content=payload_str,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        return response.json()
    except json.JSONDecodeError:
        logger.error("Shoonya non-JSON response on %s (status=%s): %s",
                     endpoint, response.status_code, response.text[:200])
        return {"stat": "Not_Ok", "emsg": f"Invalid JSON (HTTP {response.status_code})"}


def _post_raw(endpoint: str, payload: dict, jkey: str) -> tuple:
    """POST to Shoonya, returning both the raw response and parsed JSON dict/list."""
    payload_str = f"jData={json.dumps(payload)}&jKey={jkey}"
    client = get_httpx_client()
    response = client.post(
        f"{BASE_URL}/{endpoint}",
        content=payload_str,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        response_data = response.json()
    except json.JSONDecodeError:
        logger.error("Shoonya non-JSON response on %s (status=%s): %s",
                     endpoint, response.status_code, response.text[:200])
        response_data = {"stat": "Not_Ok", "emsg": f"Invalid JSON (HTTP {response.status_code})"}
    return response, response_data


# ---- Place Order ----

def place_order_api(data: dict, auth_token: str, config: dict | None = None) -> tuple:
    """Place an order on Shoonya.

    Returns:
        (response, response_data, order_id)
    """
    # Shoonya officially blocks MARKET orders for options/MCX. We intercept
    # and convert them to a LIMIT order with a 5% buffer from current LTP.
    exchange = str(data.get("exchange", ""))
    pricetype = str(data.get("pricetype", ""))
    
    if exchange in ("NFO", "BFO", "MCX") and pricetype == "MARKET":
        try:
            from backend.broker.shoonya.api.data import get_quotes
            from backend.services.market_data_cache import get_ltp_value
            from backend.broker.shoonya.mapping.transform_data import map_product_type
            
            symbol = str(data.get("symbol", ""))
            
            # 1. Try to get LTP from live websocket cache
            ltp = get_ltp_value(symbol, exchange) or 0.0
            price_source = "live websocket cache"
            
            # 2. Fallback to Shoonya REST API GetQuotes
            if ltp == 0.0:
                quote = get_quotes(symbol, exchange, auth_token, config)
                ltp = quote.get("ltp", 0.0)
                price_source = "Shoonya REST API GetQuotes"
                    
            # 3. Fallback to open position average price (if closing position)
            if ltp == 0.0:
                try:
                    shoonya_product = map_product_type(data.get("product", "MIS"))
                    positions_data = _get_cached_positions(auth_token)
                    if positions_data and positions_data.get("status") and positions_data.get("data"):
                        for pos in positions_data["data"]:
                            pos_sym = pos.get("tsym", "")
                            if pos_sym == symbol or (pos.get("exch") == exchange and symbol in pos_sym):
                                upldprc = float(pos.get("upldprc") or 0.0)
                                if upldprc > 0.0:
                                    ltp = upldprc
                                    price_source = "open position (upldprc)"
                                else:
                                    dayavgprc = float(pos.get("dayavgprc") or 0.0)
                                    if dayavgprc > 0.0:
                                        ltp = dayavgprc
                                        price_source = "open position (dayavgprc)"
                                    else:
                                        ltp = float(pos.get("netavgprc") or 0.0)
                                        price_source = "open position (netavgprc)"
                                break
                except Exception as e:
                    logger.warning("Failed to get average price for MARKET order fallback: %s", e)

            if ltp > 0:
                logger.info("MARKET to LIMIT conversion: LTP %.2f retrieved from %s for %s", ltp, price_source, symbol)
                action = str(data.get("action", "BUY")).upper()
                buffer_pct = 0.05
                if action == "BUY":
                    fallback_price = ltp * (1.0 + buffer_pct)
                else:
                    fallback_price = ltp * (1.0 - buffer_pct)
                
                # Round to nearest 0.05 tick size
                fallback_price = round(fallback_price / 0.05) * 0.05
                
                # Make a shallow copy so we don't mutate the caller's dict unexpectedly
                data = data.copy()
                data["pricetype"] = "LIMIT"
                data["price"] = round(fallback_price, 2)
                logger.info("Shoonya Market Order Fallback: %s %s converted to LIMIT at %.2f (LTP %.2f)", action, symbol, fallback_price, ltp)
            else:
                logger.error("Market to Limit Conversion failed for %s. LTP is 0.0.", symbol)
                return None, {
                    "status": "error", 
                    "message": f"Order Rejected: Failed to fetch valid LTP for MARKET to LIMIT conversion for {symbol}."
                }, None
        except Exception as e:
            logger.warning("Shoonya MARKET order fallback failed to get quote for %s: %s", data.get("symbol"), e)
            return None, {
                "status": "error", 
                "message": f"Order Rejected: Error during MARKET to LIMIT conversion for {symbol}."
            }, None

    uid, jkey, actid = _split_token(auth_token)

    payload = transform_data(data, "")
    payload["uid"] = uid
    payload["actid"] = actid

    response, result = _post_raw("PlaceOrder", payload, jkey)
    safe_payload = {k: v for k, v in payload.items() if k not in ("uid", "actid")}
    logger.info("Shoonya PlaceOrder response for payload %s: HTTP %s, Result: %s", safe_payload, response.status_code, result)

    # Inject status for service layer validation
    response.status = response.status_code

    if result.get("stat") == "Ok" and result.get("norenordno"):
        orderid = result["norenordno"]
        response_data = {"status": "success", "orderid": orderid}
    else:
        orderid = None
        if response.status == 200:
            response.status = 400
        response_data = {
            "status": "error",
            "message": result.get("emsg", "Order placement failed"),
        }

    _invalidate_position_cache(auth_token)
    return response, response_data, orderid


# ---- Place Smart Order ----

def place_smartorder_api(data: dict, auth_token: str) -> tuple:
    """Place a smart order by reconciling target position size with current."""
    try:
        symbol = data.get("symbol")
        exchange = data.get("exchange")
        product = data.get("product")

        if not all([symbol, exchange, product]):
            return None, {"status": "error", "message": "Missing symbol/exchange/product"}, None

        shoonya_product = map_product_type(product)

        position_size = int(data.get("position_size", "0"))
        current_position = int(
            get_open_position(symbol, exchange, shoonya_product, auth_token)
        )

        logger.info("position_size=%s, current=%s", position_size, current_position)

        if position_size == 0 and current_position == 0 and int(data.get("quantity", 0)) != 0:
            res, response, orderid = place_order_api(data, auth_token)
            _invalidate_position_cache(auth_token)
            return res, response, orderid

        if position_size == current_position:
            msg = "No action needed. Position size matches current position"
            if int(data.get("quantity", 0)) == 0:
                msg = "No OpenPosition Found. Not placing Exit order."
            return None, {"status": "success", "message": msg}, None

        if position_size == 0 and current_position > 0:
            action, quantity = "SELL", abs(current_position)
        elif position_size == 0 and current_position < 0:
            action, quantity = "BUY", abs(current_position)
        elif current_position == 0:
            action = "BUY" if position_size > 0 else "SELL"
            quantity = abs(position_size)
        else:
            if position_size > current_position:
                action, quantity = "BUY", position_size - current_position
            else:
                action, quantity = "SELL", current_position - position_size

        logger.info("place_smartorder_api computed action: %s, quantity: %s for %s", action, quantity, symbol)

        order_data = data.copy()
        order_data["action"] = action
        order_data["quantity"] = str(quantity)

        res, response, orderid = place_order_api(order_data, auth_token)
        _invalidate_position_cache(auth_token)
        return res, response, orderid

    except Exception as e:
        logger.exception("Error in place_smartorder_api")
        return None, {"status": "error", "message": str(e)}, None


# ---- Modify Order ----

def modify_order(data: dict, auth_token: str, config: dict | None = None) -> tuple[dict, int]:
    """Modify an existing order."""
    uid, jkey, actid = _split_token(auth_token)

    payload = transform_modify_order_data(data, "")
    payload["uid"] = uid
    payload["actid"] = actid

    response, result = _post_raw("ModifyOrder", payload, jkey)
    safe_payload = {k: v for k, v in payload.items() if k not in ("uid", "actid")}
    logger.info("Shoonya ModifyOrder response for payload %s: HTTP %s, Result: %s", safe_payload, response.status_code, result)

    if result.get("stat") == "Ok":
        orderid = result.get("result", data.get("orderid", ""))
        _invalidate_position_cache(auth_token)
        return {"status": "success", "orderid": orderid}, 200

    emsg = result.get("emsg", "Order modification failed")
    status_code = response.status_code if response.status_code != 200 else 400
    return {"status": "error", "message": emsg}, status_code


# ---- Cancel Order ----

def cancel_order(orderid: str, auth_token: str, config: dict | None = None) -> tuple[dict, int]:
    """Cancel an order."""
    uid, jkey, actid = _split_token(auth_token)

    payload = {"uid": uid, "actid": actid, "norenordno": orderid}
    response, result = _post_raw("CancelOrder", payload, jkey)
    logger.info("Shoonya CancelOrder response for order %s: HTTP %s, Result: %s", orderid, response.status_code, result)

    if result.get("stat") == "Ok":
        ret_orderid = result.get("result", orderid)
        _invalidate_position_cache(auth_token)
        return {"status": "success", "orderid": ret_orderid}, 200

    emsg = result.get("emsg", "Order cancellation failed")
    status_code = response.status_code if response.status_code != 200 else 400
    return {"status": "error", "message": emsg}, status_code


# ---- Close All Positions ----

def close_all_positions(current_api_key: str, auth_token: str) -> tuple[dict, int]:
    """Close all open positions by placing matching market orders."""
    positions_response = get_positions(auth_token)

    if not positions_response or positions_response.get("data") is None or not positions_response.get("data"):
        return {"message": "No Open Positions Found"}, 200

    if positions_response.get("status"):
        from backend.broker.shoonya.mapping.order_data import (
            _get_oa_symbol_from_token,
            _get_oa_symbol_from_brsymbol,
        )

        success_count = 0
        failure_count = 0
        last_error = ""

        for position in positions_response["data"]:
            try:
                netqty = int(position.get("netqty", 0) or 0)
            except (TypeError, ValueError):
                netqty = 0
            if netqty == 0:
                continue

            action = "SELL" if netqty > 0 else "BUY"
            quantity = abs(netqty)

            exchange = position.get("exch", "")
            token = position.get("token", "")
            brsymbol = position.get("tsym", "")

            symbol = None
            if token:
                symbol = _get_oa_symbol_from_token(token, exchange)
            if not symbol and brsymbol:
                symbol = _get_oa_symbol_from_brsymbol(brsymbol, exchange)
            if not symbol:
                symbol = brsymbol

            logger.info("Squaring off symbol: %s", symbol)

            place_order_payload = {
                "apikey": current_api_key,
                "strategy": "Squareoff",
                "symbol": symbol,
                "action": action,
                "exchange": exchange,
                "pricetype": "MARKET",
                "product": reverse_map_product_type(position.get("prd", "")),
                "quantity": str(quantity),
            }
            
            # place_order_api returns (res, response_data, order_id)
            res, response_data, order_id = place_order_api(place_order_payload, auth_token)
            
            if response_data and response_data.get("status") == "success":
                success_count += 1
                logger.info("Successfully squared off symbol: %s. Order ID: %s", symbol, order_id)
            else:
                failure_count += 1
                last_error = response_data.get("message", "Unknown error") if response_data else "Unknown error"
                logger.error("Failed to square off symbol: %s. Error: %s", symbol, last_error)

        _invalidate_position_cache(auth_token)

        if failure_count > 0:
            if success_count == 0:
                return {"status": "error", "message": f"Failed to close positions: {last_error}"}, 400
            else:
                return {"status": "error", "message": f"Partially squared off. {failure_count} failed. Last error: {last_error}"}, 207

    return {"status": "success", "message": "All Open Positions SquaredOff"}, 200


# ---- Cancel All Orders ----

def cancel_all_orders_api(data: dict, auth_token: str) -> tuple[list, list]:
    """Cancel all open or trigger-pending orders."""
    order_book_response = get_order_book(auth_token)
    if not order_book_response or order_book_response.get("status") is not True:
        return [], []

    orders_to_cancel = [
        order
        for order in order_book_response.get("data", []) or []
        if (order.get("status") or "").upper() in ("OPEN", "TRIGGER_PENDING", "PENDING")
    ]

    canceled_orders: list[str] = []
    failed_cancellations: list[str] = []
    for order in orders_to_cancel:
        orderid = order.get("norenordno")
        if not orderid:
            continue
        _, status_code = cancel_order(orderid, auth_token)
        if status_code == 200:
            canceled_orders.append(orderid)
        else:
            failed_cancellations.append(orderid)

    return canceled_orders, failed_cancellations


# ---- Get Open Position ----

def get_open_position(
    tradingsymbol: str, exchange: str, product: str, auth_token: str
) -> str:
    """Return the net quantity (as string) of an open position; '0' if none."""
    br_symbol = get_brsymbol_from_cache(tradingsymbol, exchange) or tradingsymbol
    positions_data = _get_cached_positions(auth_token)

    net_qty = 0
    if positions_data and positions_data.get("status") and positions_data.get("data"):
        for position in positions_data["data"]:
            if (
                position.get("tsym") == br_symbol
                and position.get("exch") == exchange
                and position.get("prd") == product
            ):
                try:
                    net_qty = int(position.get("netqty", 0) or 0)
                except (TypeError, ValueError):
                    net_qty = 0
                break
    return str(net_qty)


# ---- Order Book ----

def get_order_book(auth_token: str, config: dict | None = None) -> dict:
    """Fetch the order book."""
    uid, jkey, actid = _split_token(auth_token)
    payload = {"uid": uid}
    result = _post("OrderBook", payload, jkey)

    if isinstance(result, list):
        return {"status": True, "data": result}
    if isinstance(result, dict) and result.get("stat") == "Not_Ok":
        if "no data" in (result.get("emsg") or "").lower():
            return {"status": True, "data": []}
        return {"status": "error", "message": result.get("emsg", "")}
    return {"status": True, "data": []}


# ---- Trade Book ----

def get_trade_book(auth_token: str, config: dict | None = None) -> dict:
    """Fetch the trade book."""
    uid, jkey, actid = _split_token(auth_token)
    payload = {"uid": uid, "actid": actid}
    result = _post("TradeBook", payload, jkey)

    if isinstance(result, list):
        return {"status": True, "data": result}
    if isinstance(result, dict) and result.get("stat") == "Not_Ok":
        if "no data" in (result.get("emsg") or "").lower():
            return {"status": True, "data": []}
        return {"status": "error", "message": result.get("emsg", "")}
    return {"status": True, "data": []}


# ---- Positions ----

def get_positions(auth_token: str, config: dict | None = None) -> dict:
    """Fetch the position book."""
    uid, jkey, actid = _split_token(auth_token)
    payload = {"uid": uid, "actid": actid}
    result = _post("PositionBook", payload, jkey)

    if isinstance(result, list):
        return {"status": True, "data": result}
    if isinstance(result, dict) and result.get("stat") == "Not_Ok":
        if "no data" in (result.get("emsg") or "").lower():
            return {"status": True, "data": []}
        return {"status": "error", "message": result.get("emsg", "")}
    return {"status": True, "data": []}


# ---- Holdings ----

def get_holdings(auth_token: str, config: dict | None = None) -> dict:
    """Fetch holdings."""
    uid, jkey, actid = _split_token(auth_token)
    payload = {"uid": uid, "actid": actid, "prd": "C"}
    result = _post("Holdings", payload, jkey)

    if isinstance(result, list):
        return {"status": True, "data": result}
    if isinstance(result, dict) and result.get("stat") == "Not_Ok":
        if "no data" in (result.get("emsg") or "").lower():
            return {"status": True, "data": []}
        return {"status": "error", "message": result.get("emsg", "")}
    return {"status": True, "data": []}
