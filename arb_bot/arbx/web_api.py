"""Persistent control-plane API for the Vercel UI. Run only behind HTTPS on a persistent host."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import os
import queue
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from arbx.config import CCXT_ADAPTERS, MAX_EXCHANGES
from arbx.market import orderbook_limit
from arbx.permissions import (
    LIVE_PERMISSION_VERIFICATION_VENUES,
    PERMISSION_PROBE_VENUES,
    inspect_permissions,
)


APP_DB = Path(os.getenv("ARBX_APP_DB", "arbx_app.sqlite3"))
SESSION_COOKIE = "arbx_session"
SESSION_TTL = 60 * 60 * 24 * 7
VERIFICATION_TTL_SECONDS = 5 * 60
ACCOUNT_REFRESH_SECONDS = max(30, int(os.getenv("ARBX_ACCOUNT_REFRESH_SECONDS", "60")))
VENUES = {
    "binance": "Binance", "bybit": "Bybit", "okx": "OKX", "kucoin": "KuCoin", "gateio": "Gate.io",
    "mexc": "MEXC", "htx": "HTX", "lbank": "LBank", "bitget": "Bitget", "kraken": "Kraken",
    "coinbaseexchange": "Coinbase Exchange", "bitfinex": "Bitfinex", "bitstamp": "Bitstamp", "gemini": "Gemini",
    "cryptocom": "Crypto.com Exchange", "coinex": "CoinEx", "bingx": "BingX", "whitebit": "WhiteBIT",
}
AUTH_SCHEMAS = {venue_id: {"modes": [{"id": "ccxt", "label": "API key and secret",
                                     "fields": [{"name": "apiKey", "label": "API key"},
                                                {"name": "secret", "label": "API secret"}]}]}
                for venue_id in VENUES}
AUTH_SCHEMAS["kucoin"] = {"modes": [{"id": "ccxt", "label": "API key, secret, and passphrase",
    "fields": [{"name": "apiKey", "label": "API key"}, {"name": "secret", "label": "API secret"},
               {"name": "password", "label": "API passphrase"}]}]}
for _venue_id in ("okx", "bitget"):
    AUTH_SCHEMAS[_venue_id] = {"modes": [{"id": "ccxt", "label": "API key, secret, and passphrase",
        "fields": [{"name": "apiKey", "label": "API key"}, {"name": "secret", "label": "API secret"},
                   {"name": "password", "label": "API passphrase"}]}]}
AUTH_SCHEMAS["coinbaseexchange"] = {"modes": [{"id": "ccxt", "label": "API key, secret, and passphrase",
    "fields": [{"name": "apiKey", "label": "API key"}, {"name": "secret", "label": "API secret"},
               {"name": "password", "label": "API passphrase"}]}]}
AUTH_SCHEMAS["binance"] = {"modes": [
    {"id": "hmac", "label": "HMAC", "fields": [{"name": "apiKey", "label": "API key"}, {"name": "secret", "label": "API secret"}]},
    {"id": "rsa", "label": "RSA key pair", "fields": [{"name": "apiKey", "label": "API key"}, {"name": "privateKey", "label": "RSA private key"}]},
    {"id": "ed25519", "label": "Ed25519 key pair", "fields": [{"name": "apiKey", "label": "API key"}, {"name": "privateKey", "label": "Ed25519 private key"}]},
]}
PYTHON_ADAPTERS = CCXT_ADAPTERS
async def _credential_refresh_loop():
    """Refresh live-capable saved accounts without ever submitting orders."""
    while True:
        try:
            db = _connect()
            try:
                rows = db.execute("SELECT user_id,exchange_id,auth_mode,encrypted_credentials,verification_json,state FROM exchange_credentials WHERE state='LIVE_READY' OR verification_json LIKE '%liveEligible%true%'").fetchall()
            finally:
                db.close()
            for row in rows:
                exchange = None
                try:
                    credentials = json.loads(_decrypt(row["encrypted_credentials"]).decode())
                    exchange = _make_exchange(row["exchange_id"], row["auth_mode"], credentials)
                    evidence, balances, book = await _probe_exchange(row["exchange_id"], exchange, "BTC/USDT")
                    saved = json.loads(row["verification_json"] or "{}")
                    attested = bool((saved.get("evidence") or {}).get("operatorAttestedLive"))
                    if not (evidence.get("authentication") and evidence.get("account") and evidence.get("balances")
                             and evidence.get("scannerEligible") and evidence.get("executionEligible")
                             and (evidence.get("liveEligible") or attested)):
                        continue
                    if attested and not evidence.get("liveEligible"):
                        evidence["operatorAttestedLive"] = True
                        evidence["livePermissionMode"] = "operator_attested"
                        evidence["liveEligible"] = True
                    saved["evidence"] = evidence
                    saved["balances"] = balances or {}
                    if book: saved["orderBook"] = book
                    saved["balanceRefreshedAt"] = datetime.now(timezone.utc).isoformat()
                    db = _connect()
                    try:
                        db.execute("UPDATE exchange_credentials SET last_verified=?,verification_json=?,state=? WHERE user_id=? AND exchange_id=?", (evidence["verifiedAt"], json.dumps(saved, separators=(",", ":")), "LIVE_READY" if row["state"] == "LIVE_READY" else "FULLY_VERIFIED", row["user_id"], row["exchange_id"]))
                        db.commit()
                    finally:
                        db.close()
                except Exception:
                    continue
                finally:
                    if exchange is not None:
                        try: await exchange.close()
                        except Exception: pass
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(ACCOUNT_REFRESH_SECONDS)


@asynccontextmanager
async def _lifespan(_app):
    task = asyncio.create_task(_credential_refresh_loop(), name="arbx-credential-refresh")
    try:
        yield
    finally:
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass


app = FastAPI(title="ARBX Control API", version="1.0.0", lifespan=_lifespan)
engine_owner_id: str | None = None
engine_hub = None
engine_task: asyncio.Task | None = None
engine_phase = "STOPPED"
engine_error: str | None = None
engine_stop_requested = False
engine_logs: queue.Queue = queue.Queue(maxsize=100)
engine_lock = asyncio.Lock()


class _EngineOut:
    def put(self, event):
        try:
            engine_logs.put_nowait(event)
        except queue.Full:
            try:
                engine_logs.get_nowait()
            except queue.Empty:
                pass
            try:
                engine_logs.put_nowait(event)
            except queue.Full:
                pass


async def _run_engine_job(payload: "EngineStart", credentials_by_id: dict[str, dict[str, str]],
                         auth_modes: dict[str, str]) -> None:
    global engine_phase, engine_error
    try:
        if payload.mode == "live" or payload.require_private_stream:
            for exchange_id in payload.exchange_ids:
                credentials = credentials_by_id[exchange_id]
                exchange = _make_exchange(exchange_id, auth_modes[exchange_id], credentials)
                try:
                    evidence, _, _ = await _probe_exchange(exchange_id, exchange, "BTC/USDT")
                finally:
                    try:
                        await exchange.close()
                    except Exception:
                        pass
                if payload.mode == "live" and not evidence["liveEligible"]:
                    raise RuntimeError("live permission preflight failed")
                if payload.require_private_stream and not evidence["scannerEligible"]:
                    raise RuntimeError("authenticated scanner preflight failed")
        if engine_stop_requested:
            engine_phase = "STOPPED"
            return
        engine_phase = "STARTING"
        await engine_hub.run()
        engine_phase = engine_hub.stats.status
    except Exception as exc:
        engine_error = type(exc).__name__
        engine_phase = "PREFLIGHT_FAILED"


@app.exception_handler(RequestValidationError)
async def validation_error_without_input(request: Request, exc: RequestValidationError):
    # Pydantic errors can include submitted values; credential and password payloads must not echo.
    return JSONResponse(status_code=422, content={"detail": "Invalid request. Check the required fields and try again."})


def _connect():
    APP_DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(APP_DB, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.executescript("""CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, salt BLOB NOT NULL, password_hash BLOB NOT NULL,
        created_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        expires_at INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS exchange_credentials (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, exchange_id TEXT NOT NULL,
        encrypted_credentials BLOB NOT NULL, auth_mode TEXT NOT NULL, state TEXT NOT NULL,
        last_verified TEXT, verification_json TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY(user_id, exchange_id));""")
    db.commit()
    return db


def _proxy_auth(x_engine_token: str | None = Header(default=None)):
    expected = os.getenv("ENGINE_PROXY_TOKEN", "")
    if not expected or not x_engine_token or not hmac.compare_digest(expected, x_engine_token):
        raise HTTPException(503, "Control API proxy is not configured")


def _user(db: sqlite3.Connection, token: str | None):
    if not token:
        raise HTTPException(401, "Sign in required")
    digest = hashlib.sha256(token.encode()).hexdigest()
    row = db.execute("SELECT user_id, expires_at FROM sessions WHERE token_hash=?", (digest,)).fetchone()
    if not row or row["expires_at"] < int(time.time()):
        if row:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (digest,)); db.commit()
        raise HTTPException(401, "Session expired; sign in again")
    return row["user_id"]


def _encrypt(raw: bytes) -> bytes:
    key_hex = os.getenv("CREDENTIAL_ENCRYPTION_KEY", "")
    try:
        key = bytes.fromhex(key_hex)
    except ValueError as exc:
        raise HTTPException(503, "CREDENTIAL_ENCRYPTION_KEY must be 64 hexadecimal characters") from exc
    if len(key) != 32:
        raise HTTPException(503, "CREDENTIAL_ENCRYPTION_KEY must be 64 hexadecimal characters")
    nonce = secrets.token_bytes(12)
    return nonce + AESGCM(key).encrypt(nonce, raw, b"arbx-exchange-credentials-v1")


def _decrypt(blob: bytes) -> bytes:
    key = bytes.fromhex(os.getenv("CREDENTIAL_ENCRYPTION_KEY", ""))
    if len(key) != 32:
        raise RuntimeError("credential encryption key is not configured")
    return AESGCM(key).decrypt(blob[:12], blob[12:], b"arbx-exchange-credentials-v1")


class Signup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)
    password: str = Field(min_length=12, max_length=256)
    password_confirmation: str = Field(min_length=12, max_length=256)


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)
    password: str = Field(min_length=1, max_length=256)


class ExchangeConnect(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    credentials: dict[str, str] = Field(min_length=1, max_length=8)
    auth_mode: str = "ccxt"
    symbol: str = Field(default="BTC/USDT", pattern=r"^[A-Z0-9]{2,20}/[A-Z0-9]{2,20}$", max_length=41)

    @field_validator("credentials")
    @classmethod
    def bound_credential_payload(cls, credentials):
        if any(len(key) > 32 or len(value) > 16_384 for key, value in credentials.items()):
            raise ValueError("Credential fields exceed their maximum length")
        return credentials


class LiveReadyPromotion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: Literal["PROMOTE LIVE READY"]
    permission_attestation: Literal["I CONFIRM TRADE-ONLY API KEY"] | None = None


class EngineStart(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    mode: Literal["paper", "live"]
    exchange_ids: list[str] = Field(min_length=1, max_length=MAX_EXCHANGES)
    trade_size_usd: float = Field(gt=0, le=25)
    max_loss_usd: float = Field(gt=0, le=3)
    target_profit_usd: float = Field(gt=0, le=1000)
    require_private_stream: bool = True
    cross_live: bool = False
    live_confirmation: str = ""

    @field_validator("exchange_ids")
    @classmethod
    def unique_supported_venues(cls, exchange_ids):
        if len(set(exchange_ids)) != len(exchange_ids) or any(item not in VENUES for item in exchange_ids):
            raise ValueError("Select unique supported exchange IDs")
        return exchange_ids

    @model_validator(mode="after")
    def validate_cross_live_selection(self):
        if self.mode == "live" and (not self.cross_live or len(self.exchange_ids) < 2):
            raise ValueError("Live pilot requires at least two venues and live cross-exchange execution")
        if self.cross_live and self.mode != "live":
            raise ValueError("Live cross-exchange execution can only be selected in live mode")
        return self


def _make_exchange(exchange_id: str, auth_mode: str, credentials: dict[str, str]):
    import ccxt.pro as ccxtpro
    from arbx.worker import spot_market_options
    adapter_id = PYTHON_ADAPTERS.get(exchange_id, exchange_id)
    cls = getattr(ccxtpro, adapter_id, None)
    if cls is None:
        raise HTTPException(501, "This venue has no CCXT Pro adapter in the installed runtime")
    config = dict(credentials)
    if exchange_id == "binance" and auth_mode in ("rsa", "ed25519"):
        # CCXT's RSA/Ed25519 signing helpers consume PEM material as bytes.
        config["secret"] = config.pop("privateKey").encode("utf-8")
    return cls({**config, "enableRateLimit": True, "timeout": 10000,
                "options": spot_market_options(exchange_id)})


def _safe_exchange_error(exc: Exception, exchange) -> str:
    """Keep useful venue diagnostics while removing keys, signatures, and private material."""
    message = str(exc)[:1000]
    for value in (getattr(exchange, "apiKey", None), getattr(exchange, "secret", None),
                  getattr(exchange, "password", None)):
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="ignore")
        if isinstance(value, str) and value:
            message = message.replace(value, "[REDACTED]")
    message = re.sub(r"(?i)(api[-_]?key|signature|sign|passphrase|secret)=([^&\s]+)", r"\1=[REDACTED]", message)
    message = re.sub(r"-----BEGIN [^-]+-----.*?-----END [^-]+-----", "[REDACTED PRIVATE KEY]", message, flags=re.S)
    return f"{type(exc).__name__}: {message}"[:1200]


async def _probe_exchange(exchange_id: str, exchange, symbol: str) -> tuple[dict, dict | None, dict | None]:
    evidence = {"rest": False, "authentication": False, "account": False, "balances": False,
                "publicWebSocket": False, "privateWebSocket": False,
                "tradePermission": "unverified", "withdrawalsDisabled": "unverified",
                "scannerEligible": False, "executionEligible": False, "liveEligible": False,
                "connectionState": "VERIFYING", "verificationStartedAt": datetime.now(timezone.utc).isoformat()}
    balance_summary = None
    book_summary = None
    market_ok = False
    try:
        t0 = time.perf_counter()
        await exchange.load_markets()
        if exchange.has.get("fetchTime") is not True:
            raise RuntimeError("adapter does not expose a server-time request")
        evidence["rest"] = bool(await exchange.fetch_time())
        evidence["restRttMs"] = round((time.perf_counter() - t0) * 1000, 1)
        market = exchange.market(symbol)
        if not market.get("spot") or market.get("contract"):
            raise RuntimeError("verification symbol is not a spot market")
        market_ok = True
        balance = await exchange.fetch_balance()
        if not isinstance(balance, dict) or any(
            not isinstance(balance.get(field), dict) for field in ("free", "used", "total")
        ):
            raise RuntimeError("exchange returned an incomplete unified balance snapshot")
        evidence["authentication"] = True
        evidence["account"] = True
        evidence["balances"] = True
        balance_summary = {asset: {"free": float((balance.get("free") or {}).get(asset) or 0),
                                   "used": float((balance.get("used") or {}).get(asset) or 0),
                                   "total": float((balance.get("total") or {}).get(asset) or 0)}
                           for asset in (balance.get("total") or {})
                           if float((balance.get("total") or {}).get(asset) or 0) > 0}
    except Exception as exc:
        evidence["accountError"] = _safe_exchange_error(exc, exchange)

    if evidence["authentication"] and exchange.has.get("watchBalance") is True:
        try:
            private_balance = await asyncio.wait_for(exchange.watch_balance(), timeout=15)
            if isinstance(private_balance, dict) and all(key in private_balance for key in ("free", "used", "total")):
                evidence["privateWebSocket"] = True
                evidence["privateWebSocketAt"] = datetime.now(timezone.utc).isoformat()
            else:
                evidence["privateWebSocketError"] = "No unified balance snapshot received"
        except Exception as exc:
            evidence["privateWebSocketError"] = _safe_exchange_error(exc, exchange)
    elif evidence["authentication"]:
        evidence["privateWebSocketError"] = "Adapter does not advertise watchBalance support"

    if evidence["authentication"]:
        try:
            permission = await inspect_permissions(exchange_id, exchange)
            evidence["permissions"] = permission
            evidence["tradePermission"] = permission.get("tradePermission", "unverified")
            evidence["withdrawalsDisabled"] = permission.get("withdrawalsDisabled", "unverified")
        except Exception as exc:
            evidence["permissionError"] = _safe_exchange_error(exc, exchange)
            evidence["permissions"] = {"liveEligible": False}

    if market_ok and exchange.has.get("watchOrderBook") is True:
        try:
            limit = orderbook_limit(exchange_id, 10)
            book = await asyncio.wait_for(exchange.watch_order_book(symbol, limit), timeout=12)
            if book.get("bids") and book.get("asks"):
                evidence["publicWebSocket"] = True
                book_summary = {"symbol": symbol, "bestBid": book["bids"][0][0],
                                "bestAsk": book["asks"][0][0],
                                "receivedAt": datetime.now(timezone.utc).isoformat(),
                                "sequence": book.get("nonce")}
                evidence["orderBook"] = book_summary
        except Exception as exc:
            evidence["publicWebSocketError"] = _safe_exchange_error(exc, exchange)

    adapter_has = getattr(exchange, "has", {}) or {}
    time_in_force = None
    feature_value = getattr(exchange, "feature_value", None)
    if market_ok and callable(feature_value):
        try:
            time_in_force = feature_value(symbol, "createOrder", "timeInForce")
        except Exception:
            pass
    evidence["executionCapabilities"] = {
        "spotMarket": market_ok,
        "createOrder": adapter_has.get("createOrder") is True,
        "marketOrderUnwind": adapter_has.get("createMarketOrder") is True,
        "fetchOrder": adapter_has.get("fetchOrder") is True,
        "iocLimit": isinstance(time_in_force, dict) and time_in_force.get("IOC") is True,
    }
    evidence["executionEligible"] = all(evidence["executionCapabilities"].values())
    evidence["scannerEligible"] = bool(evidence["rest"] and evidence["authentication"] and evidence["account"]
                                        and evidence["balances"]
                                        and evidence["privateWebSocket"] and evidence["publicWebSocket"])
    evidence["liveEligible"] = bool(evidence["scannerEligible"] and evidence["executionEligible"]
                                     and (evidence.get("permissions") or {}).get("liveEligible") is True)
    if evidence["scannerEligible"]:
        evidence["connectionState"] = "FULLY_VERIFIED"
    elif evidence["authentication"] and evidence["account"] and evidence["balances"]:
        evidence["connectionState"] = "ACCOUNT_DATA_CONNECTED"
    elif evidence["authentication"]:
        evidence["connectionState"] = "AUTHENTICATED"
    elif evidence["publicWebSocket"]:
        evidence["connectionState"] = "MARKET_DATA_CONNECTED"
    else:
        evidence["connectionState"] = "FAILED"
    evidence["verifiedAt"] = datetime.now(timezone.utc).isoformat()
    return evidence, balance_summary, book_summary


def _verification_is_fresh(verified_at: str | None, *, now: float | None = None) -> bool:
    if not verified_at:
        return False
    try:
        verified = datetime.fromisoformat(verified_at.replace("Z", "+00:00"))
        age = (time.time() if now is None else now) - verified.timestamp()
    except (TypeError, ValueError, OverflowError):
        return False
    return 0 <= age <= VERIFICATION_TTL_SECONDS


def _new_session(db, user_id: str, response: Response):
    token = secrets.token_urlsafe(32)
    db.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)",
               (hashlib.sha256(token.encode()).hexdigest(), user_id, int(time.time()) + SESSION_TTL))
    db.commit()
    response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=os.getenv("APP_SECURE_COOKIE", "1") == "1",
                        samesite="lax", max_age=SESSION_TTL, path="/")


def _current_user(request: Request, db: sqlite3.Connection):
    return _user(db, request.cookies.get(SESSION_COOKIE))


@app.get("/api/v1/health", dependencies=[Depends(_proxy_auth)])
def health():
    enabled = os.getenv("ARBX_LIVE_TRADING_ENABLED", "0") == "1"
    return {"service": "arbx-control-api", "status": "available", "execution_enabled": enabled}


@app.get("/healthz")
def healthz():
    token_ok = bool(os.getenv("ENGINE_PROXY_TOKEN"))
    try:
        key_ok = len(bytes.fromhex(os.getenv("CREDENTIAL_ENCRYPTION_KEY", ""))) == 32
    except ValueError:
        key_ok = False
    if not token_ok or not key_ok:
        raise HTTPException(503, "Control service configuration is incomplete")
    db = _connect()
    try:
        db.execute("CREATE TABLE IF NOT EXISTS service_health_probe (ok INTEGER NOT NULL)")
        db.execute("INSERT INTO service_health_probe(ok) VALUES(1)")
        db.execute("DELETE FROM service_health_probe")
        db.commit()
    finally:
        db.close()
    return {"status": "ready"}


@app.get("/api/v1/live/activation-readiness", dependencies=[Depends(_proxy_auth)])
def live_activation_readiness(request: Request):
    """Return a fail-closed activation plan; never enables trading."""
    db = _connect()
    try:
        uid = _current_user(request, db)
        rows = db.execute(
            "SELECT exchange_id,state,last_verified,verification_json FROM exchange_credentials WHERE user_id=?",
            (uid,),
        ).fetchall()
    finally:
        db.close()
    by_exchange = {}
    for row in rows:
        evidence = json.loads(row["verification_json"] or "{}").get("evidence") or {}
        fresh = _verification_is_fresh(row["last_verified"])
        scanner_ready = bool(evidence.get("scannerEligible"))
        execution_ready = bool(evidence.get("executionEligible"))
        live_ready = bool(evidence.get("liveEligible"))
        activation_ready = row["state"] == "LIVE_READY" and fresh and scanner_ready and execution_ready and live_ready
        by_exchange[row["exchange_id"]] = {
            "state": row["state"],
            "fresh": fresh,
            "scannerEligible": scanner_ready,
            "executionEligible": execution_ready,
            "liveEligible": live_ready,
            "permissionMode": evidence.get("livePermissionMode", "unverified"),
            "activationReady": activation_ready,
        }
    venues = []
    for exchange_id, name in VENUES.items():
        item = by_exchange.get(exchange_id, {"state":"NOT_CONFIGURED","fresh":False,"scannerEligible":False,
            "executionEligible":False,"liveEligible":False,"permissionMode":"unverified","activationReady":False})
        venues.append({"id": exchange_id, "name": name, **item})
    ready = [v for v in venues if v["activationReady"]]
    cross_live_ready = len(ready) >= 2
    return {
        "liveTradingEnabled": os.getenv("ARBX_LIVE_TRADING_ENABLED", "0") == "1",
        "activationReady": cross_live_ready,
        "crossLiveReady": cross_live_ready,
        "minimumReadyVenues": 2,
        "readyExchangeIds": [v["id"] for v in ready],
        "readyCount": len(ready),
        "registeredCount": len(VENUES),
        "venues": venues,
        "action": "ENABLE_OPERATOR_LIVE_FLAG_AFTER_PREFLIGHT" if cross_live_ready else "VERIFY_AND_PROMOTE_SELECTED_ACCOUNTS",
        "note": "This endpoint is read-only and never enables live trading."
    }

@app.get("/api/v1/runtime", dependencies=[Depends(_proxy_auth)])
def runtime_config():
    return {"liveTradingEnabled": os.getenv("ARBX_LIVE_TRADING_ENABLED", "0") == "1",
            "executionActive": bool(engine_task and not engine_task.done()),
            "executionEnabled": os.getenv("ARBX_LIVE_TRADING_ENABLED", "0") == "1",
            "enginePhase": engine_phase}


@app.post("/api/v1/auth/signup", dependencies=[Depends(_proxy_auth)])
def signup(payload: Signup, response: Response):
    if payload.password != payload.password_confirmation:
        raise HTTPException(422, "Passwords do not match")
    db = _connect(); user_id = secrets.token_hex(16); salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", payload.password.encode(), salt, 310_000)
    try:
        db.execute("INSERT INTO users(id,email,salt,password_hash,created_at) VALUES(?,?,?,?,?)",
                   (user_id, payload.email.lower(), salt, digest, datetime.now(timezone.utc).isoformat()))
        db.commit(); _new_session(db, user_id, response)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "An account with this email already exists") from exc
    finally:
        db.close()
    return {"user": {"id": user_id, "email": payload.email.lower()}}


@app.post("/api/v1/auth/login", dependencies=[Depends(_proxy_auth)])
def login(payload: Login, response: Response):
    db = _connect()
    try:
        row = db.execute("SELECT id,email,salt,password_hash FROM users WHERE email=?", (payload.email.lower(),)).fetchone()
        supplied = hashlib.pbkdf2_hmac("sha256", payload.password.encode(), row["salt"], 310_000) if row else b""
        if not row or not hmac.compare_digest(supplied, row["password_hash"]):
            raise HTTPException(401, "Email or password is incorrect")
        _new_session(db, row["id"], response)
        return {"user": {"id": row["id"], "email": row["email"]}}
    finally:
        db.close()


@app.post("/api/v1/auth/logout", dependencies=[Depends(_proxy_auth)])
def logout(request: Request, response: Response):
    db = _connect(); token = request.cookies.get(SESSION_COOKIE)
    uid = None
    if token:
        row = db.execute("SELECT user_id FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        uid = row["user_id"] if row else None
    if uid and uid == engine_owner_id and engine_task is not None and not engine_task.done():
        db.close()
        raise HTTPException(409, "Stop the engine before signing out")
    if token:
        db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),)); db.commit()
    db.close(); response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, secure=True, samesite="lax")
    return {"ok": True}


@app.get("/api/v1/auth/me", dependencies=[Depends(_proxy_auth)])
def me(request: Request):
    db = _connect()
    try:
        user_id = _current_user(request, db)
        row = db.execute("SELECT id,email FROM users WHERE id=?", (user_id,)).fetchone()
        return {"user": dict(row)}
    finally:
        db.close()


@app.get("/api/v1/exchanges", dependencies=[Depends(_proxy_auth)])
async def exchanges(request: Request):
    db = _connect()
    try:
        uid = _current_user(request, db)
        rows = {r["exchange_id"]: r for r in db.execute(
            "SELECT exchange_id,auth_mode,state,last_verified,verification_json FROM exchange_credentials WHERE user_id=?", (uid,))}
        try:
            import ccxt.pro as ccxtpro
        except Exception:
            ccxtpro = None
        result = []
        for exchange_id, name in VENUES.items():
            adapter_id = PYTHON_ADAPTERS.get(exchange_id, exchange_id)
            cls = getattr(ccxtpro, adapter_id, None) if ccxtpro else None
            schema = AUTH_SCHEMAS[exchange_id]["modes"]
            row = rows.get(exchange_id)
            saved = json.loads(row["verification_json"]) if row else {}
            fresh = bool(row and _verification_is_fresh(row["last_verified"]))
            saved_evidence = saved.get("evidence", {})
            saved_state = row["state"] if row else "NOT_CONFIGURED"
            if row and not fresh and saved_state not in ("NOT_CONFIGURED", "FAILED", "DISABLED"):
                saved_state = "STALE"
            scanner_eligible = fresh and bool(saved_evidence.get("scannerEligible"))
            execution_eligible = fresh and bool(saved_evidence.get("executionEligible"))
            live_eligible = fresh and bool(saved_evidence.get("liveEligible"))
            if not bool(cls):
                engagement_state, engagement_label = "ADAPTER_UNAVAILABLE", "Adapter unavailable"
            elif saved_state == "FAILED":
                engagement_state, engagement_label = "VERIFICATION_FAILED", "Verification failed"
            elif saved_state == "STALE":
                engagement_state, engagement_label = "STALE", "Verification expired"
            elif saved_state == "LIVE_READY" and live_eligible:
                engagement_state, engagement_label = "LIVE_READY", "Live-ready"
            elif live_eligible:
                engagement_state, engagement_label = "VERIFIED_LIVE_CAPABLE", "Verified live-capable"
            elif execution_eligible:
                engagement_state, engagement_label = "EXECUTION_CAPABLE", "Potential · execution capable"
            elif scanner_eligible:
                engagement_state, engagement_label = "ACCOUNT_VERIFIED", "Potential · account verified"
            elif row:
                engagement_state, engagement_label = "CONNECTED_UNVERIFIED", "Potential · connectivity unverified"
            else:
                engagement_state, engagement_label = "POTENTIAL", "Potential"
            result.append({"id": exchange_id, "name": name, "adapterAvailable": bool(cls),
                           "authenticationModes": schema, "state": saved_state,
                           "engagementState": engagement_state, "engagementLabel": engagement_label,
                           "permissionVerificationAvailable": True,
                           "liveTradingAvailable": True,
                           "lastVerified": row["last_verified"] if row else None,
                           "verificationFresh": fresh,
                           "evidence": saved_evidence, "balances": saved.get("balances"),
                           "orderBook": saved.get("orderBook"), "maskedKey": saved.get("maskedKey"),
                           "balanceRefreshedAt": saved.get("balanceRefreshedAt"),
                           "credentialConnection": {"configured": bool(row), "adapterConnected": bool(saved_evidence.get("authentication") and saved_evidence.get("account")), "balanceConnected": bool(saved_evidence.get("balances")), "lastBalanceRefresh": saved.get("balanceRefreshedAt") or (row["last_verified"] if row else None), "balanceAssetCount": len(saved.get("balances") or {}), "refreshMode": "persistent" if saved_evidence.get("liveEligible") else "on-demand"},
                           "scannerEligible": scanner_eligible,
                           "executionEligible": execution_eligible,
                           "liveEligible": live_eligible,
                           "livePermissionMode": (saved_evidence.get("livePermissionMode") or ("verified" if live_eligible else "unverified")),
                           "liveReady": bool(saved_state == "LIVE_READY" and live_eligible),
                           "executionEnabled": bool(saved_state == "LIVE_READY" and live_eligible and os.getenv("ARBX_LIVE_TRADING_ENABLED", "0") == "1")})
        return {"exchanges": result}
    finally:
        db.close()


@app.get("/api/v1/capital-sources", dependencies=[Depends(_proxy_auth)])
def capital_sources(request: Request):
    """Return only fresh, authenticated balance snapshots; this endpoint never moves funds."""
    db = _connect()
    try:
        uid = _current_user(request, db)
        rows = db.execute(
            "SELECT exchange_id,last_verified,verification_json FROM exchange_credentials WHERE user_id=?",
            (uid,),
        ).fetchall()
        sources = []
        for row in rows:
            if not _verification_is_fresh(row["last_verified"]):
                continue
            saved = json.loads(row["verification_json"])
            evidence = saved.get("evidence", {})
            if not evidence.get("scannerEligible"):
                continue
            for asset, amounts in (saved.get("balances") or {}).items():
                free = float(amounts.get("free") or 0)
                used = float(amounts.get("used") or 0)
                if free <= 0 and used <= 0:
                    continue
                sources.append({
                    "exchangeId": row["exchange_id"], "exchange": VENUES.get(row["exchange_id"], row["exchange_id"]),
                    "asset": asset, "available": free, "reserved": used, "usable": free,
                    "valuationUsd": None, "valuationNote": "No external FX valuation is applied",
                    "lastVerified": row["last_verified"],
                })
        return {"sources": sources, "fundsMoved": False}
    finally:
        db.close()


@app.post("/api/v1/exchanges/{exchange_id}/verify", dependencies=[Depends(_proxy_auth)])
async def verify_exchange(exchange_id: str, payload: ExchangeConnect, request: Request):
    if exchange_id not in VENUES:
        raise HTTPException(404, "Exchange is not in the supported venue registry")
    uid = None; db = _connect()
    try:
        uid = _current_user(request, db)
        if engine_owner_id == uid and engine_task is not None and not engine_task.done():
            raise HTTPException(409, "Stop the active engine session before changing saved exchange credentials")
        if not payload.credentials or any(not isinstance(k, str) or not isinstance(v, str) for k, v in payload.credentials.items()):
            raise HTTPException(422, "Credential fields must be text values")
        modes = {item["id"]: item for item in AUTH_SCHEMAS[exchange_id]["modes"]}
        mode = modes.get(payload.auth_mode)
        if not mode:
            raise HTTPException(422, "Authentication mode is not supported for this exchange")
        allowed_fields = {field["name"] for field in mode["fields"]}
        if set(payload.credentials) != allowed_fields or any(not value.strip() for value in payload.credentials.values()):
            raise HTTPException(422, "Provide exactly the non-empty credential fields shown for this authentication mode")
        try:
            exchange = _make_exchange(exchange_id, payload.auth_mode, payload.credentials)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(500, f"Could not initialize adapter: {type(exc).__name__}") from exc
        try:
            evidence, balance_summary, book_summary = await _probe_exchange(exchange_id, exchange, payload.symbol)
        finally:
            try: await exchange.close()
            except Exception: pass
        live_eligible = evidence["liveEligible"]
        scanner_eligible = evidence["scannerEligible"]
        state = evidence["connectionState"]
        verified_at = evidence["verifiedAt"]
        key_preview = payload.credentials.get("apiKey", "")
        masked = f"{key_preview[:3]}••••{key_preview[-3:]}" if len(key_preview) >= 7 else "••••"
        encrypted = _encrypt(json.dumps(payload.credentials, separators=(",", ":")).encode())
        saved_verification = {"evidence": evidence, "balances": balance_summary,
                              "orderBook": book_summary, "maskedKey": masked,
                              "balanceRefreshedAt": datetime.now(timezone.utc).isoformat()}
        db.execute("""INSERT INTO exchange_credentials(user_id,exchange_id,encrypted_credentials,auth_mode,state,last_verified,verification_json)
                      VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id,exchange_id) DO UPDATE SET
                      encrypted_credentials=excluded.encrypted_credentials,auth_mode=excluded.auth_mode,state=excluded.state,
                      last_verified=excluded.last_verified,verification_json=excluded.verification_json""",
                   (uid, exchange_id, encrypted, payload.auth_mode, state, verified_at,
                    json.dumps(saved_verification, separators=(",", ":"))))
        db.commit()
        return {"id": exchange_id, "name": VENUES[exchange_id], "state": state, "maskedKey": masked,
                "verifiedAt": verified_at, "evidence": evidence, "balances": balance_summary,
                "orderBook": book_summary, "scannerEligible": scanner_eligible, "liveEligible": live_eligible,
                "executionEnabled": live_eligible and os.getenv("ARBX_LIVE_TRADING_ENABLED", "0") == "1"}
    finally:
        db.close()


@app.post("/api/v1/exchanges/{exchange_id}/refresh", dependencies=[Depends(_proxy_auth)])
async def refresh_exchange_account(exchange_id: str, request: Request):
    """Reconnect a saved API key and persist a fresh authenticated balance snapshot."""
    if exchange_id not in VENUES:
        raise HTTPException(404, "Exchange is not in the supported venue registry")
    db = _connect()
    try:
        uid = _current_user(request, db)
        row = db.execute("SELECT auth_mode,encrypted_credentials,state,verification_json FROM exchange_credentials WHERE user_id=? AND exchange_id=?", (uid, exchange_id)).fetchone()
    finally:
        db.close()
    if not row:
        raise HTTPException(409, "Connect and verify the exchange API key before refreshing")
    exchange = None
    try:
        credentials = json.loads(_decrypt(row["encrypted_credentials"]).decode())
        exchange = _make_exchange(exchange_id, row["auth_mode"], credentials)
        evidence, balances, book = await _probe_exchange(exchange_id, exchange, "BTC/USDT")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Exchange adapter connection failed: {type(exc).__name__}") from exc
    finally:
        if exchange is not None:
            try: await exchange.close()
            except Exception: pass
    if not (evidence.get("authentication") and evidence.get("account") and evidence.get("balances")):
        raise HTTPException(502, evidence.get("accountError") or "API key authentication/balance probe failed")
    db = _connect()
    try:
        saved = json.loads(row["verification_json"] or "{}")
        saved["evidence"] = evidence
        saved["balances"] = balances or {}
        if book: saved["orderBook"] = book
        saved["balanceRefreshedAt"] = datetime.now(timezone.utc).isoformat()
        saved_evidence = json.loads(row["verification_json"] or "{}").get("evidence") or {}
        attested = bool(saved_evidence.get("operatorAttestedLive"))
        if attested and not evidence.get("liveEligible") and evidence.get("scannerEligible") and evidence.get("executionEligible"):
            evidence["operatorAttestedLive"] = True
            evidence["livePermissionMode"] = "operator_attested"
            evidence["liveEligible"] = True
        next_state = "LIVE_READY" if row["state"] == "LIVE_READY" and evidence.get("liveEligible") else evidence.get("connectionState", row["state"])
        if evidence.get("liveEligible"):
            db.execute("UPDATE exchange_credentials SET state=?,last_verified=?,verification_json=? WHERE user_id=? AND exchange_id=?", (next_state, evidence["verifiedAt"], json.dumps(saved, separators=(",", ":")), uid, exchange_id))
        else:
            db.execute("UPDATE exchange_credentials SET state=?,verification_json=? WHERE user_id=? AND exchange_id=?", (next_state, json.dumps(saved, separators=(",", ":")), uid, exchange_id))
        db.commit()
        return {"id": exchange_id, "name": VENUES[exchange_id], "state": next_state, "liveEligible": bool(evidence.get("liveEligible")), "liveReady": bool(next_state == "LIVE_READY" and evidence.get("liveEligible")), "adapterConnected": True, "balanceConnected": True, "balanceRefreshedAt": saved["balanceRefreshedAt"], "balances": saved["balances"], "evidence": evidence}
    finally:
        db.close()

@app.post("/api/v1/exchanges/{exchange_id}/promote-live-ready", dependencies=[Depends(_proxy_auth)])
async def promote_live_ready(exchange_id: str, payload: LiveReadyPromotion, request: Request):
    """Require a fresh adapter reconnect before marking an account LIVE_READY."""
    if exchange_id not in VENUES:
        raise HTTPException(404, "Exchange is not in the supported venue registry")
    db = _connect()
    try:
        uid = _current_user(request, db)
        row = db.execute("SELECT auth_mode,encrypted_credentials,state,verification_json FROM exchange_credentials WHERE user_id=? AND exchange_id=?", (uid, exchange_id)).fetchone()
    finally:
        db.close()
    if not row:
        raise HTTPException(409, "Verify the exchange account before promotion")
    exchange = None
    try:
        credentials = json.loads(_decrypt(row["encrypted_credentials"]).decode())
        exchange = _make_exchange(exchange_id, row["auth_mode"], credentials)
        evidence, balances, book = await _probe_exchange(exchange_id, exchange, "BTC/USDT")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Live-ready adapter reconnect failed: {type(exc).__name__}") from exc
    finally:
        if exchange is not None:
            try: await exchange.close()
            except Exception: pass
    if not (evidence.get("authentication") and evidence.get("account") and evidence.get("balances")):
        raise HTTPException(409, evidence.get("accountError") or "Stored API key could not authenticate and return balances")
    permission_verified = (evidence.get("permissions") or {}).get("liveEligible") is True
    operator_attested = payload.permission_attestation == "I CONFIRM TRADE-ONLY API KEY"
    if not permission_verified:
        if not operator_attested or not evidence.get("scannerEligible") or not evidence.get("executionEligible"):
            raise HTTPException(409, "Fresh adapter, stream, execution, and explicit trade-only API-key attestation are required for this venue")
        evidence["operatorAttestedLive"] = True
        evidence["livePermissionMode"] = "operator_attested"
        evidence["liveEligible"] = True
    else:
        evidence["livePermissionMode"] = "verified"
    saved = json.loads(row["verification_json"] or "{}")
    saved["evidence"] = evidence
    saved["balances"] = balances or {}
    if book: saved["orderBook"] = book
    saved["balanceRefreshedAt"] = datetime.now(timezone.utc).isoformat()
    db = _connect()
    try:
        db.execute("UPDATE exchange_credentials SET state=?,last_verified=?,verification_json=? WHERE user_id=? AND exchange_id=?", ("LIVE_READY", evidence["verifiedAt"], json.dumps(saved, separators=(",", ":")), uid, exchange_id))
        db.commit()
        return {"id": exchange_id, "name": VENUES[exchange_id], "state": "LIVE_READY",
                "liveReady": True, "liveEligible": True, "executionEnabled": False,
                "liveTradingEnabled": os.getenv("ARBX_LIVE_TRADING_ENABLED", "0") == "1",
                "orderSubmitted": False, "verifiedAt": evidence["verifiedAt"],
                "balanceRefreshedAt": saved["balanceRefreshedAt"], "balances": saved["balances"]}
    finally:
        db.close()

@app.delete("/api/v1/exchanges/{exchange_id}", dependencies=[Depends(_proxy_auth)])
def remove_exchange(exchange_id: str, request: Request):
    db = _connect()
    try:
        uid = _current_user(request, db)
        if engine_owner_id == uid and engine_task is not None and not engine_task.done():
            raise HTTPException(409, "Stop the active engine session before changing saved exchange credentials")
        db.execute("DELETE FROM exchange_credentials WHERE user_id=? AND exchange_id=?", (uid, exchange_id)); db.commit()
        return {"id": exchange_id, "state": "NOT_CONFIGURED"}
    finally:
        db.close()


@app.get("/api/v1/engine/state", dependencies=[Depends(_proxy_auth)])
def engine_state(request: Request):
    db = _connect()
    try:
        uid = _current_user(request, db)
    finally:
        db.close()
    if engine_owner_id != uid or engine_hub is None:
        return {"status": "STOPPED", "mode": None, "stats": None}
    stats = engine_hub.stats.snapshot()
    status = engine_phase if engine_phase in ("PREFLIGHTING", "PREFLIGHT_FAILED", "STOPPING") else stats["status"]
    if engine_task and engine_task.done() and status.startswith("RUNNING"):
        status = "STOPPED"
    stats["venues"] = [worker.health_snapshot() for worker in engine_hub.workers]
    return {"status": status, "mode": engine_hub.cfg.mode,
            "stats": stats,
            "error": engine_error,
            "executionEnabled": engine_hub.cfg.mode == "live" and os.getenv("ARBX_LIVE_TRADING_ENABLED", "0") == "1"}


@app.get("/api/v1/engine/journal", dependencies=[Depends(_proxy_auth)])
def engine_journal(request: Request):
    db = _connect()
    try:
        uid = _current_user(request, db)
    finally:
        db.close()
    journal_path = APP_DB.parent / f"engine_{uid}.sqlite3"
    if not journal_path.exists():
        return {"trades": [], "realizedNetPnl": 0.0, "scope": "all durable engine sessions"}
    journal_db = sqlite3.connect(f"file:{journal_path.as_posix()}?mode=ro", uri=True, timeout=5)
    journal_db.row_factory = sqlite3.Row
    try:
        total = journal_db.execute(
            "SELECT COALESCE(SUM(net_pnl),0) FROM trades WHERE execution_status='FILLED'"
        ).fetchone()[0]
        records = journal_db.execute(
            "SELECT id,session_id,timestamp,mode,exchange_a,exchange_b,strategy,path,symbols,"
            "requested_quantity,executed_quantity,average_fill_price,fees,gross_pnl,slippage,net_pnl,"
            "latency_ms,book_age_ms,execution_status,verification_status,risk_decision,failure_reason,"
            "target_before,target_after,cumulative_realized_pnl FROM trades ORDER BY id DESC LIMIT 100"
        ).fetchall()
        return {"trades": [dict(record) for record in records], "realizedNetPnl": float(total),
                "scope": "all durable engine sessions"}
    except sqlite3.OperationalError:
        return {"trades": [], "realizedNetPnl": 0.0, "scope": "all durable engine sessions"}
    finally:
        journal_db.close()


@app.get("/api/v1/engine/events", dependencies=[Depends(_proxy_auth)])
def engine_events(request: Request):
    db = _connect()
    try:
        _current_user(request, db)
    finally:
        db.close()
    events = []
    while True:
        try:
            event = engine_logs.get_nowait()
        except queue.Empty:
            break
        if isinstance(event, (tuple, list)) and len(event) >= 3:
            events.append({"kind": str(event[1]), "message": str(event[2])[:2000]})
        else:
            events.append({"kind": "event", "message": str(event)[:2000]})
    return {"events": events}


@app.post("/api/v1/engine/start", dependencies=[Depends(_proxy_auth)])
async def engine_start(payload: EngineStart, request: Request):
    if not payload.require_private_stream:
        raise HTTPException(422, "The authenticated engine requires a verified private balance stream in both paper and live modes")
    if payload.mode == "live":
        if os.getenv("ARBX_LIVE_TRADING_ENABLED", "0") != "1":
            raise HTTPException(503, "Live execution is disabled by the control-service operator")
        if payload.live_confirmation != "I ACCEPT REAL ORDERS":
            raise HTTPException(422, 'For live mode, type exactly: "I ACCEPT REAL ORDERS"')

    global engine_owner_id, engine_hub, engine_task, engine_phase, engine_error, engine_stop_requested
    async with engine_lock:
        if engine_task is not None and not engine_task.done():
            raise HTTPException(409, "An engine session is already active on this control-service instance")
        db = _connect()
        try:
            uid = _current_user(request, db)
            rows = {r["exchange_id"]: r for r in db.execute(
                "SELECT exchange_id,encrypted_credentials,auth_mode,state,last_verified,verification_json FROM exchange_credentials WHERE user_id=?", (uid,))}
            if payload.mode == "live":
                missing = []
                now = time.time()
                for exchange_id in payload.exchange_ids:
                    row = rows.get(exchange_id)
                    evidence = json.loads(row["verification_json"] or "{}").get("evidence") if row else {}
                    fresh = bool(row and row["last_verified"]) and _verification_is_fresh(row["last_verified"], now=now)
                    if not row or row["state"] != "LIVE_READY" or not fresh or not (
                        evidence.get("scannerEligible") and evidence.get("executionEligible") and evidence.get("liveEligible")
                    ):
                        missing.append(exchange_id)
                if missing:
                    raise HTTPException(409, "Live activation requires every selected venue to be LIVE_READY with fresh scanner, execution, balance, and permission evidence: " + ", ".join(missing))
            credentials_by_id: dict[str, dict[str, str]] = {}
            auth_modes: dict[str, str] = {}
            for exchange_id in payload.exchange_ids:
                row = rows.get(exchange_id)
                if row is None:
                    raise HTTPException(409, f"Verify {VENUES[exchange_id]} before starting an authenticated scan or live mode")
                try:
                    credentials_by_id[exchange_id] = json.loads(_decrypt(row["encrypted_credentials"]).decode())
                except Exception as exc:
                    raise HTTPException(503, "Could not unlock saved exchange credentials") from exc
                auth_modes[exchange_id] = row["auth_mode"]

            from arbx.config import Config, ExchangeCfg
            from arbx.hub import Hub
            exchange_cfgs = []
            for exchange_id in payload.exchange_ids:
                adapter_id = PYTHON_ADAPTERS.get(exchange_id, exchange_id)
                credentials = credentials_by_id[exchange_id]
                if exchange_id == "binance" and auth_modes[exchange_id] in ("rsa", "ed25519"):
                    credentials = {**credentials, "secret": credentials.get("privateKey", "")}
                exchange_cfgs.append(ExchangeCfg(id=adapter_id, venue_id=exchange_id,
                                                 api_key=credentials.get("apiKey", ""),
                                                 secret=credentials.get("secret", ""),
                                                 password=credentials.get("password", ""),
                                                 require_private_stream=True, auth_mode=auth_modes[exchange_id]))
            cfg = Config(mode=payload.mode, exchanges=exchange_cfgs, trade_size_usd=payload.trade_size_usd,
                         max_loss_usd=payload.max_loss_usd, target_profit_usd=payload.target_profit_usd,
                         cross_enabled=payload.mode == "paper" or payload.cross_live,
                         cross_live=payload.mode == "live" and payload.cross_live,
                         journal_path=APP_DB.parent / f"engine_{uid}.csv")
            try:
                cfg.validate()
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            engine_logs.queue.clear()
            engine_owner_id = uid
            engine_error = None
            engine_stop_requested = False
            engine_phase = "PREFLIGHTING" if payload.mode == "live" or payload.require_private_stream else "STARTING"
            engine_hub = Hub(cfg, _EngineOut())
            engine_task = asyncio.create_task(_run_engine_job(payload, credentials_by_id, auth_modes), name="arbx-engine-job")
        finally:
            db.close()
    return {"status": engine_phase, "mode": payload.mode,
            "message": "Preflight is running. No live order is submitted until account, permission, stream, and latency checks pass." if payload.mode == "live" else "Paper scanner starting."}


@app.post("/api/v1/engine/stop", dependencies=[Depends(_proxy_auth)])
def engine_stop(request: Request):
    global engine_phase, engine_stop_requested
    db = _connect()
    try:
        uid = _current_user(request, db)
    finally:
        db.close()
    if engine_owner_id != uid or engine_hub is None or engine_task is None or engine_task.done():
        return {"status": "STOPPED"}
    engine_stop_requested = True
    if engine_phase in ("PREFLIGHTING", "STARTING"):
        engine_phase = "STOPPING"
    engine_hub.request_stop()
    return {"status": "STOPPING"}