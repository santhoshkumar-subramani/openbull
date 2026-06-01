"""
Shoonya basket margin calculation.

Shoonya's GetBasketMargin endpoint rejects raw MARKET / SL-M order types.
Before submitting to the margin endpoint we apply Market Price Protection (MPP):
  - MARKET  → LMT  with a protected price based on LTP
  - SL-M    → SL-LMT with a protected price

MPP price slabs (applied as a % buffer around the LTP):
  EQ / FUT:  LTP < 100  → ±2 %
             100–500    → ±1 %
             > 500      → ±0.5 %
  OPT (CE/PE): LTP < 10 → ±5 %
               10–100   → ±3 %
               100–500  → ±2 %
               > 500    → ±1 %
"""

import json
import logging

from backend.broker.upstox.mapping.order_data import get_brsymbol_from_cache, get_token_from_cache
from backend.broker.shoonya.mapping.transform_data import map_order_type, map_product_type
from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.shoonya.com/NorenWClientAPI"

_INDEX_EXCHANGE_MAP = {
    "NSE_INDEX": "NSE",
    "BSE_INDEX": "BSE",
}


def _mpp_protected_price(ltp: float, action: str, symbol: str) -> float:
    """Calculate Market Price Protection price.

    Args:
        ltp: Last traded price.
        action: "BUY" or "SELL".
        symbol: OpenBull symbol (used to detect OPT type from CE/PE suffix).

    Returns:
        Protected limit price (rounded to 2dp).
    """
    upper = symbol.upper()
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


def get_basket_margin(
    auth_token: str, config: dict, positions: list[dict]
) -> dict:
    """Calculate basket margin via Shoonya GetBasketMargin.

    Args:
        auth_token: Shoonya session token.
        config: Broker config dict.
        positions: List of OpenBull-format position dicts.

    Returns:
        OpenBull margin dict: {status, data: {total_margin_required,
        span_margin, exposure_margin, margin_benefit}}.
    """
    from backend.broker.shoonya.mapping.margin_data import (
        transform_margin_positions,
        parse_margin_response,
    )

    transformed = transform_margin_positions(positions, auth_token, config)
    if not transformed:
        return {"status": "error", "message": "No valid positions for margin calculation"}

    user_id = config.get("client_id", "")
    payload = {
        "uid": user_id,
        "actid": user_id,
        "prd": transformed[0].get("prd", "M"),
        "exch": transformed[0].get("exch", "NSE"),
        "tsym": transformed[0].get("tsym", ""),
        "qty": transformed[0].get("qty", "1"),
        "prc": transformed[0].get("prc", "0"),
        "prctyp": transformed[0].get("prctyp", "LMT"),
        "trantype": transformed[0].get("trantype", "B"),
        "trgprc": transformed[0].get("trgprc", "0"),
    }

    # Additional legs in restlist
    if len(transformed) > 1:
        payload["restlist"] = transformed[1:]

    payload_str = "jData=" + json.dumps(payload)
    headers = {
        "Content-Type": "text/plain",
        "Authorization": f"Bearer {auth_token}",
    }

    client = get_httpx_client()
    try:
        response = client.post(
            f"{_BASE_URL}/GetBasketMargin",
            content=payload_str,
            headers=headers,
        )
        data = response.json()
    except Exception as e:
        logger.error("Error calling Shoonya GetBasketMargin: %s", e)
        return {"status": "error", "message": str(e)}

    return parse_margin_response(data)
