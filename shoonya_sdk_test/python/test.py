import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from backend.config import get_settings
from backend.models.symbol import SymToken
from backend.strategy.live_auth import resolve_live_auth


DEFAULT_SYMBOLS = [
    "BSXOPT02JUL2678400CE",
    "BSXOPT02JUL2677900CE",
]


class ShoonyaApiError(Exception):
    pass


def _now_ist() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S%z")


def _parse_auth_token(auth_token: str) -> tuple[str, str, str]:
    parts = auth_token.split(":") if auth_token else []
    if len(parts) >= 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], parts[1], parts[0]
    if len(parts) == 1:
        return "", parts[0], ""
    return "", "", ""


async def _load_live_auth(user_id: int) -> str:
    engine = create_async_engine(get_settings().database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession)
    try:
        async with session_factory() as db:
            ctx = await resolve_live_auth(db, user_id=user_id, broker="shoonya")
        if not ctx or not ctx.auth_token:
            raise ShoonyaApiError("No active Shoonya session found in DB")
        return ctx.auth_token
    finally:
        await engine.dispose()


async def _resolve_instruments(user_id: int, symbols: list[str]) -> list[dict[str, str]]:
    engine = create_async_engine(get_settings().database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession)
    try:
        async with session_factory() as db:
            rows = (
                await db.execute(
                    select(SymToken.symbol, SymToken.exchange, SymToken.token)
                    .where(SymToken.symbol.in_(symbols))
                    .order_by(SymToken.symbol.asc())
                )
            ).all()

        found = {r.symbol: {"symbol": r.symbol, "exchange": r.exchange, "token": str(r.token)} for r in rows if r.token}
        instruments: list[dict[str, str]] = []

        for symbol in symbols:
            rec = found.get(symbol)
            if rec:
                instruments.append(rec)

        # Safety sentinel: always include SENSEX index for cross-check.
        instruments.append({"symbol": "SENSEX", "exchange": "BSE", "token": "1"})
        return instruments
    finally:
        await engine.dispose()


def _setup_logger(log_file: Path) -> logging.Logger:
    logger = logging.getLogger("shoonya_getquotes_validator")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    stream_h = logging.StreamHandler(sys.stdout)
    stream_h.setFormatter(fmt)
    logger.addHandler(stream_h)

    file_h = logging.FileHandler(log_file)
    file_h.setFormatter(fmt)
    logger.addHandler(file_h)

    return logger


def _validate_response(
    req_symbol: str,
    req_exchange: str,
    req_token: str,
    response: Any,
) -> dict[str, Any]:
    info: dict[str, Any] = {
        "req_symbol": req_symbol,
        "req_exchange": req_exchange,
        "req_token": str(req_token),
        "ok": False,
        "token_match": False,
        "classification": "unknown",
        "ltp": None,
        "resp_token": None,
        "resp_stat": None,
        "error": None,
    }

    if not isinstance(response, dict):
        info["classification"] = "invalid_payload"
        info["error"] = "SDK returned non-dict payload"
        return info

    info["resp_stat"] = response.get("stat")
    info["resp_token"] = str(response.get("token")) if response.get("token") is not None else None
    info["ltp"] = response.get("lp")

    if response.get("stat") != "Ok":
        info["classification"] = "api_not_ok"
        info["error"] = response.get("emsg", "Unknown API error")
        return info

    info["ok"] = True
    info["token_match"] = str(response.get("token")) == str(req_token)

    if info["token_match"]:
        info["classification"] = "ok"
    elif req_exchange == "BFO" and str(response.get("token")) == "1":
        info["classification"] = "bfo_option_returned_sensex_token"
    else:
        info["classification"] = "token_mismatch"

    return info


