"""
Shoonya order / trade / position / holdings data mapping.

Translates raw Shoonya API responses into OpenBull's standard format.
"""

import logging
from datetime import datetime

from backend.broker.upstox.mapping.order_data import (
    get_symbol_from_brsymbol_cache,
    get_token_from_cache,
)

logger = logging.getLogger(__name__)

# ---- Product / Order-type reverse maps ----

_PRODUCT_MAP = {
    "C": "CNC",
    "I": "MIS",
    "M": "NRML",
    # some old entries use full strings
    "CNC": "CNC",
    "MIS": "MIS",
    "NRML": "NRML",
}

_ORDER_TYPE_MAP = {
    "MKT": "MARKET",
    "LMT": "LIMIT",
    "SL-MKT": "SL-M",
    "SL-LMT": "SL",
    # pass-through
    "MARKET": "MARKET",
    "LIMIT": "LIMIT",
    "SL-M": "SL-M",
    "SL": "SL",
}

_ORDER_STATUS_MAP = {
    "COMPLETE": "complete",
    "OPEN": "open",
    "TRIGGER PENDING": "open",
    "TRIGGER_PENDING": "open",
    "REJECTED": "rejected",
    "CANCELLED": "cancelled",
    "CANCELED": "cancelled",
    "PENDING": "open",
    "NEWORDER": "open",
}


# ---- Order book ----

def map_order_data(order: dict, broker: str = "shoonya") -> dict:
    """Map a single Shoonya order dict to OpenBull format."""
    exchange = order.get("exch", "")
    brsymbol = order.get("tsym", "")
    symbol = get_symbol_from_brsymbol_cache(brsymbol, exchange) or brsymbol

    raw_status = order.get("status", "").upper()
    status = _ORDER_STATUS_MAP.get(raw_status, raw_status.lower())

    raw_action = order.get("trantype", "B").upper()
    action = "BUY" if raw_action == "B" else "SELL"

    prctyp = order.get("prctyp", "LMT")
    order_type = _ORDER_TYPE_MAP.get(prctyp, prctyp)

    prd = order.get("prd", "M")
    product = _PRODUCT_MAP.get(prd, prd)

    return {
        "orderid": order.get("norenordno", ""),
        "symbol": symbol,
        "exchange": exchange,
        "action": action,
        "quantity": order.get("qty", "0"),
        "price": order.get("prc", "0"),
        "trigger_price": order.get("trgprc", "0"),
        "order_type": order_type,
        "product": product,
        "status": status,
        "timestamp": order.get("norentm", ""),
        "filled_quantity": order.get("fillshares", "0"),
        "average_price": order.get("avgprc", "0"),
        "broker": broker,
    }


def transform_order_data(orders: list | dict) -> list:
    """Transform a list (or error dict) from Shoonya OrderBook to OpenBull list."""
    if not orders or (isinstance(orders, dict) and orders.get("stat") == "Not_Ok"):
        return []
    if isinstance(orders, dict):
        orders = [orders]
    return [map_order_data(o) for o in orders if isinstance(o, dict)]


# ---- Trade book ----

def _parse_shoonya_timestamp(ts_str: str) -> str:
    """Extract HH:MM:SS from Shoonya timestamp 'HH:MM:SS DD-MM-YYYY'."""
    if not ts_str:
        return ""
    try:
        parts = ts_str.split(" ")
        return parts[0] if parts else ts_str
    except Exception:
        return ts_str


