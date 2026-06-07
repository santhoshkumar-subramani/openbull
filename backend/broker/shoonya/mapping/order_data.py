"""
Shoonya order/trade/position/holdings data mapping.
Transforms broker response data to OpenBull standard format.

Numeric-type rules: numbers stay numbers (frontend calls .toFixed(2)).
Defensive casts use float(value or 0.0) / int(value or 0).
"""

import logging

from backend.broker.shoonya.mapping.transform_data import (
    reverse_map_action,
    reverse_map_order_type,
    reverse_map_product_type,
)
from backend.broker.upstox.mapping.order_data import (
    get_symbol_exchange_from_token,
    get_symbol_from_brsymbol_cache,
)

logger = logging.getLogger(__name__)


def _get_oa_symbol_from_token(token: str, exchange: str) -> str | None:
    """Resolve OpenBull symbol from Shoonya token (uses shared cache)."""
    info = get_symbol_exchange_from_token(token)
    if info:
        return info[0]
    return None


def _get_oa_symbol_from_brsymbol(brsymbol: str, exchange: str) -> str | None:
    return get_symbol_from_brsymbol_cache(brsymbol, exchange)


def _resolve_symbol(order: dict) -> str:
    """Best-effort resolve OpenBull symbol from a Shoonya order/position dict."""
    token = order.get("token", "")
    exchange = order.get("exch", "")

    # Try token-based resolution first.
    if token:
        sym = _get_oa_symbol_from_token(token, exchange)
        if sym:
            return sym

    # Fall back to brsymbol-based lookup.
    brsymbol = order.get("tsym", "")
    if brsymbol:
        sym = _get_oa_symbol_from_brsymbol(brsymbol, exchange)
        if sym:
            return sym

    return brsymbol


def map_order_data(order_data: list | dict) -> list[dict]:
    """Map Shoonya order data, converting broker symbols to OpenBull symbols
    and Shoonya product/price types to OpenBull conventions.
    """
    if isinstance(order_data, dict):
        if "data" in order_data and "status" in order_data:
            order_data = order_data.get("data", [])
        elif order_data.get("stat") == "Not_Ok":
            logger.debug("No order data: %s", order_data.get("emsg"))
            return []
        else:
            order_data = [order_data]

    if not order_data:
        return []

    for order in order_data:
        # Resolve symbol.
        order["tsym"] = _resolve_symbol(order)
        # Normalize product and price types.
        order["prd"] = reverse_map_product_type(order.get("prd", ""))
        order["prctyp"] = reverse_map_order_type(order.get("prctyp", ""))
        order["trantype"] = reverse_map_action(order.get("trantype", ""))

    return order_data


def calculate_order_statistics(order_data: list[dict]) -> dict:
    """Calculate order statistics from order data."""
    total_buy = total_sell = 0
    completed = opened = rejected = 0

    for order in order_data:
        action = order.get("trantype", "")
        if action == "BUY":
            total_buy += 1
        elif action == "SELL":
            total_sell += 1

        status = (order.get("status") or "").upper()
        if status == "COMPLETE":
            completed += 1
        elif status == "OPEN":
            opened += 1
        elif status in ("REJECTED", "REJECT"):
            rejected += 1

    return {
        "total_buy_orders": total_buy,
        "total_sell_orders": total_sell,
        "total_completed_orders": completed,
        "total_open_orders": opened,
        "total_rejected_orders": rejected,
    }


def transform_order_data(orders) -> list[dict]:
    """Transform Shoonya order data to OpenBull standard format."""
    if isinstance(orders, dict):
        orders = [orders]

    transformed = []
    for order in orders:
        if not isinstance(order, dict):
            continue

        try:
            quantity = int(order.get("qty", 0) or 0)
        except (TypeError, ValueError):
            quantity = 0

        try:
            avg_price = float(order.get("avgprc") or 0.0)
        except (TypeError, ValueError):
            avg_price = 0.0
        try:
            price_field = float(order.get("prc") or 0.0)
        except (TypeError, ValueError):
            price_field = 0.0
        price = avg_price if avg_price else price_field

        try:
            trigger_price = float(order.get("trgprc") or 0.0)
        except (TypeError, ValueError):
            trigger_price = 0.0

        transformed.append({
            "symbol": order.get("tsym", ""),
            "exchange": order.get("exch", ""),
            "action": order.get("trantype", ""),
            "quantity": quantity,
            "price": price,
            "trigger_price": trigger_price,
            "pricetype": order.get("prctyp", ""),
            "product": order.get("prd", ""),
            "orderid": order.get("norenordno", ""),
            "order_status": order.get("status") or "",
            "timestamp": order.get("norentm", ""),
        })

    return transformed


