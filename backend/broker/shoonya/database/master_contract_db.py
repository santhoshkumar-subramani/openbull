"""
Shoonya master contract download and symbol table population.

Downloads zip-compressed symbol files from Shoonya's CDN for NSE, BSE, NFO,
CDS, MCX, and BFO segments, then populates the symtoken table.

Runs in a background thread using asyncio.run() with an isolated engine
(separate from the main app's engine) to avoid event loop conflicts.
"""

import asyncio
import io
import logging
import re
import zipfile
from datetime import datetime
from pathlib import Path

import httpx
import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logger = logging.getLogger(__name__)

TMP_DIR = Path(__file__).resolve().parents[4] / "tmp"
TMP_DIR.mkdir(exist_ok=True)

# Shoonya symbol file URLs (zip compressed)
_SHOONYA_URLS = {
    "NSE": "https://api.shoonya.com/NSE_symbols.txt.zip",
    "NFO": "https://api.shoonya.com/NFO_symbols.txt.zip",
    "CDS": "https://api.shoonya.com/CDS_symbols.txt.zip",
    "MCX": "https://api.shoonya.com/MCX_symbols.txt.zip",
    "BSE": "https://api.shoonya.com/BSE_symbols.txt.zip",
    "BFO": "https://api.shoonya.com/BFO_symbols.txt.zip",
}


