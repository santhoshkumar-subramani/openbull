"""
Shoonya (Finvasia) authentication via OAuth GenAcsTok flow.

When the user clicks "Login with Shoonya", OpenBull redirects the browser to:
  https://api.shoonya.com/OAuthlogin/authorize/oauth?client_id={api_key}

Shoonya authenticates the user on their own site and redirects back to:
  http://<backend>/shoonya/callback?code=<code>

This module exchanges that code for a session token via the GenAcsTok endpoint.

Config provides:
  - api_key:    OAuth client_id (the part after ::: in the BROKER_API_KEY format
                used by OpenAlgo, e.g. "FA99299_U")
  - api_secret: App secret key

The returned access token is used for all subsequent Shoonya API calls.
"""

import hashlib
import json
import logging

from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)

_SHOONYA_TOKEN_URL = "https://api.shoonya.com/NorenWClientAPI/GenAcsTok"


def authenticate_broker(code: str, config: dict) -> tuple[str | None, str | None]:
    """Exchange Shoonya OAuth authorization code for a session token.

    Args:
        code: Authorization code received from Shoonya's OAuth redirect callback.
        config: Broker config dict with ``api_key`` (OAuth client_id) and
                ``api_secret`` (app secret key).

    Returns:
        ``(access_token, error_message)``
    """
    try:
        client_id = config.get("api_key", "").strip()     # OAuth client_id
        secret_key = config.get("api_secret", "").strip() # App secret key

        if not client_id:
            return None, "Missing API Key (OAuth client_id) in broker configuration."
        if not secret_key:
            return None, "Missing API Secret in broker configuration."
        if not code:
            return None, "Missing authorization code from Shoonya callback."

        # checksum = SHA-256(client_id + secret_key + code)
        checksum = hashlib.sha256(f"{client_id}{secret_key}{code}".encode()).hexdigest()

        payload = {
            "code": code,
            "checksum": checksum,
        }

        payload_str = "jData=" + json.dumps(payload)
        headers = {"Content-Type": "text/plain"}

        client = get_httpx_client()
        response = client.post(_SHOONYA_TOKEN_URL, content=payload_str, headers=headers)

        try:
            data = response.json()
        except Exception:
            return None, f"Shoonya authentication failed (HTTP {response.status_code})"

        if data.get("stat") == "Ok" and "access_token" in data:
            access_token = data["access_token"]
            logger.info("Successfully authenticated with Shoonya via OAuth")
            return access_token, None

        error_msg = data.get("emsg", "Authentication failed. Please try again.")
        logger.error("Shoonya auth error: %s", error_msg)
        return None, error_msg

    except Exception as e:
        logger.exception("Unexpected error during Shoonya authentication")
        return None, f"Unexpected error during authentication: {e}"
