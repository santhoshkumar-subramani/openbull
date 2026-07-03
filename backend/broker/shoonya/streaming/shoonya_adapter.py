"""
Shoonya (Finvasia / Noren) streaming adapter.

Connects to Shoonya's WebSocket (NorenWSTP), parses JSON tick messages, and
publishes normalized market data on a ZMQ PUB socket. Modeled after the
Angel adapter (sync websocket-client in a daemon thread, threading.Lock for
state, exponential reconnect, health-check loop).

Wire protocol (Shoonya / Noren WebSocket):
  Auth:     {"t": "c", "uid": <uid>, "actid": <actid>, "susertoken": <token>, "source": "API"}
  Sub TL:   {"t": "t", "k": "NSE|22#BSE|522032"}
  Sub Depth: {"t": "d", "k": "NSE|22"}
  Unsub:    {"t": "u", "k": "NSE|22"}

  Inbound tick types:
    t=tk  touchline initial snapshot (all fields)
    t=tf  touchline update (only changed fields)
    t=dk  depth initial snapshot (all fields incl. bp1-5/sp1-5)
    t=df  depth update (only changed fields)
    t=ok  order update subscription ack
    t=om  order update
"""

import json
import logging
import os
import re
import ssl
import threading
import time

import websocket

OPTION_PATTERN = re.compile(r'[CP]\d+$')

from backend.broker.upstox.mapping.order_data import (
    get_symbol_exchange_from_token,
    get_token_from_cache,
)
from backend.websocket_proxy.base_adapter import (
    BaseBrokerAdapter,
    MODE_DEPTH,
    MODE_LTP,
    MODE_QUOTE,
)

logger = logging.getLogger("shoonya_stream")

SHOONYA_WS_URL = "wss://api.shoonya.com/NorenWSAPI/"

# Reconnect / health-check tuning.
RECONNECT_MAX_TRIES = 50
RECONNECT_MAX_DELAY = 60
PING_INTERVAL = int(os.getenv("WS_PING_INTERVAL", "30"))
HEARTBEAT_INTERVAL = int(os.getenv("WS_HEALTH_CHECK_INTERVAL", "30"))
HEARTBEAT_TIMEOUT = int(os.getenv("WS_HEARTBEAT_TIMEOUT", "120"))


def _split_token(auth_token: str) -> tuple[str, str, str]:
    """Split combined ``userid:susertoken:actid``."""
    parts = auth_token.split(":") if auth_token else []
    if len(parts) >= 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], parts[1], parts[0]
    return "", auth_token or "", ""


