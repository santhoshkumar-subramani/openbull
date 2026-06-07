"""
Shoonya margin data mapping.
Transforms OpenBull margin positions to Shoonya SPAN calculator input
and parses the response back to OpenBull format.
"""

import logging

from backend.broker.upstox.mapping.order_data import get_token_from_cache

logger = logging.getLogger(__name__)


def transform_margin_positions(positions: list[dict]) -> list[dict]:
    """Transform OpenBull margin positions to Shoonya SPAN calculator format."""
    transformed: list[dict] = []
    skipped: list[str] = []

    for pos in positions:
        try:
            symbol = pos["symbol"]
            exchange = pos["exchange"]

            token = get_token_from_cache(symbol, exchange)
            if not token:
                logger.warning("Token not found for %s on %s", symbol, exchange)
                skipped.append(f"{symbol} ({exchange})")
                continue

            action = pos["action"].upper()
            quantity = int(pos["quantity"])

            entry = {
                "exch": exchange,
                "instname": _get_instrument_name(symbol, exchange),
                "symname": _get_underlying(symbol),
                "exd": _get_expiry(symbol),
                "optt": _get_option_type(symbol),
                "strprc": str(pos.get("strike", "0")),
                "netqty": str(quantity if action == "BUY" else -quantity),
                "buyqty": str(quantity) if action == "BUY" else "0",
                "sellqty": str(quantity) if action == "SELL" else "0",
                "prd": _map_product(pos.get("product", "NRML")),
            }
            transformed.append(entry)

        except Exception as e:
            logger.error("Error transforming margin position %s: %s", pos, e)
            skipped.append(f"{pos.get('symbol', 'unknown')} - Error: {e}")

    if skipped:
        logger.warning("Skipped %d margin position(s): %s", len(skipped), ", ".join(skipped))
    if transformed:
        logger.info("Transformed %d margin position(s) for Shoonya SPAN", len(transformed))

    return transformed


def parse_margin_response(response_data: dict) -> dict:
    """Parse Shoonya SPAN calculator response to OpenBull standard format."""
    try:
        if not response_data or not isinstance(response_data, dict):
            return {"status": "error", "message": "Invalid response from broker"}

        if response_data.get("stat") != "Ok":
            return {
                "status": "error",
                "message": response_data.get("emsg", "Failed to calculate margin"),
            }

        span = float(response_data.get("span", 0) or 0)
        expo = float(response_data.get("expo", 0) or 0)
        total = span + expo

        return {
            "status": "success",
            "data": {
                "total_margin_required": total,
                "span_margin": span,
                "exposure_margin": expo,
                "margin_benefit": 0,
            },
        }

    except Exception as e:
        logger.error("Error parsing Shoonya margin response: %s", e)
        return {"status": "error", "message": f"Failed to parse margin response: {e}"}


# ---- Helpers ----

def _map_product(product: str) -> str:
    return {"CNC": "C", "NRML": "M", "MIS": "I"}.get(product, "M")


def _get_instrument_name(symbol: str, exchange: str) -> str:
    """Infer instrument name from the symbol/exchange."""
    if exchange in ("NSE", "BSE"):
        return "EQ"
    if "FUT" in symbol:
        if exchange == "NFO":
            return "FUTIDX" if _is_index_underlying(symbol) else "FUTSTK"
        if exchange == "CDS":
            return "FUTCUR"
        if exchange == "MCX":
            return "FUTCOM"
        if exchange == "BFO":
            return "FUTIDX"
    if symbol.endswith("CE") or symbol.endswith("PE"):
        if exchange == "NFO":
            return "OPTIDX" if _is_index_underlying(symbol) else "OPTSTK"
        if exchange == "CDS":
            return "OPTCUR"
        if exchange == "MCX":
            return "OPTFUT"
        if exchange == "BFO":
            return "OPTIDX"
    return "EQ"


def _is_index_underlying(symbol: str) -> bool:
    """Check if the symbol's underlying is a major index."""
    indices = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "SENSEX", "BANKEX"}
    for idx in indices:
        if symbol.startswith(idx):
            return True
    return False


def _get_underlying(symbol: str) -> str:
    """Extract underlying name from an OpenBull symbol."""
    # Strip FUT/CE/PE and date/strike parts.
    import re
    match = re.match(r"^([A-Z&]+)", symbol)
    return match.group(1) if match else symbol


def _get_expiry(symbol: str) -> str:
    """Extract expiry from symbol (e.g. NIFTY28APR26FUT → 28-APR-2026)."""
    import re
    match = re.search(r"(\d{2})([A-Z]{3})(\d{2})", symbol)
    if match:
        day, month, year = match.groups()
        return f"{day}-{month}-20{year}"
    return ""


def _get_option_type(symbol: str) -> str:
    """Extract option type from symbol."""
    if symbol.endswith("CE"):
        return "CE"
    if symbol.endswith("PE"):
        return "PE"
    return ""
