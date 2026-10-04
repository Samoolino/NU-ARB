"""Loopback-only adapter for serving the browser UI with the local control API."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qs

from starlette.responses import HTMLResponse, JSONResponse

from arbx.web_api import app as control_app

INDEX_FILE = Path(__file__).resolve().parents[2] / "public" / "index.html"


class LocalHost:
    def __init__(self, api_app):
        self.api_app = api_app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.api_app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET")
        if path == "/" and method == "GET":
            await HTMLResponse(
                INDEX_FILE.read_text(encoding="utf-8"),
                headers={
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                    "X-Frame-Options": "DENY",
                    "Referrer-Policy": "no-referrer",
                },
            )(scope, receive, send)
            return

        if path == "/api/scan":
            if method != "GET":
                await JSONResponse(
                    {"error": "Method not allowed"},
                    status_code=405,
                    headers={"Allow": "GET", "Cache-Control": "no-store"},
                )(scope, receive, send)
                return
            if len(scope.get("query_string", b"")) > 2048:
                await JSONResponse(
                    {"error": "Scan query is too large."},
                    status_code=400,
                    headers={"Cache-Control": "no-store"},
                )(scope, receive, send)
                return
            from arbx.public_scanner import scan_public_spot
            from arbx.web_api import VENUES

            query = parse_qs(scope.get("query_string", b"").decode("ascii", errors="ignore"))
            venues = query.get("venues", [""])[0].split(",")
            symbols = query.get("symbols", [""])[0].split(",")
            try:
                if any(venue not in VENUES for venue in venues):
                    raise ValueError("Select registered exchange venues.")
                notional = float(query.get("notionalUsd", ["100"])[0])
                taker_fee_bps = float(query.get("takerFeeBps", ["10"])[0])
                min_net_bps = float(query.get("minNetBps", ["5"])[0])
                result = await scan_public_spot(
                    venues,
                    symbols,
                    notional_usd=notional,
                    taker_fee_bps=taker_fee_bps,
                    min_net_bps=min_net_bps,
                )
                await JSONResponse(
                    result,
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
                )(scope, receive, send)
            except (TypeError, ValueError) as exc:
                await JSONResponse(
                    {"error": str(exc)},
                    status_code=400,
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
                )(scope, receive, send)
            return

        if path != "/api/control":
            await self.api_app(scope, receive, send)
            return

        if method not in {"GET", "POST", "DELETE"}:
            await JSONResponse(
                {"error": "Method not allowed"},
                status_code=405,
                headers={"Allow": "GET, POST, DELETE", "Cache-Control": "no-store"},
            )(scope, receive, send)
            return

        query = parse_qs(scope.get("query_string", b"").decode("ascii", errors="ignore"))
        route = query.get("path", [""])[0]
        if not route.startswith("/api/v1/") or ".." in route or "\\" in route:
            await JSONResponse(
                {"error": "Invalid control API path"},
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )(scope, receive, send)
            return

        token = os.getenv("ENGINE_PROXY_TOKEN", "")
        if not token:
            await JSONResponse(
                {"error": "Local control proxy is not configured"},
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )(scope, receive, send)
            return

        forwarded_scope = dict(scope)
        forwarded_scope["path"] = route
        forwarded_scope["raw_path"] = route.encode("ascii")
        forwarded_scope["query_string"] = b""
        forwarded_scope["headers"] = [
            (name, value)
            for name, value in scope.get("headers", [])
            if name.lower() != b"x-engine-token"
        ] + [
            (b"x-engine-token", token.encode("ascii"))
        ]
        await self.api_app(forwarded_scope, receive, send)


app = LocalHost(control_app)
