"""
Shoonya (Finvasia / Noren) authentication.

Shoonya uses a credentials flow: userid + password (SHA-256 hashed) + TOTP,
plus a vendor code and app key (also hashed). No browser-based OAuth.

To match the openbull contract ``authenticate_broker(code_or_token, config)``,
the first argument is ``"userid:password:totp_code"``.  The returned access
token is ``"userid:susertoken:actid"`` so downstream callers (REST + WS)
can recover everything they need.
"""

import hashlib
import json
import logging

from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)

BASE_URL = "https://api.shoonya.com/NorenWClientTP"


def _sha256(text: str) -> str:
    """Return the hex SHA-256 digest of *text*."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _post(endpoint: str, payload: dict, jkey: str | None = None) -> dict:
    """POST to a Shoonya REST endpoint.

    Shoonya uses ``jData`` (URL-encoded JSON) + ``jKey`` (session token) as
    form fields, *not* JSON bodies or Bearer headers.
    """
    data: dict[str, str] = {"jData": json.dumps(payload)}
    if jkey:
        data["jKey"] = jkey

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


def authenticate_broker(
    code_or_token: str, config: dict
) -> tuple[str | None, str | None]:
    """Authenticate with Shoonya (Finvasia).

    Args:
        code_or_token: ``"userid:password:totp_code"`` — the credentials the
            user submitted via the login form.
        config: Broker config dict with ``api_key`` (appkey) and
            ``api_secret`` (vendor_code).

    Returns:
        ``(combined_token, error_message)`` where ``combined_token`` is
        ``"userid:susertoken:actid"``.
    """
    try:
        api_key = config.get("api_key")
        vendor_code = config.get("api_secret")

        if not api_key:
            return None, "Missing api_key (Shoonya appkey) in broker configuration."
        if not vendor_code:
            return None, "Missing api_secret (Shoonya vendor_code) in broker configuration."

        if not code_or_token or code_or_token.count(":") < 2:
            return None, (
                "Shoonya credentials must be in the form "
                "'userid:password:totp_code'."
            )

        userid, password, totp_code = code_or_token.split(":", 2)
        userid = userid.strip()
        password = password.strip()
        totp_code = totp_code.strip()

        if not all([userid, password, totp_code]):
            return None, "userid, password and totp_code are all required."

        # Shoonya expects SHA-256 hashed password and appkey.
        pwd_hash = _sha256(password)
        appkey_hash = _sha256(f"{userid}|{api_key}")

        payload = {
            "apkversion": "1.0.0",
            "uid": userid,
            "pwd": pwd_hash,
            "factor2": totp_code,
            "vc": vendor_code,
            "appkey": appkey_hash,
            "imei": "openbull",
            "source": "API",
        }

        result = _post("QuickAuth", payload)

        if result.get("stat") == "Ok" and result.get("susertoken"):
            susertoken = result["susertoken"]
            actid = result.get("actid", userid)
            combined = f"{userid}:{susertoken}:{actid}"
            logger.info("Successfully authenticated with Shoonya (user=%s)", userid)
            return combined, None

        message = result.get("emsg", "Authentication failed. Please try again.")
        return None, message

    except Exception as e:
        logger.exception("Unexpected error during Shoonya authentication")
        return None, f"Unexpected error during authentication: {e}"