def _run_validation_loop(
    api,
    instruments: list[dict[str, str]],
    duration_sec: int,
    per_call_sleep_sec: float,
    jsonl_file: Path,
    logger: logging.Logger,
) -> dict[str, int]:
    started = time.monotonic()
    counts = {
        "total_calls": 0,
        "ok": 0,
        "api_not_ok": 0,
        "token_match": 0,
        "token_mismatch": 0,
        "bfo_option_returned_sensex_token": 0,
        "other_errors": 0,
    }

    with jsonl_file.open("w", encoding="utf-8") as fp:
        while (time.monotonic() - started) < duration_sec:
            for ins in instruments:
                if (time.monotonic() - started) >= duration_sec:
                    break

                symbol = ins["symbol"]
                exch = ins["exchange"]
                token = ins["token"]
                counts["total_calls"] += 1

                try:
                    raw = api.get_quotes(exchange=exch, token=token)
                    result = _validate_response(symbol, exch, token, raw)
                except Exception as e:  # noqa: BLE001
                    result = {
                        "req_symbol": symbol,
                        "req_exchange": exch,
                        "req_token": token,
                        "ok": False,
                        "token_match": False,
                        "classification": "exception",
                        "ltp": None,
                        "resp_token": None,
                        "resp_stat": None,
                        "error": str(e),
                    }

                if result["ok"]:
                    counts["ok"] += 1
                if result["classification"] == "api_not_ok":
                    counts["api_not_ok"] += 1
                if result["token_match"]:
                    counts["token_match"] += 1
                elif result["classification"] == "bfo_option_returned_sensex_token":
                    counts["bfo_option_returned_sensex_token"] += 1
                    counts["token_mismatch"] += 1
                elif result["classification"] in ("token_mismatch",):
                    counts["token_mismatch"] += 1
                elif result["classification"] == "exception":
                    counts["other_errors"] += 1

                row = {
                    "ts": _now_ist(),
                    **result,
                }
                fp.write(json.dumps(row, ensure_ascii=True) + "\n")

                if result["classification"] == "ok":
                    logger.info(
                        "OK %s/%s token=%s ltp=%s",
                        symbol,
                        exch,
                        token,
                        result.get("ltp"),
                    )
                else:
                    logger.warning(
                        "ISSUE %s %s/%s req_token=%s resp_token=%s stat=%s err=%s",
                        result["classification"],
                        symbol,
                        exch,
                        token,
                        result.get("resp_token"),
                        result.get("resp_stat"),
                        result.get("error"),
                    )

                time.sleep(per_call_sleep_sec)

    return counts


def _build_sdk_client(uid: str, jkey: str):
    from NorenRestApiPy.NorenApi import NorenApi

    class ShoonyaApi(NorenApi):
        def __init__(self):
            super().__init__(
                host="https://api.shoonya.com/NorenWClientAPI/",
                websocket="wss://api.shoonya.com/NorenWSTP/",
            )

    api = ShoonyaApi()
    api.set_session(userid=uid, password="", usertoken=jkey)
    return api


async def _async_main(args):
    auth_token = await _load_live_auth(args.user_id)
    uid, jkey, _ = _parse_auth_token(auth_token)
    if not uid or not jkey:
        raise ShoonyaApiError("Invalid Shoonya auth token format in DB")

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        symbols = list(DEFAULT_SYMBOLS)

    instruments = await _resolve_instruments(args.user_id, symbols)
    requested = set(symbols)
    found = {x["symbol"] for x in instruments}
    missing = sorted(requested - found)

    out_dir = Path(args.log_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = out_dir / f"getquotes_validation_{stamp}.log"
    jsonl_file = out_dir / f"getquotes_validation_{stamp}.jsonl"

    logger = _setup_logger(log_file)
    logger.info("Starting Shoonya GetQuotes validation")
    logger.info("Duration=%ss per_call_sleep=%.3fs", args.duration_sec, args.per_call_sleep_sec)
    logger.info("Resolved instruments=%s", instruments)
    if missing:
        logger.warning("Symbols not found in symtoken table: %s", missing)

    api = _build_sdk_client(uid, jkey)
    counts = _run_validation_loop(
        api=api,
        instruments=instruments,
        duration_sec=args.duration_sec,
        per_call_sleep_sec=args.per_call_sleep_sec,
        jsonl_file=jsonl_file,
        logger=logger,
    )

    logger.info("Validation finished")
    logger.info("Summary: %s", counts)
    logger.info("Log file: %s", log_file)
    logger.info("JSONL file: %s", jsonl_file)

    print("\n=== FINAL SUMMARY ===")
    print(json.dumps(counts, indent=2, ensure_ascii=True))
    print(f"log_file={log_file}")
    print(f"jsonl_file={jsonl_file}")


def main():
    parser = argparse.ArgumentParser(description="Shoonya GetQuotes validation loop")
    parser.add_argument("--user-id", type=int, default=1, help="OpenBull user_id")
    parser.add_argument(
        "--symbols",
        type=str,
        default=",".join(DEFAULT_SYMBOLS),
        help="Comma-separated OpenBull symbols to validate",
    )
    parser.add_argument("--duration-sec", type=int, default=300, help="Validation runtime in seconds")
    parser.add_argument(
        "--per-call-sleep-sec",
        type=float,
        default=0.4,
        help="Delay between each GetQuotes call (0.4 sec ~= 150 req/min max)",
    )
    parser.add_argument(
        "--log-dir",
        type=str,
        default="shoonya_sdk_test/python/logs",
        help="Output directory for log/jsonl files",
    )
    args = parser.parse_args()

    asyncio.run(_async_main(args))


if __name__ == "__main__":
    main()