def _build_isolated_engine_and_session():
    from backend.config import get_settings
    engine = create_async_engine(get_settings().database_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


# ---- Download ----

def _download_and_extract(output_dir: Path) -> None:
    """Download and unzip all Shoonya symbol files to output_dir."""
    for segment, url in _SHOONYA_URLS.items():
        try:
            logger.info("Downloading Shoonya %s symbols from %s", segment, url)
            resp = httpx.get(url, timeout=60, follow_redirects=True)
            resp.raise_for_status()
            zf = zipfile.ZipFile(io.BytesIO(resp.content))
            zf.extractall(str(output_dir))
            logger.info("Extracted Shoonya %s symbols", segment)
        except Exception as e:
            logger.error("Failed to download Shoonya %s: %s", segment, e)
            raise


# ---- Symbol processors ----

def _format_expiry(date_str: str) -> str | None:
    """Convert DD-MMM-YYYY to DD-MMM-YY (uppercase)."""
    try:
        return datetime.strptime(date_str, "%d-%b-%Y").strftime("%d-%b-%y").upper()
    except (ValueError, TypeError):
        return None


def _handle_strike(strike) -> int | float:
    try:
        val = float(strike)
        return int(val) if val.is_integer() else val
    except (ValueError, TypeError):
        return -1


def _compact_expiry(expiry_str: str) -> str:
    """Convert DD-MMM-YY to DDMMMYY (no hyphens)."""
    return expiry_str.replace("-", "") if expiry_str else ""


def _format_option_symbol(name: str, expiry: str, strike, inst_type: str) -> str:
    ce = _compact_expiry(expiry)
    if isinstance(strike, int):
        strike_str = str(strike)
    elif isinstance(strike, float) and strike.is_integer():
        strike_str = str(int(strike))
    else:
        strike_str = f"{strike:.2f}".rstrip("0").rstrip(".")
    return f"{name}{ce}{strike_str}{inst_type}"


def _format_fut_symbol(name: str, expiry: str) -> str:
    return f"{name}{_compact_expiry(expiry)}FUT"


_COMMON_COLS = [
    "symbol", "brsymbol", "name", "exchange", "brexchange",
    "token", "expiry", "strike", "lotsize", "instrumenttype", "tick_size",
]


def _process_nse(output_dir: Path) -> pd.DataFrame:
    file_path = output_dir / "NSE_symbols.txt"
    df = pd.read_csv(
        file_path,
        usecols=["Exchange", "Token", "LotSize", "Symbol", "TradingSymbol", "Instrument", "TickSize"],
    )
    df.columns = ["exchange", "token", "lotsize", "name", "brsymbol", "instrumenttype", "tick_size"]

    df["symbol"] = df["brsymbol"].apply(
        lambda s: s.replace("-EQ", "").replace("-BE", "") if isinstance(s, str) else s
    )
    df["exchange"] = df.apply(
        lambda r: "NSE_INDEX" if r["instrumenttype"] == "INDEX" else "NSE", axis=1
    )
    df["brexchange"] = df["exchange"]
    df["expiry"] = ""
    df["strike"] = -1
    df["instrumenttype"] = df["instrumenttype"].apply(
        lambda x: "EQ" if x in ("EQ", "BE") else x
    )
    df["lotsize"] = pd.to_numeric(df["lotsize"], errors="coerce").fillna(0).astype(int)
    df["tick_size"] = pd.to_numeric(df["tick_size"], errors="coerce") / 100

    # Normalize index symbols: uppercase, remove spaces/hyphens
    idx_mask = df["exchange"] == "NSE_INDEX"
    df.loc[idx_mask, "symbol"] = (
        df.loc[idx_mask, "symbol"]
        .str.upper()
        .str.replace(" ", "", regex=False)
        .str.replace("-", "", regex=False)
    )
    df.loc[idx_mask, "symbol"] = df.loc[idx_mask, "symbol"].replace({
        "NIFTY50": "NIFTY",
        "NIFTYINDEX": "NIFTY",
        "NIFTYBANK": "BANKNIFTY",
        "NIFTYFIN": "FINNIFTY",
        "NIFTYFINSERVICE": "FINNIFTY",
        "NIFTYFINANCIALSERVICES": "FINNIFTY",
        "NIFTYNEXT50": "NIFTYNXT50",
        "NIFTYMIDSELECT": "MIDCPNIFTY",
        "NIFTYMIDCAPSELECT": "MIDCPNIFTY",
    })

    return df[_COMMON_COLS]


def _process_bse(output_dir: Path) -> pd.DataFrame:
    file_path = output_dir / "BSE_symbols.txt"
    df = pd.read_csv(
        file_path,
        usecols=["Exchange", "Token", "LotSize", "Symbol", "TradingSymbol", "Instrument", "TickSize"],
    )
    df.columns = ["exchange", "token", "lotsize", "name", "brsymbol", "instrumenttype", "tick_size"]

    df["symbol"] = df["brsymbol"]
    df["exchange"] = "BSE"
    df["brexchange"] = "BSE"
    df["expiry"] = ""
    df["strike"] = -1
    df["instrumenttype"] = "EQ"
    df["lotsize"] = pd.to_numeric(df["lotsize"], errors="coerce").fillna(0).astype(int)
    df["tick_size"] = pd.to_numeric(df["tick_size"], errors="coerce") / 100

    equities = df[_COMMON_COLS].copy()

    # Manually add BSE index tokens
    bse_indices = pd.DataFrame([
        {
            "symbol": "SENSEX", "brsymbol": "SENSEX", "name": "SENSEX",
            "exchange": "BSE_INDEX", "brexchange": "BSE_INDEX",
            "token": "1", "expiry": "", "strike": -1,
            "lotsize": 1, "instrumenttype": "INDEX", "tick_size": 0.05,
        },
        {
            "symbol": "BANKEX", "brsymbol": "BANKEX", "name": "BANKEX",
            "exchange": "BSE_INDEX", "brexchange": "BSE_INDEX",
            "token": "12", "expiry": "", "strike": -1,
            "lotsize": 1, "instrumenttype": "INDEX", "tick_size": 0.05,
        },
    ])

    return pd.concat([equities, bse_indices], ignore_index=True)


def _process_nfo(output_dir: Path) -> pd.DataFrame:
    file_path = output_dir / "NFO_symbols.txt"
    df = pd.read_csv(
        file_path,
        usecols=["Exchange", "Token", "LotSize", "Symbol", "TradingSymbol",
                 "Expiry", "Instrument", "OptionType", "StrikePrice", "TickSize"],
    )
    df.columns = ["exchange", "token", "lotsize", "name", "brsymbol",
                  "expiry", "instrumenttype", "optiontype", "strike", "tick_size"]

    df["expiry"] = df["expiry"].fillna("").apply(
        lambda x: _format_expiry(x) if x else ""
    )
    df["strike"] = df["strike"].fillna(-1).apply(_handle_strike)
    df["instrumenttype"] = df.apply(
        lambda r: "FUT" if r["optiontype"] == "XX" else r["optiontype"], axis=1
    )

    def fmt(row):
        if row["instrumenttype"] == "FUT":
            return _format_fut_symbol(row["name"], row["expiry"])
        return _format_option_symbol(row["name"], row["expiry"], row["strike"], row["instrumenttype"])

    df["symbol"] = df.apply(fmt, axis=1)
    df["exchange"] = "NFO"
    df["brexchange"] = "NFO"
    df["lotsize"] = pd.to_numeric(df["lotsize"], errors="coerce").fillna(0).astype(int)
    df["tick_size"] = pd.to_numeric(df["tick_size"], errors="coerce") / 100

    return df[_COMMON_COLS]


def _process_cds(output_dir: Path) -> pd.DataFrame:
    file_path = output_dir / "CDS_symbols.txt"
    df = pd.read_csv(
        file_path,
        usecols=["Exchange", "Token", "LotSize", "Precision", "Multiplier",
                 "Symbol", "TradingSymbol", "Expiry", "Instrument",
                 "OptionType", "StrikePrice", "TickSize"],
    )
    df.columns = ["exchange", "token", "lotsize", "precision", "multiplier",
                  "name", "brsymbol", "expiry", "instrumenttype",
                  "optiontype", "strike", "tick_size"]

    # Filter out dummy entries (low token numbers)
    df = df[pd.to_numeric(df["token"], errors="coerce") > 100]

    df["expiry"] = df["expiry"].fillna("").apply(
        lambda x: _format_expiry(x) if x else ""
    )
    df["strike"] = df["strike"].fillna(-1).apply(_handle_strike)
    df["instrumenttype"] = df.apply(
        lambda r: "FUT" if r["optiontype"] == "XX" else r["instrumenttype"], axis=1
    )
    df["instrumenttype"] = df.apply(
        lambda r: r["optiontype"] if r["instrumenttype"] == "OPTCUR" else r["instrumenttype"], axis=1
    )

    def fmt(row):
        if row["instrumenttype"] == "FUT":
            return _format_fut_symbol(row["name"], row["expiry"])
        return _format_option_symbol(row["name"], row["expiry"], row["strike"], row["instrumenttype"])

    df["symbol"] = df.apply(fmt, axis=1)
    df["exchange"] = "CDS"
    df["brexchange"] = "CDS"
    df["lotsize"] = pd.to_numeric(df["lotsize"], errors="coerce").fillna(0).astype(int)
    df["tick_size"] = pd.to_numeric(df["tick_size"], errors="coerce") / 100

    return df[_COMMON_COLS]


def _process_mcx(output_dir: Path) -> pd.DataFrame:
    file_path = output_dir / "MCX_symbols.txt"
    df = pd.read_csv(
        file_path,
        usecols=["Exchange", "Token", "LotSize", "GNGD", "Symbol", "TradingSymbol",
                 "Expiry", "Instrument", "OptionType", "StrikePrice", "TickSize"],
    )
    df.columns = ["exchange", "token", "lotsize", "gngd", "name", "brsymbol",
                  "expiry", "instrumenttype", "optiontype", "strike", "tick_size"]

    df["expiry"] = df["expiry"].fillna("").apply(
        lambda x: _format_expiry(x) if x else ""
    )
    df["strike"] = df["strike"].fillna(-1).apply(_handle_strike)
    df["instrumenttype"] = df.apply(
        lambda r: "FUT" if r["optiontype"] == "XX" else r["instrumenttype"], axis=1
    )
    df["instrumenttype"] = df.apply(
        lambda r: r["optiontype"] if r["instrumenttype"] == "OPTFUT" else r["instrumenttype"], axis=1
    )

    def fmt(row):
        if row["instrumenttype"] == "FUT":
            return _format_fut_symbol(row["name"], row["expiry"])
        return _format_option_symbol(row["name"], row["expiry"], row["strike"], row["instrumenttype"])

    df["symbol"] = df.apply(fmt, axis=1)
    df["exchange"] = "MCX"
    df["brexchange"] = "MCX"
    df["lotsize"] = pd.to_numeric(df["lotsize"], errors="coerce").fillna(0).astype(int)
    df["tick_size"] = pd.to_numeric(df["tick_size"], errors="coerce") / 100

    return df[_COMMON_COLS]


def _process_bfo(output_dir: Path) -> pd.DataFrame:
    file_path = output_dir / "BFO_symbols.txt"
    df = pd.read_csv(
        file_path,
        usecols=["Exchange", "Token", "LotSize", "Symbol", "TradingSymbol",
                 "Expiry", "Instrument", "OptionType", "StrikePrice", "TickSize"],
    )
    df.columns = ["exchange", "token", "lotsize", "name", "brsymbol",
                  "expiry", "instrumenttype", "optiontype", "strike", "tick_size"]

    df["expiry"] = df["expiry"].fillna("").apply(
        lambda x: _format_expiry(x) if x else ""
    )
    df["strike"] = df["strike"].fillna(-1).apply(_handle_strike)

    # Extract underlying from the trading symbol. Keep digit-bearing names
    # such as SENSEX50 instead of truncating them to leading letters only.
    def extract_underlying(s: str) -> str:
        if not isinstance(s, str):
            return s
        m = re.match(r"^([A-Z0-9]+?)(\d{2}[A-Z]{3}\d{2})", s.upper())
        if m:
            return m.group(1)
        m = re.match(r"^([A-Z0-9]+)", s.upper())
        return m.group(1) if m else s

    df["name"] = df["brsymbol"].apply(extract_underlying)

    def extract_inst(s: str) -> str:
        if isinstance(s, str):
            if s.endswith("FUT"):
                return "FUT"
            if s.endswith("CE"):
                return "CE"
            if s.endswith("PE"):
                return "PE"
        return "UNKNOWN"

    df["instrumenttype"] = df["brsymbol"].apply(extract_inst)

    def fmt(row):
        if row["instrumenttype"] == "FUT":
            return _format_fut_symbol(row["name"], row["expiry"])
        return _format_option_symbol(row["name"], row["expiry"], row["strike"], row["instrumenttype"])

    df["symbol"] = df.apply(fmt, axis=1)
    df["exchange"] = "BFO"
    df["brexchange"] = "BFO"
    df["lotsize"] = pd.to_numeric(df["lotsize"], errors="coerce").fillna(0).astype(int)
    df["tick_size"] = pd.to_numeric(df["tick_size"], errors="coerce") / 100

    return df[_COMMON_COLS]


# ---- Database write ----

async def _bulk_insert(df: pd.DataFrame, session_factory) -> int:
    """Insert DataFrame rows into symtoken, skipping existing (token, exchange) pairs."""
    # Ensure token is stored as string (DB column is VARCHAR) and other
    # numpy scalar types are converted to native Python types so asyncpg
    # can serialize them correctly.
    df = df.copy()
    df["token"] = df["token"].astype(str)

    records = df.to_dict(orient="records")
    if not records:
        return 0

    # asyncpg cannot serialize numpy scalars or NaN in string columns —
    # convert to native Python types so asyncpg can serialize correctly.
    import math
    import numpy as np
    def _to_native(v):
        if isinstance(v, (np.integer,)):
            return int(v)
        if isinstance(v, (np.floating,)):
            return None if np.isnan(v) else float(v)
        if isinstance(v, float) and math.isnan(v):
            return None
        return v

    records = [{k: _to_native(v) for k, v in r.items()} for r in records]

    async with session_factory() as session:
        # Fetch existing (token, exchange) to deduplicate
        result = await session.execute(
            text("SELECT token, exchange FROM symtoken")
        )
        existing = {(row[0], row[1]) for row in result.fetchall()}

    new_records = [
        r for r in records
        if (str(r.get("token", "")), str(r.get("exchange", ""))) not in existing
    ]

    if not new_records:
        return 0

    async with session_factory() as session:
        await session.execute(
            text("""
                INSERT INTO symtoken
                  (symbol, brsymbol, name, exchange, brexchange, token,
                   expiry, strike, lotsize, instrumenttype, tick_size)
                VALUES
                  (:symbol, :brsymbol, :name, :exchange, :brexchange, :token,
                   :expiry, :strike, :lotsize, :instrumenttype, :tick_size)
            """),
            new_records,
        )
        await session.commit()

    return len(new_records)


async def _run_download(auth_token: str) -> dict:
    """Async entry point: download, process, and persist all Shoonya contracts."""
    engine, factory = _build_isolated_engine_and_session()
    try:
        output_dir = TMP_DIR / "shoonya"
        output_dir.mkdir(exist_ok=True)

        # Download
        _download_and_extract(output_dir)

        # Clear existing Shoonya contracts from symtoken
        async with factory() as session:
            await session.execute(
                text("""
                    DELETE FROM symtoken
                    WHERE exchange IN
                      ('NSE','BSE','NFO','BFO','CDS','MCX','NSE_INDEX','BSE_INDEX')
                """)
            )
            await session.commit()

        processors = [
            ("NSE", _process_nse),
            ("BSE", _process_bse),
            ("NFO", _process_nfo),
            ("CDS", _process_cds),
            ("MCX", _process_mcx),
            ("BFO", _process_bfo),
        ]

        total_inserted = 0
        for segment, fn in processors:
            try:
                df = fn(output_dir)
                count = await _bulk_insert(df, factory)
                total_inserted += count
                logger.info("Shoonya %s: inserted %d symbols", segment, count)
            except Exception as e:
                logger.error("Error processing Shoonya %s: %s", segment, e)

        # Mirror the freshly-inserted rows into Redis, then reload in-memory dicts.
        # Quote/order paths use these caches for OpenBull symbol -> Shoonya token.
        from backend.utils import symtoken_cache
        from backend.broker.upstox.mapping.order_data import _load_symbol_cache
        await symtoken_cache.warm_from_db()
        await _load_symbol_cache()

        return {"status": "success", "count": total_inserted}

    except Exception as e:
        logger.exception("Shoonya master contract download failed")
        return {"status": "error", "message": str(e)}
    finally:
        await engine.dispose()


def master_contract_download(auth_token: str | None = None) -> dict:
    """Entry point called by symbol_service in a background thread.

    Spins up a temporary asyncio event loop so asyncpg calls work in a
    plain threading.Thread (not the main uvicorn loop).
    """
    return asyncio.run(_run_download(auth_token or ""))


async def search_symbols(symbol: str, exchange: str) -> list[dict]:
    """Search symtoken for Shoonya symbols matching the query on the given exchange.

    Mirrors the tokenized search behavior used by the mature broker loaders so
    broker-agnostic symbol search accepts loose queries such as "NIFTY 28APR26".
    """
    tokens = [t for t in symbol.split() if t][:6]
    if not tokens:
        return []

    where_parts = ["exchange = :exchange"]
    params: dict = {"exchange": exchange, "prefix": f"{tokens[0]}%"}
    for i, tok in enumerate(tokens):
        key = f"t{i}"
        where_parts.append(
            f"(symbol ILIKE :{key} OR brsymbol ILIKE :{key} OR name ILIKE :{key})"
        )
        params[key] = f"%{tok}%"

    sql = (
        "SELECT symbol, brsymbol, name, exchange, brexchange, token, "
        "expiry, strike, lotsize, instrumenttype, tick_size "
        "FROM symtoken WHERE " + " AND ".join(where_parts) + " "
        "ORDER BY "
        "  CASE WHEN symbol ILIKE :prefix THEN 0 ELSE 1 END, "
        "  LENGTH(symbol), symbol "
        "LIMIT 50"
    )

    engine, factory = _build_isolated_engine_and_session()
    try:
        async with factory() as session:
            result = await session.execute(text(sql), params)
            rows = result.fetchall()
    finally:
        await engine.dispose()

    return [
        {
            "symbol": row[0],
            "brsymbol": row[1],
            "name": row[2],
            "exchange": row[3],
            "brexchange": row[4],
            "token": row[5],
            "expiry": row[6],
            "strike": row[7],
            "lotsize": row[8],
            "instrumenttype": row[9],
            "tick_size": row[10],
        }
        for row in rows
    ]
