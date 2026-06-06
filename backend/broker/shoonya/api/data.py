"""
Shoonya market data API — quotes, multi-quotes, depth, and historical candles.

All REST endpoints use form-encoded ``jData={json}`` with a Bearer token header,
except chart/history endpoints (TPSeries) which use ``jData={json}&jKey={token}``
as form-urlencoded body (Shoonya's legacy chart API does not accept Bearer).
"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import httpx

from backend.broker.upstox.mapping.order_data import (
    get_token_from_cache,
)
from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.shoonya.com/NorenWClientAPI"
_CHART_URL = "https://api.shoonya.com/NorenWClientTP"

# Shoonya timeframe resolution codes
_TIMEFRAME_MAP: dict[str, str] = {
    "1m": "1",
    "3m": "3",
    "5m": "5",
    "10m": "10",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "2h": "120",
    "4h": "240",
    "D": "D",
}

TIMEFRAME_MAP = _TIMEFRAME_MAP

# Max days per history chunk (to avoid API timeouts)
_CHUNK_DAYS: dict[str, int] = {
    "1": 5,
    "3": 10,
    "5": 15,
    "10": 20,
    "15": 30,
    "30": 60,
    "60": 90,
    "120": 180,
    "240": 365,
    "D": 730,
}

# Batch size for multi-quote requests
_QUOTE_BATCH_SIZE = 20
_QUOTE_BATCH_DELAY = 1.0

# Map index exchanges back to Shoonya exchange code
_INDEX_EXCHANGE_MAP = {
    "NSE_INDEX": "NSE",
    "BSE_INDEX": "BSE",
}


def _api_headers(auth_token: str) -> dict:
    return {
        "Content-Type": "text/plain",
        "Authorization": f"Bearer {auth_token}",
    }


def _post_jdata(endpoint: str, auth_token: str, payload: dict) -> dict:
    """POST to a standard Shoonya endpoint with jData form encoding + Bearer header."""
    client = get_httpx_client()
    payload_str = "jData=" + json.dumps(payload)
    response = client.post(
        f"{_BASE_URL}{endpoint}",
        content=payload_str,
        headers=_api_headers(auth_token),
    )
    return response.json()


def _post_chart_jdata(endpoint: str, auth_token: str, payload: dict) -> dict:
    """POST to a Shoonya chart endpoint using jData+jKey form-urlencoded body.

    Chart endpoints do not accept Authorization: Bearer — the token must be
    included as ``jKey`` in the form-urlencoded body.
    """
    client = get_httpx_client()
    payload_str = "jData=" + json.dumps(payload) + "&jKey=" + auth_token
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    response = client.post(
        f"{_CHART_URL}{endpoint}",
        content=payload_str,
        headers=headers,
    )
    return response.json()


def _get_shoonya_exchange(exchange: str) -> str:
    """Convert OpenBull exchange to Shoonya exchange code."""
    return _INDEX_EXCHANGE_MAP.get(exchange, exchange)


def _resolve_token(symbol: str, exchange: str) -> str:
    token = get_token_from_cache(symbol, exchange)
    if token:
        return token

    # Redis/in-memory cache can be stale after a broker switch or an older
    # master-contract download. The DB is the source of truth for OpenBull
    # symbol -> broker token mapping, so fall back to symtoken before failing.
    from backend.services.market_data_service import _run_query

    rows = _run_query(
        "SELECT token FROM symtoken WHERE symbol = :symbol AND exchange = :exchange LIMIT 1",
        {"symbol": symbol, "exchange": exchange},
    )
    if rows and rows[0][0]:
        return str(rows[0][0])

    raise ValueError(
        f"Symbol {exchange}:{symbol} not in master contracts - download Shoonya master contracts first"
    )


def _quote_from_shoonya(data: dict) -> dict:
    return {
        "ltp": float(data.get("lp", 0) or 0),
        "open": float(data.get("o", 0) or 0),
        "high": float(data.get("h", 0) or 0),
        "low": float(data.get("l", 0) or 0),
        "close": float(data.get("c", 0) or 0),
        "prev_close": float(data.get("c", 0) or 0),
        "volume": int(float(data.get("v", 0) or 0)),
        "oi": int(float(data.get("oi", 0) or 0)),
        "bid": float(data.get("bp1", 0) or 0),
        "ask": float(data.get("sp1", 0) or 0),
        "bid_qty": int(float(data.get("bq1", 0) or 0)),
        "ask_qty": int(float(data.get("sq1", 0) or 0)),
    }


def get_quotes(
    symbol: str, exchange: str, auth_token: str, config: dict | None = None
) -> dict:
    """Get real-time quote for a single symbol.

    Returns a dict with ltp, open, high, low, close, prev_close, volume,
    oi, bid, ask, bid_qty, ask_qty.
    """
    config = config or {}
    user_id = config.get("client_id", "")

    token = _resolve_token(symbol, exchange)

    shoonya_exchange = _get_shoonya_exchange(exchange)

    payload = {
        "uid": user_id,
        "exch": shoonya_exchange,
        "token": token,
    }

    try:
        data = _post_jdata("/GetQuotes", auth_token, payload)

        if data.get("stat") != "Ok":
            raise ValueError(f"Shoonya GetQuotes error: {data.get('emsg', 'Unknown')}")

        return _quote_from_shoonya(data)
    except Exception as e:
        logger.error("Error fetching Shoonya quote for %s/%s: %s", symbol, exchange, e)
        raise


def _fetch_single_quote(
    symbol: str, exchange: str, auth_token: str, user_id: str
) -> dict:
    """Fetch a single symbol's quote synchronously (for ThreadPoolExecutor)."""
    try:
        token = _resolve_token(symbol, exchange)
        shoonya_exchange = _get_shoonya_exchange(exchange)

        payload = {
            "uid": user_id,
            "exch": shoonya_exchange,
            "token": token,
        }
        payload_str = "jData=" + json.dumps(payload)
        headers = _api_headers(auth_token)

        response = httpx.post(
            f"{_BASE_URL}/GetQuotes",
            content=payload_str,
            headers=headers,
            timeout=10.0,
        )
        data = response.json()

        if data.get("stat") != "Ok":
            return {
                "symbol": symbol,
                "exchange": exchange,
                "error": data.get("emsg", "Quote error"),
            }

        return {
            "symbol": symbol,
            "exchange": exchange,
            **_quote_from_shoonya(data),
        }
    except Exception as e:
        return {"symbol": symbol, "exchange": exchange, "error": str(e)}