class ShoonyaAdapter(BaseBrokerAdapter):
    """Shoonya (Finvasia) streaming adapter."""

    def __init__(self, auth_token: str, broker_config: dict):
        super().__init__(auth_token, broker_config)

        uid, susertoken, actid = _split_token(auth_token)
        self._uid = broker_config.get("userid") or uid
        self._susertoken = susertoken
        self._actid = broker_config.get("actid") or actid

        self._ws: websocket.WebSocketApp | None = None
        self._ws_thread: threading.Thread | None = None
        self._health_thread: threading.Thread | None = None

        self._connected = False
        self._authenticated = False
        self._last_msg_time: float | None = None

        # Subscription bookkeeping.
        # _subscriptions: (symbol, exchange) -> {"token", "mode"}
        self._subscriptions: dict[tuple[str, str], dict] = {}
        # token (str) -> (symbol, exchange) — used in tick parsing.
        self._token_to_se: dict[str, tuple[str, str]] = {}

        # Accumulator for incremental ticks: token -> {field: value, ...}
        self._tick_state: dict[str, dict] = {}

        self._sub_lock = threading.Lock()
        self._reconnect_reset_signal = False
        self._fatal_error: bool = False
        self._fatal_error_message: str = ""

    # ---- BaseBrokerAdapter interface ----

    def connect(self) -> None:
        if not self._uid or not self._susertoken:
            raise ConnectionError(
                "Shoonya WS connect requires uid and susertoken; "
                "ensure the auth flow stored the combined token."
            )

        self._fatal_error = False
        self._fatal_error_message = ""
        self._running = True
        self._ws = websocket.WebSocketApp(
            SHOONYA_WS_URL,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

        self._ws_thread = threading.Thread(
            target=self._run_ws, daemon=True, name="shoonya-ws"
        )
        self._ws_thread.start()

        # Block briefly for the handshake.
        for _ in range(100):
            if self._authenticated:
                return
            time.sleep(0.1)
        if not self._authenticated:
            raise ConnectionError("Shoonya WebSocket connection timed out")

    def subscribe(self, symbols: list[dict], mode: int) -> None:
        new_tokens: dict[str, str] = {}  # exchange -> "EXCH|TOKEN#EXCH|TOKEN"
        with self._sub_lock:
            for item in symbols:
                sym = item.get("symbol")
                exch = item.get("exchange")
                if not sym or not exch:
                    continue

                token_str = get_token_from_cache(sym, exch)
                if not token_str:
                    logger.warning("Shoonya subscribe: token not found for %s/%s", sym, exch)
                    continue

                # Shoonya maps INDEX exchanges back to base.
                api_exch = _api_exchange(exch)

                key = (sym, exch)
                self._subscriptions[key] = {"token": token_str, "mode": mode, "api_exch": api_exch}
                self._token_to_se[token_str] = (sym, exch)
                sub_key = f"{api_exch}|{token_str}"
                new_tokens[sub_key] = sub_key

        if not new_tokens or not self._authenticated:
            return

        # Build subscribe message.
        feed_type = "d" if mode == MODE_DEPTH else "t"
        tokens_str = "#".join(new_tokens.keys())
        self._send_subscribe(feed_type, tokens_str)

    def unsubscribe(self, symbols: list[dict], mode: int) -> None:
        unsub_keys: list[str] = []
        with self._sub_lock:
            for item in symbols:
                sym = item.get("symbol")
                exch = item.get("exchange")
                if not sym or not exch:
                    continue
                key = (sym, exch)
                sub = self._subscriptions.pop(key, None)
                if not sub:
                    continue
                self._token_to_se.pop(sub["token"], None)
                self._tick_state.pop(sub["token"], None)
                unsub_keys.append(f"{sub['api_exch']}|{sub['token']}")

        if not unsub_keys or not self._authenticated:
            return

        tokens_str = "#".join(unsub_keys)
        msg = json.dumps({"t": "u", "k": tokens_str})
        try:
            self._ws.send(msg)
        except Exception as e:
            logger.error("Shoonya unsubscribe error: %s", e)

    def disconnect(self) -> None:
        self._running = False
        self._connected = False
        self._authenticated = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        with self._sub_lock:
            self._subscriptions.clear()
            self._token_to_se.clear()
            self._tick_state.clear()
        logger.info("Shoonya adapter disconnected")

    # ---- WS lifecycle ----

    def _run_ws(self) -> None:
        reconnect_attempts = 0
        while self._running:
            self._reconnect_reset_signal = False
            try:
                self._ws.run_forever(
                    sslopt={"cert_reqs": ssl.CERT_NONE},
                    ping_interval=PING_INTERVAL,
                    ping_timeout=10,
                    ping_payload="ping",
                )
            except Exception as e:
                logger.error("Shoonya WS run_forever error: %s", e)

            self._connected = False
            self._authenticated = False
            if not self._running:
                break

            if self._fatal_error:
                logger.error(
                    "Shoonya WS stopping — fatal error (likely auth/token failure): %s",
                    self._fatal_error_message,
                )
                break

            if self._reconnect_reset_signal:
                reconnect_attempts = 0

            reconnect_attempts += 1
            if reconnect_attempts > RECONNECT_MAX_TRIES:
                logger.error("Shoonya max reconnect attempts (%d) reached", RECONNECT_MAX_TRIES)
                break

            delay = min(2 * (1.5 ** reconnect_attempts), RECONNECT_MAX_DELAY)
            logger.info("Shoonya reconnecting in %.1fs (attempt %d)", delay, reconnect_attempts)
            time.sleep(delay)

    def _on_open(self, ws) -> None:
        logger.info("Shoonya WebSocket connected, sending auth")
        self._connected = True
        self._last_msg_time = time.time()

        # Authenticate.
        auth_msg = json.dumps({
            "t": "a",
            "uid": self._uid,
            "actid": self._actid,
            "accesstoken": self._susertoken,
            "source": "API",
        })
        try:
            ws.send(auth_msg)
        except Exception as e:
            logger.error("Shoonya auth send error: %s", e)

    def _on_message(self, ws, message) -> None:
        self._last_msg_time = time.time()

        if isinstance(message, bytes):
            message = message.decode("utf-8", errors="ignore")

        if not isinstance(message, str) or not message.strip():
            return

        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            if message != "pong":
                logger.debug("Shoonya WS non-JSON: %s", message[:200])
            return

        msg_type = data.get("t", "")

        # Connection acknowledgement.
        if msg_type == "ak":
            status = data.get("s", "")
            if status == "OK":
                logger.info("Shoonya WS authenticated")
                self._authenticated = True
                self._reconnect_reset_signal = True
                self._start_health_check()
                self._resubscribe_all()
            else:
                logger.error("Shoonya WS auth failed: %s", data)
                self._mark_fatal_error(data.get("emsg", "Authentication failed"))
            return

        # Touchline / Depth updates.
        if msg_type in ("tk", "tf", "dk", "df"):
            if "lp" in data:
                token = data.get("tk", "")
                if token:
                    info = self._token_to_se.get(token)
                    if not info:
                        info = get_symbol_exchange_from_token(token)
                    if info:
                        symbol, exchange = info
                        # Removed generic > 10000 log as it spams for indices and high-priced stocks.
                        if exchange in ("NFO", "BFO", "MCX"):
                            try:
                                new_lp = float(data["lp"] or 0)
                                if new_lp > 10000:
                                    is_option = symbol.endswith('CE') or symbol.endswith('PE') or bool(OPTION_PATTERN.search(symbol)) or "OPT" in symbol
                                    if is_option:
                                        state = self._tick_state.get(token, {})
                                        prev_lp = float(state.get("lp", 0) or 0)
                                        if prev_lp == 0 or prev_lp < 5000:
                                            logger.warning("Shoonya Websocket [%s] bug detected: Absurd lp %s for option %s. Raw tick data: %s", msg_type, new_lp, symbol, json.dumps(data))
                                            del data["lp"]
                            except Exception as e:
                                import traceback
                                logger.error("Shoonya WS filter crashed! Error: %s", traceback.format_exc())

            if msg_type in ("tk", "tf"):
                self._process_touchline(data)
            else:
                self._process_depth(data)
            return

        # Order update — log and trigger position sync.
        if msg_type in ("ok", "om"):
            logger.debug("Shoonya order update: %s", data)
            
            # If the order is COMPLETE (filled), or it's just an update, 
            # trigger a background fetch of positions so the diffing engine fires alerts!
            if hasattr(self, "_openbull_user_id") and self._openbull_user_id:
                try:
                    import threading
                    def _bg_fetch_positions():
                        try:
                            logger.info(f"Triggering background position fetch for user {self._openbull_user_id} due to order update")
                            from backend.services.positions_service import get_positions_with_auth
                            # This will fetch the positions synchronously and fire the async diff engine
                            get_positions_with_auth(self.auth_token, "shoonya", user_id=self._openbull_user_id)
                        except Exception as fetch_err:
                            logger.error("Background position fetch failed on order update: %s", fetch_err)
                            
                    threading.Thread(target=_bg_fetch_positions, daemon=True).start()
                except Exception as e:
                    logger.error("Failed to start background position thread: %s", e)
            else:
                logger.debug("Skipping background position fetch (no openbull_user_id attached to adapter)")
            return

    def _on_error(self, ws, error) -> None:
        logger.error("Shoonya WS error: %s", error)
        self._connected = False
        if self._is_fatal_auth_error(error):
            self._mark_fatal_error(str(error))

    def _on_close(self, ws, code, msg) -> None:
        logger.info("Shoonya WS closed (code=%s, msg=%s)", code, msg)
        self._connected = False
        self._authenticated = False
        if not self._fatal_error and self._is_fatal_auth_error(msg):
            self._mark_fatal_error(f"close_msg={msg!r}")

    # ---- Tick processing ----

    def _process_touchline(self, data: dict) -> None:
        """Process touchline (tk/tf) messages."""
        token = data.get("tk", "")
        if not token:
            return

        # Merge into accumulator.
        state = self._tick_state.setdefault(token, {})
        state.update(data)

        info = self._token_to_se.get(token)
        if not info:
            info = get_symbol_exchange_from_token(token)
        if not info:
            return
        symbol, exchange = info

        ltp = float(state.get("lp", 0) or 0)
        close = float(state.get("c", 0) or 0)
        change = round(ltp - close, 4) if (ltp and close) else 0.0
        change_pct = round((ltp - close) / close * 100, 4) if close else 0.0

        # Publish LTP.
        ltp_data = {
            "symbol": symbol, "exchange": exchange, "mode": "ltp",
            "ltp": ltp,
            "ltt": int(time.time()),
            "cp": close, "change": change, "change_percent": change_pct,
        }
        self.publish(f"{exchange}_{symbol}_LTP", ltp_data)

        # Publish QUOTE.
        quote_data = {
            "symbol": symbol, "exchange": exchange, "mode": "quote",
            "ltp": ltp,
            "ltq": int(state.get("ltq", 0) or 0),
            "average_price": float(state.get("ap", 0) or 0),
            "volume": int(state.get("v", 0) or 0),
            "total_buy_quantity": 0,
            "total_sell_quantity": 0,
            "open": float(state.get("o", 0) or 0),
            "high": float(state.get("h", 0) or 0),
            "low": float(state.get("l", 0) or 0),
            "close": close,
            "cp": close,
            "change": change,
            "change_percent": change_pct,
            "oi": int(state.get("oi", 0) or 0),
            "lower_circuit": float(state.get("lc", 0) or 0),
            "upper_circuit": float(state.get("uc", 0) or 0),
            "ltt": int(time.time()),
        }
        self.publish(f"{exchange}_{symbol}_QUOTE", quote_data)

    def _process_depth(self, data: dict) -> None:
        """Process depth (dk/df) messages."""
        token = data.get("tk", "")
        if not token:
            return

        state = self._tick_state.setdefault(token, {})
        state.update(data)

        info = self._token_to_se.get(token)
        if not info:
            info = get_symbol_exchange_from_token(token)
        if not info:
            return
        symbol, exchange = info

        ltp = float(state.get("lp", 0) or 0)
        close = float(state.get("c", 0) or 0)
        change = round(ltp - close, 4) if (ltp and close) else 0.0
        change_pct = round((ltp - close) / close * 100, 4) if close else 0.0

        # Also publish LTP and QUOTE for depth subscribers.
        self._process_touchline(data)

        # Build 5-level depth.
        bids: list[dict] = []
        asks: list[dict] = []
        for i in range(1, 6):
            bids.append({
                "price": float(state.get(f"bp{i}", 0) or 0),
                "quantity": int(state.get(f"bq{i}", 0) or 0),
                "orders": int(state.get(f"bo{i}", 0) or 0),
            })
            asks.append({
                "price": float(state.get(f"sp{i}", 0) or 0),
                "quantity": int(state.get(f"sq{i}", 0) or 0),
                "orders": int(state.get(f"so{i}", 0) or 0),
            })

        depth_data = {
            "symbol": symbol, "exchange": exchange, "mode": "full",
            "ltp": ltp,
            "ltq": int(state.get("ltq", 0) or 0),
            "average_price": float(state.get("ap", 0) or 0),
            "volume": int(state.get("v", 0) or 0),
            "total_buy_quantity": int(state.get("tbq", 0) or 0),
            "total_sell_quantity": int(state.get("tsq", 0) or 0),
            "open": float(state.get("o", 0) or 0),
            "high": float(state.get("h", 0) or 0),
            "low": float(state.get("l", 0) or 0),
            "close": close,
            "cp": close,
            "change": change,
            "change_percent": change_pct,
            "oi": int(state.get("oi", 0) or 0),
            "lower_circuit": float(state.get("lc", 0) or 0),
            "upper_circuit": float(state.get("uc", 0) or 0),
            "ltt": int(time.time()),
            "depth": {"buy": bids, "sell": asks},
        }
        self.publish(f"{exchange}_{symbol}_DEPTH", depth_data)

    # ---- Subscribe helpers ----

    def _send_subscribe(self, feed_type: str, tokens_str: str) -> None:
        """Send a subscribe message."""
        msg = json.dumps({"t": feed_type, "k": tokens_str})
        try:
            self._ws.send(msg)
        except Exception as e:
            logger.error("Shoonya subscribe send error: %s", e)

    def _resubscribe_all(self) -> None:
        """Re-subscribe all symbols after reconnect."""
        with self._sub_lock:
            # Group by mode.
            touchline_keys: list[str] = []
            depth_keys: list[str] = []

            for sub in self._subscriptions.values():
                k = f"{sub['api_exch']}|{sub['token']}"
                if sub["mode"] == MODE_DEPTH:
                    depth_keys.append(k)
                else:
                    touchline_keys.append(k)

        if touchline_keys:
            self._send_subscribe("t", "#".join(touchline_keys))
            time.sleep(0.2)
        if depth_keys:
            self._send_subscribe("d", "#".join(depth_keys))

        total = len(touchline_keys) + len(depth_keys)
        if total:
            logger.info("Shoonya re-subscribed %d tokens after connect", total)

    # ---- Health check ----

    def _start_health_check(self) -> None:
        if self._health_thread and self._health_thread.is_alive():
            return
        self._health_thread = threading.Thread(
            target=self._heartbeat_worker, daemon=True, name="shoonya-heartbeat"
        )
        self._health_thread.start()

    def _heartbeat_worker(self) -> None:
        while self._running and self._connected:
            time.sleep(HEARTBEAT_INTERVAL)
            if not self._running or not self._connected:
                break

            # Send app-level heartbeat
            try:
                if self._ws and self._authenticated:
                    self._ws.send(json.dumps({"t": "h"}))
            except Exception as e:
                logger.error("Shoonya heartbeat send error: %s", e)

            # Check connection health
            self._check_connection_health()

    def _check_connection_health(self) -> None:
        if self._last_msg_time and (time.time() - self._last_msg_time) > HEARTBEAT_TIMEOUT:
            logger.info("Shoonya data stall (>%ds). Forcing reconnect.", HEARTBEAT_TIMEOUT)
            if self._ws:
                try:
                    self._ws.close()
                except Exception:
                    pass

    # ---- Auth-failure detection ----

    _AUTH_FAILURE_INDICATORS = (
        "403",
        "401",
        "unauthorized",
        "tokenexception",
        "invalid api_key",
        "invalid access_token",
        "invalid token",
        "auth failed",
        "not_ok",
        "session expired",
    )

    def _is_fatal_auth_error(self, payload) -> bool:
        """True iff the error/close payload looks like an auth/token failure."""
        if not payload:
            return False
        text = str(payload).lower()
        return any(tok in text for tok in self._AUTH_FAILURE_INDICATORS)

    def _mark_fatal_error(self, message: str) -> None:
        """Flag a non-retryable auth failure (idempotent)."""
        if self._fatal_error:
            return
        self._fatal_error = True
        self._fatal_error_message = message
        logger.error(
            "Shoonya auth/token failure detected — will not retry. "
            "Refresh the token and reconnect. (%s)",
            message,
        )



def _api_exchange(exchange: str) -> str:
    """Map INDEX-segmented exchanges to base codes."""
    if exchange == "NSE_INDEX":
        return "NSE"
    if exchange == "BSE_INDEX":
        return "BSE"
    if exchange == "MCX_INDEX":
        return "MCX"
    return exchange
