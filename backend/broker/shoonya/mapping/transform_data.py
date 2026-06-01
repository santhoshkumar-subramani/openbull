"""
Shoonya order request transformation.

Converts OpenBull-format order dicts to Shoonya API request format.
Applies Market Price Protection (MPP) for MARKET and SL-M order types,
which Shoonya's API rejects if submitted verbatim.
"""

import logging

from backend.broker.upstox.mapping.order_data import (
    get_brsymbol_from_cache,
    get_token_from_cache,
)

logger = logging.getLogger(__name__)

# ---- Mapping tables ----

_PRODUCT_MAP = {
    "CNC": "C",
    "MIS": "I",
    "NRML": "M",
}

_ORDER_TYPE_MAP = {
    "LIMIT": "LMT",
    "MARKET": "MKT",
    "SL": "SL-LMT",
    "SL-M": "SL-MKT",
}

_REVERSE_PRODUCT_MAP = {v: k for k, v in _PRODUCT_MAP.items()}


def map_product_type(product: str) -> str:
    """OpenBull product → Shoonya single-char product code."""
    return _PRODUCT_MAP.get(product.upper(), product)


def reverse_map_product_type(product: str) -> str:
    """Shoonya product code → OpenBull product string."""
    return _REVERSE_PRODUCT_MAP.get(product, product)


def map_order_type(order_type: str) -> str:
    """OpenBull order type → Shoonya prctyp string."""
    return _ORDER_TYPE_MAP.get(order_type.upper(), order_type)


# ---- MPP (Market Price Protection) ----

def _calculate_protected_price(ltp: float, action: str, symbol: str) -> float:
    """Calculate MPP limit price for a MARKET or SL-M order.

    Shoonya's API rejects raw MARKET / SL-M submissions.  The workaround is
    to send LMT / SL-LMT with a price buffered away from the LTP so the order
    fills immediately while staying within the risk controls.

    Slabs (percentage buffer):
        EQ / FUT:      LTP <  100  → 2 %
                  100 ≤ LTP < 500  → 1 %
                       LTP ≥ 500  → 0.5 %

        OPT (CE/PE):   LTP <  10  → 5 %
                  10  ≤ LTP < 100  → 3 %
                  100 ≤ LTP < 500  → 2 %
                       LTP ≥ 500  → 1 %
    """
    upper = symbol.upper() if symbol else ""
    is_option = upper.endswith("CE") or upper.endswith("PE")

    if is_option:
        if ltp < 10:
            pct = 0.05
        elif ltp < 100:
            pct = 0.03
        elif ltp < 500:
            pct = 0.02
        else:
            pct = 0.01
    else:
        if ltp < 100:
            pct = 0.02
        elif ltp < 500:
            pct = 0.01
        else:
            pct = 0.005

    buffer = ltp * pct
    if action.upper() == "BUY":
        return round(ltp + buffer, 2)
    return round(ltp - buffer, 2)


def _apply_mpp(data: dict, shoonya_order: dict) -> dict:
    """Apply MPP to convert MARKET → LMT and SL-MKT → SL-LMT in place."""
    prctyp = shoonya_order.get("prctyp", "LMT")
    if prctyp not in ("MKT", "SL-MKT"):
        return shoonya_order

    action = data.get("action", "BUY").upper()
    symbol = data.get("symbol", "")

    # Attempt to read LTP from market data; fall back to provided price
    ltp_str = data.get("ltp", data.get("price", "0"))
    try:
        ltp = float(ltp_str)
    except (ValueError, TypeError):
        ltp = 0.0

    if ltp <= 0:
        logger.warning(
            "MPP: LTP is zero or missing for %s. Using price field as LTP.", symbol
        )
        try:
            ltp = float(data.get("price", "0") or "0")
        except (ValueError, TypeError):
            ltp = 0.0

    if ltp > 0:
        protected = _calculate_protected_price(ltp, action, symbol)
        if prctyp == "MKT":
            shoonya_order["prctyp"] = "LMT"
            shoonya_order["prc"] = str(protected)
        else:  # SL-MKT → SL-LMT
            shoonya_order["prctyp"] = "SL-LMT"
            shoonya_order["prc"] = str(protected)
    else:
        # Last resort: convert to LMT with prc=0 (may still be rejected)
        shoonya_order["prctyp"] = "LMT"
        if not shoonya_order.get("prc") or shoonya_order["prc"] == "0":
            logger.error(
                "MPP: Cannot compute protected price for %s — LTP is zero.", symbol
            )

    return shoonya_order


# ---- Main transformation ----

def transform_data(data: dict, auth_token: str | None = None, config: dict | None = None) -> dict:
    """Convert an OpenBull order dict to a Shoonya PlaceOrder payload.

    The returned dict does NOT include uid / actid — the caller (order_api)
    adds those from config before serialising.
    """
    symbol = data.get("symbol", "")
    exchange = data.get("exchange", "NSE")
    action = data.get("action", "BUY").upper()
    quantity = str(data.get("quantity", "0"))
    price = str(data.get("price", "0"))
    trigger_price = str(data.get("trigger_price", "0"))
    product = data.get("product", "MIS")
    order_type = data.get("order_type", "MARKET")
    disclosed_qty = str(data.get("disclosed_quantity", "0"))

    # Resolve broker symbol and token from cache
    brsymbol = get_brsymbol_from_cache(symbol, exchange) or symbol
    token = get_token_from_cache(symbol, exchange) or ""

    # Map index exchanges to their underlying exchange for Shoonya
    shoonya_exchange = "NSE" if exchange == "NSE_INDEX" else (
        "BSE" if exchange == "BSE_INDEX" else exchange
    )

    shoonya_order = {
        "exch": shoonya_exchange,
        "tsym": brsymbol,
        "qty": quantity,
        "prc": price,
        "trgprc": trigger_price if trigger_price != "0" else "0",
        "dscqty": disclosed_qty,
        "prd": map_product_type(product),
        "trantype": "B" if action == "BUY" else "S",
        "prctyp": map_order_type(order_type),
        "ret": "DAY",
        "ordersource": "API",
    }

    # Apply MPP if needed
    shoonya_order = _apply_mpp(data, shoonya_order)

    return shoonya_order


def transform_modify_order_data(data: dict) -> dict:
    """Convert an OpenBull modify-order dict to Shoonya ModifyOrder payload.

    Returns a dict without uid (caller adds it).
    """
    order_type = data.get("order_type", "LIMIT")
    prctyp = map_order_type(order_type)

    return {
        "norenordno": data.get("orderid", ""),
        "exch": data.get("exchange", "NSE"),
        "tsym": data.get("symbol", ""),
        "qty": str(data.get("quantity", "0")),
        "prc": str(data.get("price", "0")),
        "trgprc": str(data.get("trigger_price", "0")),
        "prctyp": prctyp,
        "ret": "DAY",
    }
