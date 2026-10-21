"""Fail-closed BNB Smart Chain BTCB route verifier.

Verification-only adapter. It does not sign or submit transactions. A live route cannot
become eligible until a separately reviewed manifest digest is pinned in this module.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

import aiohttp


# Deliberately unset: no BTCB/router/pool addresses have yet been independently pinned
# in this repository. Do not replace with values copied from token tickers or untrusted pages.
PINNED_MANIFEST_SHA256: str | None = None
BSC_MAINNET_CHAIN_ID = 56


@dataclass(frozen=True, slots=True)
class BTCBRouteManifest:
    chain_id: int
    btcb_token: str
    quote_token: str
    router: str
    factory: str
    pool: str
    expected_decimals: int
    expected_symbol: str = "BTCB"

    def canonical_sha256(self) -> str:
        raw = json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class BTCBVerification:
    live_eligible: bool
    reasons: tuple[str, ...]
    evidence: dict


class BSCBTCBAdapter:
    """RPC verifier only; never sends transactions or treats a ticker as token identity."""

    def __init__(self, rpc_url: str, manifest: BTCBRouteManifest | None):
        self.rpc_url = rpc_url
        self.manifest = manifest

    async def _rpc(self, session, method, params):
        async with session.post(self.rpc_url, json={"jsonrpc":"2.0","id":1,"method":method,"params":params}) as response:
            response.raise_for_status()
            payload = await response.json()
        if payload.get("error") or "result" not in payload:
            raise RuntimeError(f"RPC {method} failed")
        return payload["result"]

    @staticmethod
    def _address_ok(value):
        return isinstance(value, str) and len(value) == 42 and value.startswith("0x") and all(c in "0123456789abcdefABCDEF" for c in value[2:])

    async def verify(self, *, simulation_ok: bool = False) -> BTCBVerification:
        reasons = []
        evidence = {"adapter":"bsc_btcb_verification_only","transaction_submission":False}
        m = self.manifest
        if not self.rpc_url:
            return BTCBVerification(False, ("rpc_url_missing",), evidence)
        if m is None:
            return BTCBVerification(False, ("route_manifest_missing","trusted_manifest_not_pinned"), evidence)
        addresses = {"btcb_token":m.btcb_token,"quote_token":m.quote_token,"router":m.router,"factory":m.factory,"pool":m.pool}
        invalid = [key for key,value in addresses.items() if not self._address_ok(value)]
        if invalid:
            return BTCBVerification(False, tuple(f"invalid_address:{key}" for key in invalid), evidence)
        if m.chain_id != BSC_MAINNET_CHAIN_ID:
            reasons.append("wrong_manifest_chain_id")
        if PINNED_MANIFEST_SHA256 is None:
            reasons.append("trusted_manifest_not_pinned")
        elif m.canonical_sha256().lower() != PINNED_MANIFEST_SHA256.lower():
            reasons.append("manifest_digest_mismatch")
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                chain_hex = await self._rpc(session,"eth_chainId",[])
                chain_id = int(chain_hex,16)
                evidence["chain_id"] = chain_id
                if chain_id != BSC_MAINNET_CHAIN_ID:
                    reasons.append("rpc_wrong_chain")
                code = {}
                for key,address in addresses.items():
                    bytecode = await self._rpc(session,"eth_getCode",[address,"latest"])
                    code[key] = bool(bytecode and bytecode != "0x")
                evidence["contract_code_present"] = code
                if not all(code.values()):
                    reasons.append("contract_code_missing")
                decimals_hex = await self._rpc(session,"eth_call",[{"to":m.btcb_token,"data":"0x313ce567"},"latest"])
                decimals = int(decimals_hex,16)
                evidence["token_decimals"] = decimals
                if decimals != m.expected_decimals:
                    reasons.append("token_decimals_mismatch")
                symbol_hex = await self._rpc(session,"eth_call",[{"to":m.btcb_token,"data":"0x95d89b41"},"latest"])
                raw = bytes.fromhex(symbol_hex[2:])
                symbol_bytes = raw.rstrip(b"\\x00")
                if len(raw) >= 64:
                    offset = int.from_bytes(raw[:32], "big")
                    if 0 <= offset <= len(raw) - 32:
                        length = int.from_bytes(raw[offset:offset + 32], "big")
                        if 0 <= length <= len(raw) - offset - 32:
                            symbol_bytes = raw[offset + 32:offset + 32 + length]
                symbol = symbol_bytes.decode("utf-8", errors="replace").strip()
                evidence["token_symbol"] = symbol
                if symbol != m.expected_symbol:
                    reasons.append("token_symbol_mismatch")
        except Exception as exc:
            reasons.append(f"rpc_verification_failed:{type(exc).__name__}")
        if not simulation_ok:
            reasons.append("transaction_simulation_not_verified")
        evidence["manifest_sha256"] = m.canonical_sha256()
        evidence["simulation_ok"] = bool(simulation_ok)
        # A successful RPC read alone is not identity proof; independent manifest pinning
        # and a separately executed simulation are both mandatory.
        return BTCBVerification(not reasons, tuple(dict.fromkeys(reasons)), evidence)