def map_trade_data(trade_data: list | dict) -> list[dict]:
    """Map Shoonya trade data — same normalization as map_order_data."""
    if isinstance(trade_data, dict):
        if "data" in trade_data and "status" in trade_data:
            trade_data = trade_data.get("data", [])
        elif trade_data.get("stat") == "Not_Ok":
            logger.debug("No trade data: %s", trade_data.get("emsg"))
            return []
        else:
            trade_data = [trade_data]

    if not trade_data:
        return []

    for trade in trade_data:
        trade["tsym"] = _resolve_symbol(trade)
        trade["prd"] = reverse_map_product_type(trade.get("prd", ""))
        trade["trantype"] = reverse_map_action(trade.get("trantype", ""))

    return trade_data


def transform_tradebook_data(tradebook_data: list[dict]) -> list[dict]:
    """Transform Shoonya tradebook to OpenBull standard format."""
    transformed = []
    for trade in tradebook_data:
        try:
            quantity = int(trade.get("flqty") or trade.get("qty", 0) or 0)
        except (TypeError, ValueError):
            quantity = 0
        try:
            avg_price = float(trade.get("flprc") or 0.0)
        except (TypeError, ValueError):
            avg_price = 0.0
        try:
            trade_value = avg_price * quantity
        except (TypeError, ValueError):
            trade_value = 0.0

        transformed.append({
            "symbol": trade.get("tsym", ""),
            "exchange": trade.get("exch", ""),
            "product": trade.get("prd", ""),
            "action": trade.get("trantype", ""),
            "quantity": quantity,
            "average_price": avg_price,
            "trade_value": trade_value,
            "orderid": trade.get("norenordno", ""),
            "timestamp": trade.get("fltm") or trade.get("norentm", ""),
        })
    return transformed


def map_position_data(position_data: list | dict) -> list[dict]:
    """Map Shoonya position data."""
    return map_order_data(position_data)


def transform_positions_data(positions_data: list[dict]) -> list[dict]:
    """Transform Shoonya positions to OpenBull standard format."""
    transformed = []
    for pos in positions_data:
        try:
            quantity = int(pos.get("netqty", 0) or 0)
        except (TypeError, ValueError):
            quantity = 0
        try:
            # TODO: Revisit this functionality later if any issues are identified.
            # Shoonya uses `upldprc` for the true carry-forward entry price, 
            # while `netavgprc` often resets to the previous day's close.
            # We prioritize `upldprc`, then `dayavgprc` (intraday), then `netavgprc`.
            upldprc = float(pos.get("upldprc") or 0.0)
            if upldprc > 0.0:
                avg_price = upldprc
            else:
                dayavgprc = float(pos.get("dayavgprc") or 0.0)
                if dayavgprc > 0.0:
                    avg_price = dayavgprc
                else:
                    avg_price = float(pos.get("netavgprc") or 0.0)
        except (TypeError, ValueError):
            avg_price = 0.0
        try:
            ltp = float(pos.get("lp") or 0.0)
        except (TypeError, ValueError):
            ltp = 0.0

        # PNL: Shoonya provides urmtom (unrealized) + rpnl (realized).
        try:
            urmtom = float(pos.get("urmtom") or 0.0)
        except (TypeError, ValueError):
            urmtom = 0.0
        try:
            rpnl = float(pos.get("rpnl") or 0.0)
        except (TypeError, ValueError):
            rpnl = 0.0
        pnl = urmtom + rpnl

        if pnl == 0.0 and quantity != 0 and avg_price > 0 and ltp > 0:
            pnl = (ltp - avg_price) * quantity + rpnl

        transformed.append({
            "symbol": pos.get("tsym", ""),
            "exchange": pos.get("exch", ""),
            "product": pos.get("prd", ""),
            "quantity": quantity,
            "average_price": avg_price,
            "ltp": round(ltp, 2),
            "pnl": round(pnl, 2),
        })
    return transformed