def map_trade_data(trade: dict, broker: str = "shoonya") -> dict:
    """Map a single Shoonya trade dict to OpenBull format."""
    exchange = trade.get("exch", "")
    brsymbol = trade.get("tsym", "")
    symbol = get_symbol_from_brsymbol_cache(brsymbol, exchange) or brsymbol

    raw_action = trade.get("trantype", "B").upper()
    action = "BUY" if raw_action == "B" else "SELL"

    prd = trade.get("prd", "M")
    product = _PRODUCT_MAP.get(prd, prd)

    return {
        "orderid": trade.get("norenordno", ""),
        "tradeid": trade.get("fllid", ""),
        "symbol": symbol,
        "exchange": exchange,
        "action": action,
        "quantity": trade.get("flqty", "0"),
        "price": trade.get("flprc", "0"),
        "product": product,
        "timestamp": _parse_shoonya_timestamp(trade.get("fltm", "")),
        "broker": broker,
    }


def transform_tradebook_data(trades: list | dict) -> list:
    """Transform Shoonya TradeBook response to OpenBull list."""
    if not trades or (isinstance(trades, dict) and trades.get("stat") == "Not_Ok"):
        return []
    if isinstance(trades, dict):
        trades = [trades]
    return [map_trade_data(t) for t in trades if isinstance(t, dict)]


# ---- Positions ----

def map_position_data(pos: dict, broker: str = "shoonya") -> dict:
    """Map a single Shoonya position to OpenBull format."""
    exchange = pos.get("exch", "")
    brsymbol = pos.get("tsym", "")
    symbol = get_symbol_from_brsymbol_cache(brsymbol, exchange) or brsymbol

    prd = pos.get("prd", "M")
    product = _PRODUCT_MAP.get(prd, prd)

    net_qty = int(pos.get("netqty", "0") or "0")
    avg_price = float(pos.get("netavgprc", "0") or "0")
    ltp = float(pos.get("lp", "0") or "0")

    rpnl = float(pos.get("rpnl", "0") or "0")
    urmtom = float(pos.get("urmtom", "0") or "0")

    # Shoonya reports realized PnL with opposite sign convention in some versions
    realized_pnl = round(-rpnl, 2)
    unrealized_pnl = round(urmtom, 2)
    pnl = round(realized_pnl + unrealized_pnl, 2)

    return {
        "symbol": symbol,
        "exchange": exchange,
        "product": product,
        "quantity": str(net_qty),
        "average_price": str(avg_price),
        "ltp": str(ltp),
        "pnl": str(pnl),
        "realized_pnl": str(realized_pnl),
        "unrealized_pnl": str(unrealized_pnl),
        "broker": broker,
    }


def transform_positions_data(positions: list | dict) -> list:
    """Transform Shoonya PositionBook response to OpenBull list."""
    if not positions or (isinstance(positions, dict) and positions.get("stat") == "Not_Ok"):
        return []
    if isinstance(positions, dict):
        positions = [positions]
    return [map_position_data(p) for p in positions if isinstance(p, dict)]


# ---- Holdings ----

def map_portfolio_data(holding: dict, broker: str = "shoonya") -> dict:
    """Map a single Shoonya holding to OpenBull format."""
    exchange = holding.get("exch", "NSE")
    brsymbol = holding.get("tsym", "")
    symbol = get_symbol_from_brsymbol_cache(brsymbol, exchange) or brsymbol

    dpqty = int(holding.get("dpqty", "0") or "0")
    benqty = int(holding.get("benqty", "0") or "0")
    quantity = dpqty + benqty

    return {
        "symbol": symbol,
        "exchange": exchange,
        "quantity": str(quantity),
        "average_price": holding.get("upldprc", "0"),
        "ltp": holding.get("lp", "0"),
        "product": "CNC",
        "broker": broker,
    }


def transform_holdings_data(holdings: list | dict) -> list:
    """Transform Shoonya Holdings response to OpenBull list."""
    if not holdings or (isinstance(holdings, dict) and holdings.get("stat") == "Not_Ok"):
        return []
    if isinstance(holdings, dict):
        # Holdings response wraps data in "hldvl" key
        items = holdings.get("hldvl", [])
        if not items:
            return []
        return [map_portfolio_data(h) for h in items if isinstance(h, dict)]
    return [map_portfolio_data(h) for h in holdings if isinstance(h, dict)]
