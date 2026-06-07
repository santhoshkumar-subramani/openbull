"""
Shoonya order data transformation.
Maps OpenBull order format to Shoonya (Noren) API field names and back.
"""

import logging

from backend.broker.upstox.mapping.order_data import get_brsymbol_from_cache

logger = logging.getLogger(__name__)


def _br_symbol(symbol: str, exchange: str) -> str:
    """Look up broker symbol from the in-memory cache (shared with upstox)."""
    return get_brsymbol_from_cache(symbol, exchange) or symbol


def transform_data(data: dict, token: str) -> dict:
    """Transform OpenBull order request to Shoonya PlaceOrder format."""
    symbol = _br_symbol(data["symbol"], data["exchange"])
    return {
        "exch": data["exchange"],
        "tsym": symbol,
        "qty": str(data["quantity"]),
        "prc": str(data.get("price", "0")),
        "trgprc": str(data.get("trigger_price", "0")),
        "dscqty": str(data.get("disclosed_quantity", "0")),
        "prd": map_product_type(data["product"]),
        "trantype": map_action(data["action"]),
        "prctyp": map_order_type(data["pricetype"]),
        "ret": "DAY",
        "remarks": data.get("strategy", ""),
        "ordersource": "API",
    }


def transform_modify_order_data(data: dict, token: str) -> dict:
    """Transform OpenBull modify order to Shoonya ModifyOrder format."""
    return {
        "exch": data["exchange"],
        "norenordno": data["orderid"],
        "tsym": data["symbol"],
        "qty": str(data["quantity"]),
        "prc": str(data["price"]),
        "trgprc": str(data.get("trigger_price", "0")),
        "prctyp": map_order_type(data["pricetype"]),
        "ret": "DAY",
    }


def map_order_type(pricetype: str) -> str:
    """Map OpenBull pricetype to Shoonya price type."""
    mapping = {
        "MARKET": "MKT",
        "LIMIT": "LMT",
        "SL": "SL-LMT",
        "SL-M": "SL-MKT",
    }
    return mapping.get(pricetype, "MKT")


def reverse_map_order_type(prctyp: str) -> str:
    """Map Shoonya price type back to OpenBull pricetype."""
    mapping = {
        "MKT": "MARKET",
        "LMT": "LIMIT",
        "SL-LMT": "SL",
        "SL-MKT": "SL-M",
    }
    return mapping.get(prctyp, "MARKET")


def map_product_type(product: str) -> str:
    """Map OpenBull product type to Shoonya product code."""
    mapping = {
        "CNC": "C",
        "NRML": "M",
        "MIS": "I",
    }
    return mapping.get(product, "I")


def reverse_map_product_type(prd: str) -> str:
    """Map Shoonya product code back to OpenBull product type."""
    mapping = {
        "C": "CNC",
        "M": "NRML",
        "I": "MIS",
        "B": "MIS",  # Bracket → MIS for display
        "H": "MIS",  # Cover → MIS for display
    }
    return mapping.get(prd, "MIS")


def map_action(action: str) -> str:
    """Map OpenBull action to Shoonya transaction type."""
    mapping = {
        "BUY": "B",
        "SELL": "S",
    }
    return mapping.get(action.upper(), "B")


def reverse_map_action(trantype: str) -> str:
    """Map Shoonya transaction type back to OpenBull action."""
    mapping = {
        "B": "BUY",
        "S": "SELL",
    }
    return mapping.get(trantype, "BUY")
