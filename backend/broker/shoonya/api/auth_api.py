"""
Shoonya (Finvasia) authentication via OAuth + GenAcsTok.

Flow:
  1. Frontend redirects user to Shoonya OAuth login page.
  2. User logs in (user ID + password + TOTP) and clicks "Authorize".
  3. Shoonya redirects to our /shoonya/callback with ?code=OAUTH_CODE.
  4. This module exchanges the code for a session token via GenAcsTok.

Credentials sourced from broker config:
  - api_key    : Vendor code, e.g. "user_id_U"  (client_id in OAuth URL)
  - api_secret : App secret key                  (used in checksum)
  - client_id  : Trading account user ID, e.g. "user_id"  (uid)

GenAcsTok checksum = SHA-256(vendor_code + api_secret + oauth_code)
"""

import hashlib
import json
import logging

from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)

_GEN_ACS_TOK_URL = "https://api.shoonya.com/NorenWClientAPI/GenAcsTok"


def authenticate_broker(code_or_token: str, config: dict) -> tuple[str | None, str | None]:
    """Exchange Shoonya OAuth code for a session token via GenAcsTok.

    Args:
        code_or_token: The OAuth authorization code received from Shoonya's
            redirect callback (the ``code`` query parameter).
        config: Broker config dict with ``api_key`` (vendor code),
                ``api_secret`` (app secret), and ``client_id`` (user ID).

    Returns:
        ``(susertoken, error_message)``
    """
    try:
        uid = config.get("client_id", "").strip()          # trading account ID (user_id)
        vendor_code = config.get("api_key", "").strip()    # vendor code (user_id_U)
        api_secret = config.get("api_secret", "").strip()  # app secret
        oauth_code = code_or_token.strip() if code_or_token else ""

        if not uid:
            return None, "Missing User ID (trading account ID) in broker configuration."
        if not vendor_code:
            return None, "Missing Vendor Code (API Key) in broker configuration."
        if not api_secret:
            return None, "Missing App Secret in broker configuration."
        if not oauth_code:
            return None, "Missing OAuth authorization code."

        # checksum = SHA-256(vendor_code + api_secret + oauth_code)  — no separators
        checksum = hashlib.sha256(
            f"{vendor_code}{api_secret}{oauth_code}".encode()
        ).hexdigest()

        payload = {
            "code": oauth_code,
            "checksum": checksum,
            "uid": uid,
        }
        payload_str = "jData=" + json.dumps(payload)
        logger.info(f"Shoonya GenAcsTok payload: {payload_str}")

        client = get_httpx_client()
        response = client.post(
            _GEN_ACS_TOK_URL,
            content=payload_str,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if response.status_code >= 500:
            logger.error("Shoonya server error (GenAcsTok) HTTP %s", response.status_code)
            return None, (
                f"Shoonya server is currently unavailable (HTTP {response.status_code}). "
                "Please try again in a few minutes."
            )
        if response.status_code >= 400:
            logger.error("Shoonya API error (GenAcsTok) HTTP %s", response.status_code)
            return None, f"Shoonya API error (HTTP {response.status_code}). Check your credentials."

        try:
            data = response.json()
        except Exception:
            return None, f"Unexpected response from Shoonya (HTTP {response.status_code})."

        # Successful response contains susertoken
        if "susertoken" in data:
            logger.info("Successfully authenticated with Shoonya for uid=%s", uid)
            # order_api.py _split_token expects "userid:susertoken:actid"
            combined_token = f"{uid}:{data['susertoken']}:{uid}"
            return combined_token, None

        error_msg = data.get("emsg", "Authentication failed. Please try again.")
        logger.error("Shoonya GenAcsTok error for uid=%s: %s", uid, error_msg)
        return None, error_msg

    except Exception as e:
        logger.exception("Unexpected error during Shoonya authentication")
        return None, f"Unexpected error during authentication: {e}"
