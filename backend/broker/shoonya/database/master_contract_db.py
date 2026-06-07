"""
Shoonya master contract download and symbol table population.

Downloads per-exchange ZIP files from api.shoonya.com, extracts TSV data,
normalizes symbols to OpenBull canonical format, and bulk-inserts into the
symtoken table.

Runs in a background thread. Uses asyncio.run() with a dedicated async engine
(same pattern as angel/upstox/zerodha).
"""

import asyncio
import io
import logging
import os
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

SHOONYA_MASTER_URLS = {
    "NSE": "https://api.shoonya.com/NSE_symbols.txt.zip",
    "NFO": "https://api.shoonya.com/NFO_symbols.txt.zip",
    "BSE": "https://api.shoonya.com/BSE_symbols.txt.zip",
    "BFO": "https://api.shoonya.com/BFO_symbols.txt.zip",
    "CDS": "https://api.shoonya.com/CDS_symbols.txt.zip",
    "MCX": "https://api.shoonya.com/MCX_symbols.txt.zip",
}


def _build_isolated_engine_and_session():
    """Create a fresh engine + sessionmaker for use under asyncio.run()."""
    from backend.config import get_settings
    engine = create_async_engine(get_settings().database_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


def _download_and_extract(url: str, exchange: str) -> pd.DataFrame | None:
    """Download a ZIP and extract the first .txt file into a DataFrame."""
    logger.info("Downloading Shoonya %s master from %s", exchange, url)
    try:
        response = httpx.get(url, timeout=60, follow_redirects=True)
        response.raise_for_status()
    except Exception as e:
        logger.error("Failed to download %s master: %s", exchange, e)
        return None

    try:
        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            names = zf.namelist()
            txt_name = next((n for n in names if n.endswith(".txt")), names[0] if names else None)
            if not txt_name:
                logger.error("No .txt file found in %s ZIP", exchange)
                return None

            with zf.open(txt_name) as f:
                df = pd.read_csv(f, sep=",", dtype=str)

        logger.info("Shoonya %s master: %d rows", exchange, len(df))
        return df
    except Exception as e:
        logger.error("Failed to parse %s master: %s", exchange, e)
        return None


def _convert_expiry(date_str: str | None) -> str | None:
    """Convert from various formats to 'DD-MMM-YY' uppercase.

    Shoonya uses multiple expiry date formats depending on the exchange.
    """
    if not date_str or pd.isna(date_str):
        return None

    date_str = str(date_str).strip()
    if not date_str:
        return None

    for fmt in ("%d-%b-%Y", "%d-%b-%y", "%d-%m-%Y", "%Y-%m-%d", "%d%b%Y"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%d-%b-%y").upper()
        except (ValueError, TypeError):
            continue

    return date_str.upper()


def _process_exchange(df: pd.DataFrame, exchange: str) -> pd.DataFrame:
    """Process a single exchange's master data into symtoken schema.

    Shoonya master file columns (typical):
      Exchange, Token, LotSize, Symbol, TradingSymbol, Expiry, Instrument,
      OptionType, StrikePrice, TickSize

    Column names vary slightly; we normalize by position + name matching.
    """
    if df is None or df.empty:
        return pd.DataFrame()

    # Standardize column names (lowercase, stripped).
    df.columns = [c.strip().lower() for c in df.columns]

    # Map to expected names. Shoonya uses various column names across exchanges.
    col_map = {
        "exchange": "exchange",
        "exch": "exchange",
        "token": "token",
        "lotsize": "lotsize",
        "lot_size": "lotsize",
        "symbol": "name",
        "symbolname": "name",
        "tradingsymbol": "brsymbol",
        "trading_symbol": "brsymbol",
        "tsym": "brsymbol",
        "expiry": "expiry",
        "instrument": "instrumenttype",
        "instrumentname": "instrumenttype",
        "instname": "instrumenttype",
        "optiontype": "optiontype",
        "option_type": "optiontype",
        "optt": "optiontype",
        "strikeprice": "strike",
        "strike_price": "strike",
        "strprc": "strike",
        "ticksize": "tick_size",
        "tick_size": "tick_size",
    }

    renamed = {}
    for old_col in df.columns:
        canonical = col_map.get(old_col)
        if canonical:
            renamed[old_col] = canonical
    df = df.rename(columns=renamed)

    # Ensure required columns exist.
    for col in ("token", "brsymbol", "name"):
        if col not in df.columns:
            logger.warning("Shoonya %s master missing column '%s'", exchange, col)
            return pd.DataFrame()

    # Fill defaults.
    if "exchange" not in df.columns:
        df["exchange"] = exchange
    if "lotsize" not in df.columns:
        df["lotsize"] = "1"
    if "tick_size" not in df.columns:
        df["tick_size"] = "0.05"
    if "expiry" not in df.columns:
        df["expiry"] = None
    if "strike" not in df.columns:
        df["strike"] = "0"
    if "instrumenttype" not in df.columns:
        df["instrumenttype"] = "EQ"
    if "optiontype" not in df.columns:
        df["optiontype"] = ""

    df["brexchange"] = df["exchange"]

    # Clean and type-cast.
    df["token"] = df["token"].astype(str).str.strip()
    df["brsymbol"] = df["brsymbol"].astype(str).str.strip()
    df["name"] = df["name"].astype(str).str.strip()
    df["exchange"] = df["exchange"].astype(str).str.strip()

    try:
        df["lotsize"] = pd.to_numeric(df["lotsize"], errors="coerce").fillna(1).astype(int)
    except Exception:
        df["lotsize"] = 1

    try:
        df["tick_size"] = pd.to_numeric(df["tick_size"], errors="coerce").fillna(0.05)
    except Exception:
        df["tick_size"] = 0.05

    try:
        df["strike"] = pd.to_numeric(df["strike"], errors="coerce").fillna(0.0)
    except Exception:
        df["strike"] = 0.0

    # Normalize expiry.
    df["expiry"] = df["expiry"].apply(_convert_expiry)

    # ---- Symbol construction (OpenBull canonical format) ----

    # Start with broker symbol as base, then apply transformations.
    df["symbol"] = df["brsymbol"]

    # Classify instrument types.
    inst = df["instrumenttype"].str.upper().fillna("")
    opt_type = df["optiontype"].str.upper().fillna("")

    # Index rows.
    is_index = inst.isin(["UNDIND", "INDEX", "AMXIDX"])
    df.loc[is_index & (df["exchange"] == "NSE"), "exchange"] = "NSE_INDEX"
    df.loc[is_index & (df["exchange"] == "BSE"), "exchange"] = "BSE_INDEX"

    # Equity: strip -EQ/-BE/-MF/-SG suffixes.
    is_eq = inst.isin(["EQ", ""])
    df.loc[is_eq, "symbol"] = (
        df.loc[is_eq, "brsymbol"].str.replace(r"-EQ|-BE|-MF|-SG|-BL|-BZ|-GS|-IL|-IV", "", regex=True)
    )

    # Futures: construct NAME + EXPIRY + FUT.
    is_fut = inst.str.startswith("FUT")
    if is_fut.any():
        df.loc[is_fut, "symbol"] = (
            df.loc[is_fut, "name"]
            + df.loc[is_fut, "expiry"].fillna("").str.replace("-", "", regex=False)
            + "FUT"
        )
        df.loc[is_fut, "instrumenttype"] = "FUT"

    # Options: construct NAME + EXPIRY + STRIKE + CE/PE.
    is_opt = inst.str.startswith("OPT")
    if is_opt.any():
        strike_str = (
            df.loc[is_opt, "strike"]
            .astype(str)
            .str.replace(r"\.0$", "", regex=True)
        )
        df.loc[is_opt, "symbol"] = (
            df.loc[is_opt, "name"]
            + df.loc[is_opt, "expiry"].fillna("").str.replace("-", "", regex=False)
            + strike_str
            + opt_type.loc[is_opt]
        )
        # Map instrument type to CE/PE.
        df.loc[is_opt & (opt_type == "CE"), "instrumenttype"] = "CE"
        df.loc[is_opt & (opt_type == "PE"), "instrumenttype"] = "PE"

    # NSE_INDEX symbol normalization.
    nse_idx = df["exchange"] == "NSE_INDEX"
    if nse_idx.any():
        df.loc[nse_idx, "symbol"] = (
            df.loc[nse_idx, "name"]
            .str.upper()
            .str.replace(" ", "", regex=False)
            .str.replace("-", "", regex=False)
        )

    # BSE_INDEX symbol normalization.
    bse_idx = df["exchange"] == "BSE_INDEX"
    if bse_idx.any():
        df.loc[bse_idx, "symbol"] = (
            df.loc[bse_idx, "name"]
            .str.upper()
            .str.replace("S&P ", "", regex=False)
            .str.replace(" ", "", regex=False)
            .str.replace("-", "", regex=False)
        )

    # Major-index aliases.
    df["symbol"] = df["symbol"].replace({
        "NIFTY50": "NIFTY",
        "NIFTYBANK": "BANKNIFTY",
        "NIFTYFINSERVICE": "FINNIFTY",
        "NIFTYNEXT50": "NIFTYNXT50",
        "NIFTYMIDSELECT": "MIDCPNIFTY",
        "NIFTYMIDCAPSELECT": "MIDCPNIFTY",
        "SNSX50": "SENSEX50",
    })

    # Select final columns.
    result = df[[
        "symbol", "brsymbol", "name", "exchange", "brexchange",
        "token", "expiry", "strike", "lotsize", "instrumenttype", "tick_size",
    ]].copy()

    return result


def master_contract_download(auth_token: str | None = None) -> dict:
    """Download Shoonya master contracts and populate symtoken table.

    ``auth_token`` is accepted for signature parity but Shoonya's master
    files are publicly hosted; no auth needed.
    """
    all_frames: list[pd.DataFrame] = []

    for exchange, url in SHOONYA_MASTER_URLS.items():
        df = _download_and_extract(url, exchange)
        if df is not None and not df.empty:
            processed = _process_exchange(df, exchange)
            if not processed.empty:
                all_frames.append(processed)

    if not all_frames:
        return {"status": "error", "message": "No master contract data downloaded"}

    token_df = pd.concat(all_frames, ignore_index=True)

    # Drop rows with empty token or symbol.
    token_df = token_df[
        token_df["token"].notna()
        & (token_df["token"] != "")
        & token_df["symbol"].notna()
        & (token_df["symbol"] != "")
    ]

    logger.info("Total Shoonya master records: %d", len(token_df))

    try:
        async def _db_ops():
            engine, session_factory = _build_isolated_engine_and_session()
            try:
                async with session_factory() as session:
                    async with session.begin():
                        logger.info("Clearing symtoken table")
                        await session.execute(text("DELETE FROM symtoken"))
                        data_dict = token_df.to_dict(orient="records")

                        import math
                        for row in data_dict:
                            for k, v in row.items():
                                if isinstance(v, float) and math.isnan(v):
                                    row[k] = None

                        logger.info("Performing bulk insert of %d records", len(data_dict))
                        await session.execute(
                            text(
                                "INSERT INTO symtoken (symbol, brsymbol, name, exchange, brexchange, "
                                "token, expiry, strike, lotsize, instrumenttype, tick_size) "
                                "VALUES (:symbol, :brsymbol, :name, :exchange, :brexchange, "
                                ":token, :expiry, :strike, :lotsize, :instrumenttype, :tick_size)"
                            ),
                            data_dict,
                        )
                logger.info("Bulk insert completed with %d records", len(data_dict))
            finally:
                await engine.dispose()

        asyncio.run(_db_ops())

        # Refresh caches.
        async def _refresh_caches():
            from backend.utils import symtoken_cache
            from backend.broker.upstox.mapping.order_data import _load_symbol_cache
            await symtoken_cache.warm_from_db()
            await _load_symbol_cache()

        asyncio.run(_refresh_caches())

        logger.info("Shoonya master contract download completed successfully")
        return {
            "status": "success",
            "message": "Shoonya master contracts downloaded",
            "count": len(token_df),
        }

    except Exception as e:
        logger.error("Shoonya master contract download failed: %s", e)
        return {"status": "error", "message": str(e)}


async def search_symbols(symbol: str, exchange: str) -> list[dict]:
    """Search symtoken for symbols on the given exchange.

    Same multi-token contains-match algorithm as upstox/zerodha/angel.
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
        "  length(symbol), symbol "
        "LIMIT 50"
    )

    from backend.database import async_session
    async with async_session() as session:
        result = await session.execute(text(sql), params)
        return [
            {
                "symbol": r[0], "brsymbol": r[1], "name": r[2], "exchange": r[3],
                "brexchange": r[4], "token": r[5], "expiry": r[6], "strike": r[7],
                "lotsize": r[8], "instrumenttype": r[9], "tick_size": r[10],
            }
            for r in result.fetchall()
        ]
