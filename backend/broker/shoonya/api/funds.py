"""
Shoonya funds / margin (limits) API.
Adapted for the openbull funds convention:
  - get_margin_data(auth_token, config) -> dict with numeric values.
"""

import json
import logging

from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)

BASE_URL = "https://api.shoonya.com/NorenWClientAPI"


def _split_token(auth_token: str) -> tuple[str, str, str]:
    """Split combined ``userid:susertoken:actid``."""
    parts = auth_token.split(":") if auth_token else []
    if len(parts) >= 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], parts[1], parts[0]
    return "", auth_token or "", ""


def _post(endpoint: str, payload: dict, jkey: str) -> dict:
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
        return {"stat": "Not_Ok", "emsg": f"Invalid JSON (HTTP {response.status_code})"}


def get_margin_data(auth_token: str, config: dict | None = None) -> dict:
    """Fetch margin / limits data from Shoonya.

    Returns dict with stringified values (matches openbull funds convention).
    """
    uid, jkey, actid = _split_token(auth_token)

    if not uid or not jkey:
        logger.error("Missing uid or jkey for Shoonya limits call")
        return {}

    try:
        payload = {"uid": uid, "actid": actid}
        result = _post("Limits", payload, jkey)
    except Exception as e:
        logger.error("Error fetching Shoonya limits: %s", e)
        return {}

    if not result or result.get("stat") != "Ok":
        logger.error(
            "Shoonya limits response error: %s",
            result.get("emsg") if isinstance(result, dict) else None,
        )
        return {}

    # Parse relevant fields.
    try:
        cash = float(result.get("cash", 0) or 0)
    except (ValueError, TypeError):
        cash = 0.0

    try:
        collateral = float(result.get("collateral") or result.get("brkcollamt", 0) or 0)
    except (ValueError, TypeError):
        collateral = 0.0

    try:
        rpnl = float(result.get("rpnl", 0) or 0)
    except (ValueError, TypeError):
        rpnl = 0.0

    try:
        unmtom = float(result.get("unmtom", 0) or 0)
    except (ValueError, TypeError):
        unmtom = 0.0

    try:
        marginused = float(result.get("marginused", 0) or 0)
    except (ValueError, TypeError):
        marginused = 0.0

    if rpnl == 0.0 and unmtom == 0.0:
        try:
            from backend.broker.shoonya.api.order_api import get_positions
            pos_result = get_positions(auth_token, config)
            if pos_result.get("status") is True and isinstance(pos_result.get("data"), list):
                for pos in pos_result.get("data"):
                    try:
                        pos_rpnl = float(pos.get("rpnl") or 0.0)
                        pos_urmtom = float(pos.get("urmtom") or 0.0)
                        qty = float(pos.get("netqty") or 0.0)
                        avg_price = float(pos.get("netavgprc") or 0.0)
                        ltp = float(pos.get("lp") or 0.0)
                        if pos_urmtom == 0.0 and qty != 0 and avg_price > 0 and ltp > 0:
                            pos_urmtom = (ltp - avg_price) * qty
                        rpnl += pos_rpnl
                        unmtom += pos_urmtom
                    except Exception:
                        pass
        except Exception as e:
            logger.error("Error fetching Shoonya positions for PnL fallback: %s", e)

    return {
        "availablecash": round(cash, 2),
        "collateral": round(collateral, 2),
        "m2mrealized": round(rpnl, 2),
        "m2munrealized": round(unmtom, 2),
        "utiliseddebits": round(marginused, 2),
    }
