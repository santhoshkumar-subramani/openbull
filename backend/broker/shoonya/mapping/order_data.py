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

def _map_single_order(order: dict, broker: str = "shoonya") -> dict:
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
        "quantity": int(order.get("qty", 0) or 0),
        "price": float(order.get("prc", 0) or 0),
        "trigger_price": float(order.get("trgprc", 0) or 0),
        "order_type": order_type,
        "product": product,
        "status": status,
        "timestamp": order.get("norentm", ""),
        "filled_quantity": int(order.get("fillshares", 0) or 0),
        "average_price": float(order.get("avgprc", 0) or 0),
        "broker": broker,
    }


def map_order_data(order_data: list | dict, broker: str = "shoonya") -> list[dict]:
    """Map the full Shoonya OrderBook response to a list of OpenBull order dicts."""
    if not order_data or (isinstance(order_data, dict) and order_data.get("stat") == "Not_Ok"):
        return []
    if isinstance(order_data, dict):
        order_data = [order_data]
    return [_map_single_order(o, broker) for o in order_data if isinstance(o, dict)]


def calculate_order_statistics(order_data: list[dict]) -> dict:
    """Calculate buy/sell/status counts from mapped order data."""
    total_buy_orders = total_sell_orders = 0
    total_completed_orders = total_open_orders = total_rejected_orders = 0

    for order in order_data:
        action = order.get("action", "")
        if action == "BUY":
            total_buy_orders += 1
        elif action == "SELL":
            total_sell_orders += 1

        status = order.get("status", "")
        if status == "complete":
            total_completed_orders += 1
        elif status == "open":
            total_open_orders += 1
        elif status == "rejected":
            total_rejected_orders += 1

    return {
        "total_buy_orders": total_buy_orders,
        "total_sell_orders": total_sell_orders,
        "total_completed_orders": total_completed_orders,
        "total_open_orders": total_open_orders,
        "total_rejected_orders": total_rejected_orders,
    }


def transform_order_data(orders: list) -> list:
    """Pass-through — data is already mapped by map_order_data."""
    return orders if isinstance(orders, list) else []


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


def _map_single_trade(trade: dict, broker: str = "shoonya") -> dict:
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
        "quantity": int(trade.get("flqty", 0) or 0),
        "price": float(trade.get("flprc", 0) or 0),
        "product": product,
        "timestamp": _parse_shoonya_timestamp(trade.get("fltm", "")),
        "broker": broker,
    }


def map_trade_data(trade_data: list | dict, broker: str = "shoonya") -> list[dict]:
    """Map the full Shoonya TradeBook response to a list of OpenBull trade dicts."""
    if not trade_data or (isinstance(trade_data, dict) and trade_data.get("stat") == "Not_Ok"):
        return []
    if isinstance(trade_data, dict):
        trade_data = [trade_data]
    return [_map_single_trade(t, broker) for t in trade_data if isinstance(t, dict)]


def transform_tradebook_data(trades: list) -> list:
    """Pass-through — data is already mapped by map_trade_data."""
    return trades if isinstance(trades, list) else []


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
        "quantity": net_qty,
        "average_price": round(avg_price, 2),
        "ltp": round(ltp, 2),
        "pnl": pnl,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
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

def _map_single_holding(holding: dict, broker: str = "shoonya") -> dict:
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
        "quantity": quantity,
        "average_price": float(holding.get("upldprc", 0) or 0),
        "ltp": float(holding.get("lp", 0) or 0),
        "product": "CNC",
        "broker": broker,
    }


def map_portfolio_data(holdings_data: list | dict, **kwargs) -> list[dict]:
    """Map the full Shoonya Holdings response to a list of OpenBull holding dicts."""
    if not holdings_data or (isinstance(holdings_data, dict) and holdings_data.get("stat") == "Not_Ok"):
        return []
    if isinstance(holdings_data, dict):
        items = holdings_data.get("hldvl", [])
        if not items:
            return []
        holdings_data = items
    return [_map_single_holding(h) for h in holdings_data if isinstance(h, dict)]


def calculate_portfolio_statistics(holdings_data: list[dict]) -> dict:
    """Calculate portfolio totals from mapped holdings data."""
    if not isinstance(holdings_data, list):
        holdings_data = []

    totalinvvalue = sum(
        float(h.get("average_price") or 0) * int(h.get("quantity") or 0)
        for h in holdings_data
    )
    totalholdingvalue = sum(
        float(h.get("ltp") or 0) * int(h.get("quantity") or 0)
        for h in holdings_data
    )
    totalprofitandloss = totalholdingvalue - totalinvvalue
    totalpnlpercentage = (totalprofitandloss / totalinvvalue * 100) if totalinvvalue else 0.0

    return {
        "totalholdingvalue": totalholdingvalue,
        "totalinvvalue": totalinvvalue,
        "totalprofitandloss": totalprofitandloss,
        "totalpnlpercentage": totalpnlpercentage,
    }


def transform_holdings_data(holdings: list) -> list:
    """Pass-through — data is already mapped by map_portfolio_data."""
    return holdings if isinstance(holdings, list) else []
