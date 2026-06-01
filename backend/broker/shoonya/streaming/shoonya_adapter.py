"""
Shoonya WebSocket streaming adapter.

Connects to Shoonya's NorenWSAPI using a text-based JSON protocol
(NOT binary like Zerodha / Dhan / Angel).  Inherits BaseBrokerAdapter
and publishes normalized ticks on a ZMQ PUB socket.

Shoonya WS protocol summary
----------------------------
URL     : wss://api.shoonya.com/NorenWSAPI/ws
Auth    : {"t":"c","uid":<uid>,"actid":<uid>,"susertoken":<token>,"source":"API"}
Ack     : {"t":"ck","s":"OK"}  (or "NOT_OK" on failure)
Sub LTP : {"t":"t","k":"NSE|26000#NFO|56521#..."}   (batch ≤100)
Sub Dep : {"t":"d","k":"NSE|26000#..."}
Unsub   : {"t":"u","k":"..."}  / {"t":"ud","k":"..."}
Heartbeat: {"t":"h"}  sent every 30 s; stall >90 s → reconnect
Tick    : "t"/"tf" for touchline  ("lp","o","h","l","c","v","oi","bp1-5","sp1-5","bq1-5","sq1-5")
        : "d"/"df" for depth
"""

import json
import logging
import ssl
import threading
import time

import websocket

from backend.broker.upstox.mapping.order_data import (
    get_symbol_exchange_from_token,
    get_token_from_cache,
)
from backend.broker.shoonya.streaming.shoonya_mapping import ShoonyaExchangeMapper
from backend.websocket_proxy.base_adapter import (
    BaseBrokerAdapter,
    MODE_DEPTH,
    MODE_LTP,
    MODE_QUOTE,
)

logger = logging.getLogger("shoonya_stream")

_WS_URL = "wss://api.shoonya.com/NorenWSAPI/ws"

_RECONNECT_MAX_TRIES = 10
_RECONNECT_BASE_DELAY = 5      # seconds
_RECONNECT_MAX_DELAY = 60
_HEARTBEAT_INTERVAL = 30       # seconds between {"t":"h"} frames
_DATA_STALL_TIMEOUT = 90       # seconds without any data → reconnect
_HEALTH_CHECK_INTERVAL = 30    # health-check thread poll interval

_BATCH_SIZE = 100              # Shoonya max tokens per subscription frame


