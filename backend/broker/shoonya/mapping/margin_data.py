"""
Shoonya basket margin data transformation.

Prepares position lists for GetBasketMargin and parses the response.
"""

import logging

from backend.broker.upstox.mapping.order_data import get_brsymbol_from_cache, get_token_from_cache
from backend.broker.shoonya.mapping.transform_data import (
    _calculate_protected_price,
    map_order_type,
    map_product_type,
)

logger = logging.getLogger(__name__)


def transform_margin_positions(positions: list[dict], auth_token: str, config: dict) -> list[dict]:
    """Transform OpenBull position dicts to Shoonya GetBasketMargin format.

    Applies MPP: MARKET → LMT, SL-M → SL-LMT with protected price.
    """
    result = []
    for pos in positions:
        symbol = pos.get("symbol", "")
        exchange = pos.get("exchange", "NSE")
        action = pos.get("action", "BUY").upper()
        order_type = pos.get("order_type", "LIMIT")
        product = pos.get("product", "NRML")
        quantity = str(pos.get("quantity", "1"))
        price = str(pos.get("price", "0"))
        trigger_price = str(pos.get("trigger_price", "0"))

        brsymbol = get_brsymbol_from_cache(symbol, exchange) or symbol
        token = get_token_from_cache(symbol, exchange) or ""

        # Map index exchanges
        shoonya_exchange = "NSE" if exchange == "NSE_INDEX" else (
            "BSE" if exchange == "BSE_INDEX" else exchange
        )

        prctyp = map_order_type(order_type)
        prd = map_product_type(product)

        # Apply MPP if market order type
        if prctyp == "MKT":
            ltp_str = pos.get("ltp", price)
            try:
                ltp = float(ltp_str)
            except (ValueError, TypeError):
                ltp = 0.0
            if ltp > 0:
                price = str(_calculate_protected_price(ltp, action, symbol))
                prctyp = "LMT"

        elif prctyp == "SL-MKT":
            ltp_str = pos.get("ltp", price)
            try:
                ltp = float(ltp_str)
            except (ValueError, TypeError):
                ltp = 0.0
            if ltp > 0:
                price = str(_calculate_protected_price(ltp, action, symbol))
                prctyp = "SL-LMT"

        result.append({
            "prd": prd,
            "exch": shoonya_exchange,
            "tsym": brsymbol,
            "token": token,
            "qty": quantity,
            "prc": price,
            "trgprc": trigger_price if trigger_price != "0" else "0",
            "trantype": "B" if action == "BUY" else "S",
            "prctyp": prctyp,
        })

    return result


def parse_margin_response(data: dict) -> dict:
    """Parse Shoonya GetBasketMargin response into OpenBull margin dict."""
    if not data or data.get("stat") == "Not_Ok":
        return {
            "status": "error",
            "message": data.get("emsg", "Failed to fetch basket margin") if data else "No response",
        }

    try:
        span = float(data.get("span", "0") or "0")
        expo = float(data.get("expo", "0") or "0")
        margin_used = float(data.get("marginused", "0") or "0")

        return {
            "status": "success",
            "data": {
                "total_margin_required": round(margin_used, 2),
                "span_margin": round(span, 2),
                "exposure_margin": round(expo, 2),
                "margin_benefit": 0,
            },
        }
    except (ValueError, TypeError) as e:
        logger.error("Error parsing Shoonya margin response: %s", e)
        return {"status": "error", "message": f"Margin parse error: {e}"}
