"""
Shoonya margin calculator API.
Uses the SPAN Calculator endpoint as Shoonya lacks a dedicated basket-margin API.
"""

import json
import logging

from backend.broker.shoonya.mapping.margin_data import (
    parse_margin_response,
    transform_margin_positions,
)
from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)

BASE_URL = "https://api.shoonya.com/NorenWClientTP"


class _MockResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.status = status_code


def _split_token(auth_token: str) -> tuple[str, str, str]:
    parts = auth_token.split(":") if auth_token else []
    if len(parts) >= 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], parts[1], parts[0]
    return "", auth_token or "", ""


def _post(endpoint: str, payload: dict, jkey: str) -> dict:
    data = {"jData": json.dumps(payload), "jKey": jkey}
    client = get_httpx_client()
    response = client.post(
        f"{BASE_URL}/{endpoint}",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        return response.json()
    except json.JSONDecodeError:
        return {"stat": "Not_Ok", "emsg": f"Invalid JSON (HTTP {response.status_code})"}


def calculate_margin_api(positions: list[dict], auth_token: str) -> tuple:
    """Calculate margin requirement for a basket of positions via Shoonya SPAN."""
    transformed = transform_margin_positions(positions)

    if not transformed:
        return _MockResponse(400), {
            "status": "error",
            "message": "No valid positions to calculate margin. Check if symbols are valid.",
        }

    uid, jkey, actid = _split_token(auth_token)
    payload = {"actid": actid, "pos": transformed}

    logger.info("Shoonya SPAN payload: %s", json.dumps(payload))

    try:
        result = _post("SpanCalc", payload, jkey)
        logger.info("Shoonya SPAN response: %s", result)
        return _MockResponse(200), parse_margin_response(result)

    except Exception as e:
        logger.error("Error calling Shoonya SPAN API: %s", e)
        return _MockResponse(500), {
            "status": "error",
            "message": f"Failed to calculate margin: {e}",
        }