def transform_holdings_data(holdings_data: list) -> list[dict]:
    """Transform Shoonya holdings to OpenBull standard format."""
    if not isinstance(holdings_data, list):
        return []

    transformed = []
    for holding in holdings_data:
        # Get resolved fields from map_portfolio_data
        symbol = holding.get("_oa_symbol")
        exchange = holding.get("_exchange", "NSE")

        if not symbol:
            # Fallback if map_portfolio_data wasn't run or missed
            exch_tsym_list = holding.get("exch_tsym", [])
            if not exch_tsym_list:
                continue
            et = exch_tsym_list[0]
            exchange = et.get("exch", "")
            brsymbol = et.get("tsym", "")
            token = et.get("token", "")

            symbol = brsymbol
            if token:
                resolved = _get_oa_symbol_from_token(token, exchange)
                if resolved:
                    symbol = resolved
            if symbol == brsymbol:
                resolved = _get_oa_symbol_from_brsymbol(brsymbol, exchange)
                if resolved:
                    symbol = resolved

        try:
            quantity = int(holding.get("holdqty", 0) or 0)
        except (TypeError, ValueError):
            quantity = 0
        try:
            avg_price = float(holding.get("upldprc") or 0.0)
        except (TypeError, ValueError):
            avg_price = 0.0

        ltp = float(holding.get("_ltp") or holding.get("lp") or 0.0)

        if ltp > 0 and avg_price > 0:
            pnl = (ltp - avg_price) * quantity
            pnlpercent = ((ltp - avg_price) / avg_price) * 100
        else:
            pnl = 0.0
            pnlpercent = 0.0

        transformed.append({
            "symbol": symbol,
            "exchange": exchange,
            "quantity": quantity,
            "product": "CNC",
            "average_price": avg_price,
            "ltp": ltp,
            "pnl": float(round(pnl, 2)),
            "pnlpercent": float(round(pnlpercent, 2)),
        })
    return transformed


def map_portfolio_data(portfolio_data, auth_token=None, broker=None, config=None) -> list:
    """Validate and enrich Shoonya holdings data with listing exchange + live LTP.

    Fetches live LTPs using get_multi_quotes_with_auth if auth context is supplied.
    """
    if isinstance(portfolio_data, dict):
        if "data" in portfolio_data and "status" in portfolio_data:
            portfolio_data = portfolio_data.get("data", [])
        elif portfolio_data.get("stat") == "Not_Ok":
            logger.info("No holdings available.")
            return []
        else:
            portfolio_data = [portfolio_data]

    if not isinstance(portfolio_data, list):
        return []

    # Resolve symbol + exchange
    for h in portfolio_data:
        exch_tsym_list = h.get("exch_tsym", [])
        resolved_symbol = ""
        resolved_exchange = "NSE"
        if exch_tsym_list:
            et = exch_tsym_list[0]
            exchange = et.get("exch", "")
            brsymbol = et.get("tsym", "")
            token = et.get("token", "")

            resolved_symbol = brsymbol
            resolved_exchange = exchange
            if token:
                resolved = _get_oa_symbol_from_token(token, exchange)
                if resolved:
                    resolved_symbol = resolved
            if resolved_symbol == brsymbol:
                resolved = _get_oa_symbol_from_brsymbol(brsymbol, exchange)
                if resolved:
                    resolved_symbol = resolved

        h["_oa_symbol"] = resolved_symbol
        h["_exchange"] = resolved_exchange
        h["_ltp"] = 0.0

    # Batch fetch live LTPs if auth is available
    if auth_token and broker:
        try:
            from backend.services.quotes_service import get_multi_quotes_with_auth

            payload = [
                {"symbol": h["_oa_symbol"], "exchange": h["_exchange"]}
                for h in portfolio_data
                if h.get("_oa_symbol") and h.get("_exchange")
            ]
            if payload:
                ok, resp, _ = get_multi_quotes_with_auth(
                    symbols_list=payload, auth_token=auth_token, broker=broker, config=config
                )
                if ok and isinstance(resp, dict):
                    ltp_map: dict[str, float] = {}
                    for row in resp.get("results", []):
                        if not isinstance(row, dict):
                            continue
                        data = row.get("data", row)
                        ltp_map[f"{row.get('exchange')}:{row.get('symbol')}"] = float(
                            data.get("ltp", 0) or 0
                        )
                    for h in portfolio_data:
                        key = f"{h['_exchange']}:{h['_oa_symbol']}"
                        if key in ltp_map:
                            h["_ltp"] = ltp_map[key]
        except Exception as e:
            logger.warning("Failed to fetch Shoonya holdings LTP: %s", e)

    return portfolio_data


def calculate_portfolio_statistics(holdings_data: list) -> dict:
    """Calculate portfolio statistics from Shoonya holdings data."""
    if not isinstance(holdings_data, list):
        holdings_data = []

    totalinvvalue = sum(
        float(item.get("upldprc") or 0) * int(item.get("holdqty") or 0)
        for item in holdings_data
    )
    totalholdingvalue = sum(
        (float(item.get("_ltp") or 0) or float(item.get("upldprc") or 0))
        * int(item.get("holdqty") or 0)
        for item in holdings_data
    )
    totalprofitandloss = totalholdingvalue - totalinvvalue
    totalpnlpercentage = (totalprofitandloss / totalinvvalue * 100) if totalinvvalue else 0.0

    return {
        "totalholdingvalue": totalholdingvalue,
        "totalinvvalue": totalinvvalue,
        "totalprofitandloss": totalprofitandloss,
        "totalpnlpercentage": totalpnlpercentage,
    }