def get_multi_quotes(
    symbols_list: list[dict], auth_token: str, config: dict | None = None
) -> list[dict]:
    """Get quotes for multiple symbols using concurrent requests.

    Shoonya's GetQuotes is a single-symbol endpoint. We parallelize with
    a ThreadPoolExecutor (20 workers) and batch with a rate-limit delay.
    """
    if not symbols_list:
        return []

    config = config or {}
    user_id = config.get("client_id", "")

    results: list[dict] = []

    for batch_start in range(0, len(symbols_list), _QUOTE_BATCH_SIZE):
        batch = symbols_list[batch_start: batch_start + _QUOTE_BATCH_SIZE]

        with ThreadPoolExecutor(max_workers=min(len(batch), 20)) as executor:
            futures = {
                executor.submit(
                    _fetch_single_quote,
                    item["symbol"],
                    item["exchange"],
                    auth_token,
                    user_id,
                ): item
                for item in batch
            }
            for future in as_completed(futures):
                try:
                    result = future.result()
                    if "error" not in result:
                        results.append(result)
                    elif "error" in result:
                        logger.warning(
                            "Shoonya quote error for %s/%s: %s",
                            result["symbol"], result["exchange"], result["error"],
                        )
                except Exception as e:
                    logger.warning("Shoonya quote worker exception: %s", e)

        if batch_start + _QUOTE_BATCH_SIZE < len(symbols_list):
            time.sleep(_QUOTE_BATCH_DELAY)

    return results


