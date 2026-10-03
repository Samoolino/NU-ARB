"""Persistent control-plane API for the Vercel UI. Run only behind HTTPS on a persistent host."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator


APP_DB = Path(os.getenv("ARBX_APP_DB", "arbx_app.sqlite3"))
SESSION_COOKIE = "arbx_session"
SESSION_TTL = 60 * 60 * 24 * 7
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
PYTHON_ADAPTERS = {"gateio": "gate"}
app = FastAPI(title="ARBX Control API", version="1.0.0")


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
    model_config = ConfigDict(extra="forbid")
    credentials: dict[str, str] = Field(min_length=1, max_length=8)
    auth_mode: str = "ccxt"
    symbol: str = Field(default="BTC/USDT", pattern=r"^[A-Z0-9]{2,20}/[A-Z0-9]{2,20}$", max_length=41)

    @field_validator("credentials")
    @classmethod
    def bound_credential_payload(cls, credentials):
        if any(len(key) > 32 or len(value) > 16_384 for key, value in credentials.items()):
            raise ValueError("Credential fields exceed their maximum length")
        return credentials


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
    return {"service": "arbx-control-api", "status": "available", "execution_enabled": False}


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
            result.append({"id": exchange_id, "name": name, "adapterAvailable": bool(cls),
                           "authenticationModes": schema, "state": row["state"] if row else "NOT_CONFIGURED",
                           "lastVerified": row["last_verified"] if row else None,
                           "evidence": saved.get("evidence", {}), "balances": saved.get("balances"),
                           "orderBook": saved.get("orderBook"), "maskedKey": saved.get("maskedKey"),
                           "scannerEligible": False, "liveEligible": False})
        return {"exchanges": result}
    finally:
        db.close()


@app.post("/api/v1/exchanges/{exchange_id}/verify", dependencies=[Depends(_proxy_auth)])
async def verify_exchange(exchange_id: str, payload: ExchangeConnect, request: Request):
    if exchange_id not in VENUES:
        raise HTTPException(404, "Exchange is not in the supported venue registry")
    uid = None; db = _connect()
    try:
        uid = _current_user(request, db)
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
            import ccxt.pro as ccxtpro
            adapter_id = PYTHON_ADAPTERS.get(exchange_id, exchange_id)
            cls = getattr(ccxtpro, adapter_id, None)
            if cls is None:
                raise HTTPException(501, "This venue has no CCXT Pro adapter in the installed runtime")
            ccxt_credentials = dict(payload.credentials)
            if exchange_id == "binance" and payload.auth_mode in ("rsa", "ed25519"):
                # CCXT's Binance signer reads its private key from `secret`; keep the UI schema truthful.
                ccxt_credentials["secret"] = ccxt_credentials.pop("privateKey")
            exchange = cls({**ccxt_credentials, "enableRateLimit": True, "timeout": 10000,
                           "options": {"defaultType": "spot"}})
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(500, f"Could not initialize adapter: {type(exc).__name__}") from exc
        evidence = {"rest": False, "authentication": False, "account": False, "balances": False,
                    "publicWebSocket": False, "privateWebSocket": False, "tradePermission": "unverified",
                    "withdrawalsDisabled": "unverified", "scannerEligible": False, "liveEligible": False}
        balance_summary = None; book_summary = None; state = "VERIFYING"; verified_at = None
        try:
            t0 = time.perf_counter(); await exchange.load_markets();
            if not exchange.has.fetchTime:
                raise RuntimeError("adapter does not expose a server-time request")
            server_time = await exchange.fetch_time()
            evidence["rest"] = bool(server_time); evidence["restRttMs"] = round((time.perf_counter()-t0)*1000, 1)
            market = exchange.market(payload.symbol)
            if not market.get("spot") or market.get("contract"):
                raise RuntimeError("verification symbol is not a spot market")
            if not exchange.apiKey:
                state = "MARKET_DATA_CONNECTED"
            else:
                balance = await exchange.fetch_balance()
                evidence["authentication"] = True; evidence["account"] = True; evidence["balances"] = True
                balance_summary = {asset: {"free": float((balance.get("free") or {}).get(asset) or 0),
                                            "used": float((balance.get("used") or {}).get(asset) or 0),
                                            "total": float((balance.get("total") or {}).get(asset) or 0)}
                                   for asset in (balance.get("total") or {}) if float((balance.get("total") or {}).get(asset) or 0) > 0}
                state = "ACCOUNT_DATA_CONNECTED"
            if not exchange.has.watchOrderBook:
                raise RuntimeError("adapter does not expose a WebSocket order-book method")
            book = await asyncio.wait_for(exchange.watch_order_book(payload.symbol, 10), timeout=12)
            if not book.get("bids") or not book.get("asks"):
                raise RuntimeError("WebSocket returned an empty order book")
            evidence["publicWebSocket"] = True
            book_summary = {"symbol": payload.symbol, "bestBid": book["bids"][0][0], "bestAsk": book["asks"][0][0],
                            "receivedAt": datetime.now(timezone.utc).isoformat(), "sequence": book.get("nonce")}
            evidence["orderBook"] = book_summary; evidence["privateWebSocket"] = False
            evidence["reasonNotFullyVerified"] = "The installed adapter did not prove private-stream health, trade permission, and withdrawal restriction."
            state = "ACCOUNT_DATA_CONNECTED" if evidence["balances"] else "MARKET_DATA_CONNECTED"
            verified_at = datetime.now(timezone.utc).isoformat()
        except Exception as exc:
            state = "FAILED"; evidence["error"] = type(exc).__name__
        finally:
            try: await exchange.close()
            except Exception: pass
        key_preview = payload.credentials.get("apiKey", "")
        masked = f"{key_preview[:3]}••••{key_preview[-3:]}" if len(key_preview) >= 7 else "••••"
        encrypted = _encrypt(json.dumps(payload.credentials, separators=(",", ":")).encode())
        saved_verification = {"evidence": evidence, "balances": balance_summary,
                              "orderBook": book_summary, "maskedKey": masked}
        db.execute("""INSERT INTO exchange_credentials(user_id,exchange_id,encrypted_credentials,auth_mode,state,last_verified,verification_json)
                      VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id,exchange_id) DO UPDATE SET
                      encrypted_credentials=excluded.encrypted_credentials,auth_mode=excluded.auth_mode,state=excluded.state,
                      last_verified=excluded.last_verified,verification_json=excluded.verification_json""",
                   (uid, exchange_id, encrypted, payload.auth_mode, state, verified_at,
                    json.dumps(saved_verification, separators=(",", ":"))))
        db.commit()
        return {"id": exchange_id, "name": VENUES[exchange_id], "state": state, "maskedKey": masked,
                "verifiedAt": verified_at, "evidence": evidence, "balances": balance_summary,
                "orderBook": book_summary, "scannerEligible": False, "liveEligible": False,
                "executionEnabled": False}
    finally:
        db.close()


@app.delete("/api/v1/exchanges/{exchange_id}", dependencies=[Depends(_proxy_auth)])
def remove_exchange(exchange_id: str, request: Request):
    db = _connect()
    try:
        uid = _current_user(request, db)
        db.execute("DELETE FROM exchange_credentials WHERE user_id=? AND exchange_id=?", (uid, exchange_id)); db.commit()
        return {"id": exchange_id, "state": "NOT_CONFIGURED"}
    finally:
        db.close()

