"""
Shoonya funds API - fetch margin and account limits.
"""

import json
import logging

from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)


def get_margin_data(auth_token: str, config: dict | None = None) -> dict:
    """Fetch margin/funds data from Shoonya's Limits endpoint.

    Args:
        auth_token: Shoonya session token (susertoken).
        config: Broker config dict with ``client_id`` (trading user ID).

    Returns:
        Dict with keys: availablecash, collateral, m2munrealized,
        m2mrealized, utiliseddebits — all formatted as "%.2f" strings.
    """
    config = config or {}
    user_id = config.get("client_id", "")

    payload = json.dumps({"uid": user_id, "actid": user_id})
    payload_str = "jData=" + payload

    headers = {
        "Content-Type": "text/plain",
        "Authorization": f"Bearer {auth_token}",
    }

    client = get_httpx_client()
    try:
        response = client.post(
            "https://api.shoonya.com/NorenWClientAPI/Limits",
            content=payload_str,
            headers=headers,
        )
        data = response.json()
    except Exception as e:
        logger.error("Error fetching Shoonya funds: %s", e)
        return {}

    if data.get("stat") != "Ok":
        logger.error("Shoonya Limits error: %s", data.get("emsg", "Unknown error"))
        return {}

    try:
        available_cash = (
            float(data.get("cash", 0) or 0)
            + float(data.get("payin", 0) or 0)
            - float(data.get("marginused", 0) or 0)
        )
        collateral = float(data.get("brkcollamt", 0) or 0)
        used_margin = float(data.get("marginused", 0) or 0)
        m2m_realized = -float(data.get("rpnl", 0) or 0)
        m2m_unrealized = float(data.get("unmtom", 0) or 0)

        return {
            "availablecash": f"{available_cash:.2f}",
            "collateral": f"{collateral:.2f}",
            "m2munrealized": f"{m2m_unrealized:.2f}",
            "m2mrealized": f"{m2m_realized:.2f}",
            "utiliseddebits": f"{used_margin:.2f}",
        }
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Error processing Shoonya funds data: %s", e)
        return {}