def get_market_depth(
    symbol: str, exchange: str, auth_token: str, config: dict | None = None
) -> dict:
    """Get 5-level market depth for a symbol."""
    config = config or {}
    user_id = config.get("client_id", "")

    token = _resolve_token(symbol, exchange)
    shoonya_exchange = _get_shoonya_exchange(exchange)

    payload = {
        "uid": user_id,
        "exch": shoonya_exchange,
        "token": token,
    }

    try:
        data = _post_jdata("/GetQuotes", auth_token, payload)

        if data.get("stat") != "Ok":
            raise ValueError(f"Shoonya depth error: {data.get('emsg', 'Unknown')}")

        bids = []
        asks = []
        for i in range(1, 6):
            bids.append({
                "price": float(data.get(f"bp{i}", 0) or 0),
                "quantity": int(float(data.get(f"bq{i}", 0) or 0)),
                "orders": int(float(data.get(f"bo{i}", 0) or 0)),
            })
            asks.append({
                "price": float(data.get(f"sp{i}", 0) or 0),
                "quantity": int(float(data.get(f"sq{i}", 0) or 0)),
                "orders": int(float(data.get(f"so{i}", 0) or 0)),
            })

        return {
            "ltp": float(data.get("lp", 0) or 0),
            "bids": bids,
            "asks": asks,
            "totalbuyqty": int(float(data.get("tbq", 0) or 0)),
            "totalsellqty": int(float(data.get("tsq", 0) or 0)),
        }
    except Exception as e:
        logger.error("Error fetching Shoonya depth for %s/%s: %s", symbol, exchange, e)
        raise


def get_history(
    symbol: str,
    exchange: str,
    interval: str,
    from_date: str,
    to_date: str,
    auth_token: str,
    config: dict | None = None,
) -> list[dict]:
    """Get historical OHLCV candles using Shoonya TPSeries endpoint.

    Chunks requests to avoid server-side timeouts (especially for 1m data).
    Dates are in "DD-MM-YYYY HH:MM:SS" format.
    """
    config = config or {}
    user_id = config.get("client_id", "")

    resolution = _TIMEFRAME_MAP.get(interval)
    if not resolution:
        raise ValueError(f"Unsupported interval: {interval}. Supported: {list(_TIMEFRAME_MAP)}")

    token = _resolve_token(symbol, exchange)
    shoonya_exchange = _get_shoonya_exchange(exchange)

    # Parse input dates
    try:
        from_dt = datetime.strptime(from_date, "%Y-%m-%d")
        to_dt = datetime.strptime(to_date, "%Y-%m-%d")
    except ValueError:
        try:
            from_dt = datetime.strptime(from_date, "%d-%m-%Y")
            to_dt = datetime.strptime(to_date, "%d-%m-%Y")
        except ValueError:
            raise ValueError(f"Unsupported date format: {from_date}. Use YYYY-MM-DD.")

    chunk_size = timedelta(days=_CHUNK_DAYS.get(resolution, 365))
    all_candles: list[dict] = []
    chunk_start = from_dt

    while chunk_start <= to_dt:
        chunk_end = min(chunk_start + chunk_size, to_dt)

        payload = {
            "uid": user_id,
            "exch": shoonya_exchange,
            "token": token,
            "st": str(int(chunk_start.timestamp())),
            "et": str(int((chunk_end + timedelta(days=1)).timestamp())),
            "intrv": resolution,
        }

        try:
            data = _post_chart_jdata("/TPSeries", auth_token, payload)

            if isinstance(data, list):
                for candle in data:
                    try:
                        # Shoonya returns epoch seconds in 'ssboe'
                        ts = int(candle.get("ssboe", 0))
                        all_candles.append({
                            "timestamp": ts,
                            "open": float(candle.get("into", 0)),
                            "high": float(candle.get("inth", 0)),
                            "low": float(candle.get("intl", 0)),
                            "close": float(candle.get("intc", 0)),
                            "volume": int(float(candle.get("intv", 0))),
                        })
                    except (KeyError, TypeError, ValueError) as e:
                        logger.debug("Skipping malformed candle: %s (%s)", candle, e)

            elif isinstance(data, dict) and data.get("stat") == "Not_Ok":
                logger.warning(
                    "Shoonya history chunk failed (%s-%s): %s",
                    chunk_start.date(), chunk_end.date(), data.get("emsg"),
                )

        except Exception as e:
            logger.error(
                "Error fetching Shoonya history chunk (%s-%s): %s",
                chunk_start.date(), chunk_end.date(), e,
            )

        chunk_start = chunk_end + timedelta(days=1)

    # Sort ascending by timestamp
    all_candles.sort(key=lambda x: x["timestamp"])
    return all_candles
