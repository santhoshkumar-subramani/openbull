"""
Shoonya market data API — quotes, depth, historical candles.

Public surface (matches openbull contract):
  - TIMEFRAME_MAP (module-level dict)
  - get_quotes(symbol, exchange, auth_token, config=None) -> dict
  - get_multi_quotes(symbols_list, auth_token, config=None) -> list[dict]
  - get_market_depth(symbol, exchange, auth_token, config=None) -> dict
  - get_history(symbol, exchange, interval, start_date, end_date, auth_token, config=None) -> list[dict]
"""

import json
import logging
import time
from datetime import datetime

from backend.broker.upstox.mapping.order_data import (
    get_brsymbol_from_cache,
    get_token_from_cache,
)
from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)

BASE_URL = "https://api.shoonya.com/NorenWClientTP"

TIMEFRAME_MAP = {
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

# Shoonya quote API has no batch mode; throttle serial calls.
_QUOTE_RATE_DELAY = 0.15


def _split_token(auth_token: str) -> tuple[str, str, str]:
    """Split combined ``userid:susertoken:actid``."""
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
        logger.error("Shoonya non-JSON on %s (status=%s): %s",
                     endpoint, response.status_code, response.text[:200])
        return {"stat": "Not_Ok", "emsg": f"Invalid JSON (HTTP {response.status_code})"}


def _api_exchange(exchange: str) -> str:
    """Map INDEX-segmented exchanges back to bare codes Shoonya expects."""
    if exchange == "NSE_INDEX":
        return "NSE"
    if exchange == "BSE_INDEX":
        return "BSE"
    if exchange == "MCX_INDEX":
        return "MCX"
    return exchange


# ---------- Quotes ----------

def get_quotes(
    symbol: str, exchange: str, auth_token: str, config: dict | None = None
) -> dict:
    """Fetch a single LTP/OHLC quote."""
    uid, jkey, _ = _split_token(auth_token)
    token = get_token_from_cache(symbol, exchange)
    if not token:
        raise Exception(f"Instrument token not found for {symbol}/{exchange}")

    api_exch = _api_exchange(exchange)
    payload = {"uid": uid, "exch": api_exch, "token": token}

    result = _post("GetQuotes", payload, jkey)
    if result.get("stat") != "Ok":
        raise Exception(f"Error from Shoonya: {result.get('emsg', 'Unknown error')}")

    return {
        "bid": float(result.get("bp1", 0) or 0),
        "ask": float(result.get("sp1", 0) or 0),
        "open": float(result.get("o", 0) or 0),
        "high": float(result.get("h", 0) or 0),
        "low": float(result.get("l", 0) or 0),
        "ltp": float(result.get("lp", 0) or 0),
        "prev_close": float(result.get("c", 0) or 0),
        "volume": int(result.get("v", 0) or 0),
        "oi": int(result.get("oi", 0) or 0),
    }


def get_multi_quotes(
    symbols_list: list[dict], auth_token: str, config: dict | None = None
) -> list[dict]:
    """Fetch quotes for many symbols.

    Shoonya doesn't have a batch quote endpoint so we call GetQuotes serially.
    """
    if not symbols_list:
        return []

    results: list[dict] = []
    for i, item in enumerate(symbols_list):
        sym = item.get("symbol")
        exch = item.get("exchange")
        if not sym or not exch:
            continue

        try:
            quote = get_quotes(sym, exch, auth_token, config)
            quote["symbol"] = sym
            quote["exchange"] = exch
            results.append(quote)
        except Exception as e:
            logger.warning("Shoonya multi-quote skip %s/%s: %s", sym, exch, e)
            results.append({"symbol": sym, "exchange": exch, "error": str(e)})

        # Rate-limit between calls.
        if i < len(symbols_list) - 1:
            time.sleep(_QUOTE_RATE_DELAY)

    return results


# ---------- Market depth ----------

def get_market_depth(
    symbol: str, exchange: str, auth_token: str, config: dict | None = None
) -> dict:
    """5-level market depth (bids / asks / OHLC / volume / OI)."""
    uid, jkey, _ = _split_token(auth_token)
    token = get_token_from_cache(symbol, exchange)
    if not token:
        raise Exception(f"Instrument token not found for {symbol}/{exchange}")

    api_exch = _api_exchange(exchange)
    payload = {"uid": uid, "exch": api_exch, "token": token}

    result = _post("GetQuotes", payload, jkey)
    if result.get("stat") != "Ok":
        raise Exception(f"Error from Shoonya: {result.get('emsg', 'Unknown error')}")

    bids: list[dict] = []
    asks: list[dict] = []
    for i in range(1, 6):
        bids.append({
            "price": float(result.get(f"bp{i}", 0) or 0),
            "quantity": int(result.get(f"bq{i}", 0) or 0),
            "orders": int(result.get(f"bo{i}", 0) or 0),
        })
        asks.append({
            "price": float(result.get(f"sp{i}", 0) or 0),
            "quantity": int(result.get(f"sq{i}", 0) or 0),
            "orders": int(result.get(f"so{i}", 0) or 0),
        })

    return {
        "bids": bids,
        "asks": asks,
        "high": float(result.get("h", 0) or 0),
        "low": float(result.get("l", 0) or 0),
        "ltp": float(result.get("lp", 0) or 0),
        "ltq": int(result.get("ltq", 0) or 0),
        "open": float(result.get("o", 0) or 0),
        "prev_close": float(result.get("c", 0) or 0),
        "close": float(result.get("c", 0) or 0),
        "volume": int(result.get("v", 0) or 0),
        "oi": int(result.get("oi", 0) or 0),
        "totalbuyqty": 0,
        "totalsellqty": 0,
    }


# ---------- Historical candles ----------

def _parse_shoonya_ts(time_str: str) -> int:
    """Convert Shoonya time strings to Unix epoch.

    Shoonya returns two formats:
      - Intraday (TPSeries): ``"DD-MM-YYYY HH:MM:SS"`` or ``"DD/MM/YYYY HH:MM:SS"``
      - Daily (get_daily_price_series): ``ssboe`` field (seconds since epoch)
    """
    if not time_str:
        return 0

    for fmt in ("%d-%m-%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%d-%b-%Y"):
        try:
            return int(datetime.strptime(time_str, fmt).timestamp())
        except (ValueError, TypeError):
            continue

    # Maybe it's already epoch seconds (from ssboe).
    try:
        return int(float(time_str))
    except (ValueError, TypeError):
        return 0


def get_history(
    symbol: str,
    exchange: str,
    interval: str,
    start_date: str,
    end_date: str,
    auth_token: str,
    config: dict | None = None,
) -> list[dict]:
    """Fetch historical OHLCV(+OI) candles.

    For daily candles, uses Shoonya's ``get_daily_price_series`` (chart API).
    For intraday, uses ``TPSeries`` endpoint.
    """
    if interval not in TIMEFRAME_MAP:
        raise ValueError(
            f"Unsupported interval: {interval}. Supported: {list(TIMEFRAME_MAP.keys())}"
        )

    uid, jkey, _ = _split_token(auth_token)
    token = get_token_from_cache(symbol, exchange)
    if not token:
        raise Exception(f"Instrument token not found for {symbol}/{exchange}")

    api_exch = _api_exchange(exchange)

    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    if interval == "D":
        return _fetch_daily(
            uid, jkey, api_exch, symbol, token,
            int(start_dt.timestamp()), int(end_dt.timestamp()),
        )

    return _fetch_intraday(
        uid, jkey, api_exch, token,
        int(start_dt.timestamp()), int(end_dt.timestamp()),
        TIMEFRAME_MAP[interval],
    )


def _fetch_intraday(
    uid: str, jkey: str, exchange: str, token: str,
    start_epoch: int, end_epoch: int, intrv: str,
) -> list[dict]:
    """Fetch intraday candles via TPSeries."""
    payload = {
        "uid": uid,
        "exch": exchange,
        "token": token,
        "st": str(start_epoch),
        "et": str(end_epoch),
        "intrv": intrv,
    }

    result = _post("TPSeries", payload, jkey)

    if isinstance(result, dict) and result.get("stat") == "Not_Ok":
        logger.error("Shoonya TPSeries error: %s", result.get("emsg"))
        return []

    if not isinstance(result, list):
        return []

    candles: list[dict] = []
    for row in result:
        if not isinstance(row, dict):
            continue
        ts = _parse_shoonya_ts(row.get("time", ""))
        candles.append({
            "timestamp": ts,
            "open": float(row.get("into", 0) or 0),
            "high": float(row.get("inth", 0) or 0),
            "low": float(row.get("intl", 0) or 0),
            "close": float(row.get("intc", 0) or 0),
            "volume": int(float(row.get("intv", 0) or 0)),
            "oi": int(row.get("oi", 0) or 0),
        })

    # Sort ascending, deduplicate.
    seen: set[int] = set()
    unique: list[dict] = []
    for c in sorted(candles, key=lambda x: x["timestamp"]):
        if c["timestamp"] not in seen:
            seen.add(c["timestamp"])
            unique.append(c)
    return unique


def _fetch_daily(
    uid: str, jkey: str, exchange: str, symbol: str, token: str,
    start_epoch: int, end_epoch: int,
) -> list[dict]:
    """Fetch daily candles via Shoonya chart API.

    Shoonya's daily endpoint (get_daily_price_series) is at a different URL
    and uses tradingsymbol rather than token.
    """
    brsymbol = get_brsymbol_from_cache(symbol, exchange) or symbol

    client = get_httpx_client()
    payload = json.dumps({
        "sym": f"{exchange}:{brsymbol}",
        "from": str(start_epoch),
        "to": str(end_epoch),
    })

    try:
        response = client.post(
            "https://api.shoonya.com/chartapi/getdata/",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        result = response.json()
    except Exception as e:
        logger.error("Shoonya daily chart API error: %s", e)
        return []

    if not isinstance(result, list):
        return []

    candles: list[dict] = []
    for row_str in result:
        # Daily API returns array of JSON strings (!).
        if isinstance(row_str, str):
            try:
                row = json.loads(row_str)
            except json.JSONDecodeError:
                continue
        elif isinstance(row_str, dict):
            row = row_str
        else:
            continue

        ts = int(row.get("ssboe", 0) or 0)
        if not ts:
            ts = _parse_shoonya_ts(row.get("time", ""))

        candles.append({
            "timestamp": ts,
            "open": float(row.get("into", 0) or 0),
            "high": float(row.get("inth", 0) or 0),
            "low": float(row.get("intl", 0) or 0),
            "close": float(row.get("intc", 0) or 0),
            "volume": int(float(row.get("intv", 0) or 0)),
            "oi": 0,
        })

    seen: set[int] = set()
    unique: list[dict] = []
    for c in sorted(candles, key=lambda x: x["timestamp"]):
        if c["timestamp"] not in seen:
            seen.add(c["timestamp"])
            unique.append(c)
    return unique