class ShoonyaAdapter(BaseBrokerAdapter):
    """Shoonya NorenWSAPI streaming adapter."""

    def __init__(self, auth_token: str, broker_config: dict):
        super().__init__(auth_token, broker_config)

        self._susertoken = auth_token
        self._uid = broker_config.get("client_id", "")

        self._ws: websocket.WebSocketApp | None = None
        self._ws_thread: threading.Thread | None = None
        self._health_thread: threading.Thread | None = None

        self._connected = False
        self._auth_ok = False
        self._auth_failed = False          # Set on NOT_OK → stop reconnecting
        self._last_msg_time: float | None = None

        # Subscription bookkeeping
        # keyed by (symbol, exchange) → {"token": str, "mode": int}
        self._subscriptions: dict[tuple[str, str], dict] = {}
        # token → (symbol, exchange)
        self._token_to_se: dict[str, tuple[str, str]] = {}

        self._sub_lock = threading.Lock()
        self._reconnect_attempt = 0

    # ---- Connect / disconnect ----

    def connect(self) -> None:
        self._running = True
        self._connect_ws()

        for _ in range(100):
            if self._auth_ok:
                return
            time.sleep(0.1)
        if not self._auth_ok:
            raise ConnectionError("Shoonya WS authentication timed out")

    def disconnect(self) -> None:
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        self.cleanup_zmq()

    # ---- Subscribe / Unsubscribe ----

    def subscribe(self, symbols: list[dict], mode: int) -> None:
        new_keys: list[str] = []

        with self._sub_lock:
            for item in symbols:
                sym = item.get("symbol")
                exch = item.get("exchange")
                if not sym or not exch:
                    continue
                token = get_token_from_cache(sym, exch)
                if not token:
                    logger.warning("Shoonya subscribe: token not found for %s/%s", sym, exch)
                    continue
                key = (sym, exch)
                self._subscriptions[key] = {"token": token, "mode": mode}
                self._token_to_se[token] = key
                ws_key = ShoonyaExchangeMapper.make_key(exch, token)
                new_keys.append(ws_key)

        if new_keys and self._auth_ok:
            self._send_subscribe(new_keys, mode)

    def unsubscribe(self, symbols: list[dict], mode: int) -> None:
        remove_keys: list[str] = []

        with self._sub_lock:
            for item in symbols:
                sym = item.get("symbol")
                exch = item.get("exchange")
                key = (sym, exch)
                sub = self._subscriptions.pop(key, None)
                if sub:
                    ws_key = ShoonyaExchangeMapper.make_key(exch, sub["token"])
                    remove_keys.append(ws_key)
                    self._token_to_se.pop(sub["token"], None)

        if remove_keys and self._auth_ok:
            self._send_unsubscribe(remove_keys, mode)

    # ---- Internal helpers ----

    def _send(self, msg: dict) -> None:
        if self._ws and self._connected:
            try:
                self._ws.send(json.dumps(msg))
            except Exception as e:
                logger.error("Shoonya WS send error: %s", e)

    def _send_subscribe(self, ws_keys: list[str], mode: int) -> None:
        t = "d" if mode == MODE_DEPTH else "t"
        for i in range(0, len(ws_keys), _BATCH_SIZE):
            batch = ws_keys[i: i + _BATCH_SIZE]
            self._send({"t": t, "k": "#".join(batch)})

    def _send_unsubscribe(self, ws_keys: list[str], mode: int) -> None:
        t = "ud" if mode == MODE_DEPTH else "u"
        for i in range(0, len(ws_keys), _BATCH_SIZE):
            batch = ws_keys[i: i + _BATCH_SIZE]
            self._send({"t": t, "k": "#".join(batch)})

    def _resubscribe_all(self) -> None:
        """Re-send all current subscriptions after a reconnect."""
        with self._sub_lock:
            touchline: list[str] = []
            depth: list[str] = []
            for (sym, exch), info in self._subscriptions.items():
                ws_key = ShoonyaExchangeMapper.make_key(exch, info["token"])
                if info["mode"] == MODE_DEPTH:
                    depth.append(ws_key)
                else:
                    touchline.append(ws_key)

        if touchline:
            self._send_subscribe(touchline, MODE_QUOTE)
        if depth:
            self._send_subscribe(depth, MODE_DEPTH)

    # ---- WebSocketApp callbacks ----

    def _on_open(self, ws) -> None:
        self._connected = True
        self._last_msg_time = time.monotonic()
        logger.info("Shoonya WS connected, sending auth")
        self._send({
            "t": "c",
            "uid": self._uid,
            "actid": self._uid,
            "susertoken": self._susertoken,
            "source": "API",
        })

    def _on_message(self, ws, message: str) -> None:
        self._last_msg_time = time.monotonic()
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return

        t = data.get("t", "")

        if t == "ck":
            if data.get("s") == "OK":
                self._auth_ok = True
                self._reconnect_attempt = 0
                logger.info("Shoonya WS authenticated OK")
                if self._zmq_socket is None:
                    try:
                        self.setup_zmq()
                    except Exception as e:
                        logger.error("Shoonya ZMQ setup failed: %s", e)
                self._resubscribe_all()
                self._start_health_check()
            else:
                self._auth_failed = True
                self._auth_ok = False
                logger.error("Shoonya WS auth failed: %s", data)
            return

        if t in ("t", "tf"):
            self._handle_touchline(data)
        elif t in ("d", "df"):
            self._handle_depth(data)
        elif t == "h":
            pass  # heartbeat ack — nothing to do

    def _on_error(self, ws, error) -> None:
        logger.error("Shoonya WS error: %s", error)

    def _on_close(self, ws, close_status_code, close_msg) -> None:
        self._connected = False
        self._auth_ok = False
        logger.info("Shoonya WS closed (%s %s)", close_status_code, close_msg)
        if self._running and not self._auth_failed:
            self._schedule_reconnect()

    # ---- Tick handlers ----

    def _handle_touchline(self, data: dict) -> None:
        token = data.get("tk", "")
        se = self._token_to_se.get(token)
        if not se:
            se = get_symbol_exchange_from_token(token)
        if not se:
            return

        symbol, exchange = se
        ltp = float(data.get("lp", "0") or "0")
        prev_close = float(data.get("c", "0") or "0")
        change = round(ltp - prev_close, 2) if prev_close else 0.0
        change_pct = round((change / prev_close) * 100, 2) if prev_close else 0.0

        tick = {
            "symbol": symbol,
            "exchange": exchange,
            "ltp": ltp,
            "open": float(data.get("o", "0") or "0"),
            "high": float(data.get("h", "0") or "0"),
            "low": float(data.get("l", "0") or "0"),
            "close": float(data.get("c", "0") or "0"),
            "volume": int(data.get("v", "0") or "0"),
            "oi": int(data.get("oi", "0") or "0"),
            "change": change,
            "change_percent": change_pct,
            "timestamp": int(time.time()),
        }

        mode_name = "LTP" if data.get("t") == "t" else "QUOTE"
        topic = f"{exchange}_{symbol}_{mode_name}"
        self.publish(topic, tick)

    def _handle_depth(self, data: dict) -> None:
        token = data.get("tk", "")
        se = self._token_to_se.get(token)
        if not se:
            se = get_symbol_exchange_from_token(token)
        if not se:
            return

        symbol, exchange = se

        bids = []
        asks = []
        for i in range(1, 6):
            bp = float(data.get(f"bp{i}", "0") or "0")
            bq = int(data.get(f"bq{i}", "0") or "0")
            sp = float(data.get(f"sp{i}", "0") or "0")
            sq = int(data.get(f"sq{i}", "0") or "0")
            bids.append({"price": bp, "quantity": bq, "orders": 0})
            asks.append({"price": sp, "quantity": sq, "orders": 0})

        tick = {
            "symbol": symbol,
            "exchange": exchange,
            "bids": bids,
            "asks": asks,
            "timestamp": int(time.time()),
        }
        topic = f"{exchange}_{symbol}_DEPTH"
        self.publish(topic, tick)

    # ---- Heartbeat / health check ----

    def _heartbeat_loop(self) -> None:
        while self._running and self._auth_ok:
            time.sleep(_HEARTBEAT_INTERVAL)
            if not self._auth_ok or not self._running:
                break
            # Check for data stall
            if self._last_msg_time and (
                time.monotonic() - self._last_msg_time > _DATA_STALL_TIMEOUT
            ):
                logger.warning("Shoonya WS data stall detected, reconnecting")
                if self._ws:
                    self._ws.close()
                break
            self._send({"t": "h"})

    def _start_health_check(self) -> None:
        if self._health_thread and self._health_thread.is_alive():
            return
        self._health_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True, name="shoonya-hb"
        )
        self._health_thread.start()

    # ---- Reconnect ----

    def _schedule_reconnect(self) -> None:
        if self._auth_failed:
            logger.error("Shoonya WS: auth failed, will not reconnect")
            return
        if self._reconnect_attempt >= _RECONNECT_MAX_TRIES:
            logger.error("Shoonya WS: max reconnect attempts reached")
            return

        delay = min(
            _RECONNECT_BASE_DELAY * (2 ** self._reconnect_attempt),
            _RECONNECT_MAX_DELAY,
        )
        self._reconnect_attempt += 1
        logger.info("Shoonya WS reconnecting in %ds (attempt %d)", delay, self._reconnect_attempt)
        time.sleep(delay)
        if self._running:
            self._connect_ws()

    def _connect_ws(self) -> None:
        self._ws = websocket.WebSocketApp(
            _WS_URL,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._ws_thread = threading.Thread(
            target=self._run_ws, daemon=True, name="shoonya-ws"
        )
        self._ws_thread.start()

    def _run_ws(self) -> None:
        try:
            self._ws.run_forever(
                sslopt={"cert_reqs": ssl.CERT_NONE},
                ping_interval=0,   # we send manual heartbeat frames
            )
        except Exception as e:
            logger.error("Shoonya WS run error: %s", e)
