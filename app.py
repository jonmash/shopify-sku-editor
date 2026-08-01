"""
Shopify SKU / Barcode Editor — FastAPI backend.

Serves the single-page UI and proxies GraphQL calls to Shopify's Admin API.
A backend is required (not a bare static HTML file) because Shopify's Admin
API does not send CORS headers and will reject direct browser requests.
Since this app serves the page AND proxies the API from the same origin,
there's no CORS configuration to worry about at all.

Run:
    pip install -r requirements.txt
    uvicorn app:app --reload --port 8787
Then open http://localhost:8787
"""

import sys
import threading
import webbrowser
import sys
import threading
import time
import webbrowser
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
import os

app = FastAPI(title="Linda's Natural Remedies — SKU Editor")

SHOPIFY_API_VERSION = "2026-07"
HOST = "127.0.0.1"
PORT = 8787


def resource_path(relative: str) -> Path:
    """Find bundled files (like static/) whether running from source or as a frozen .exe."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / relative


def config_dir() -> Path:
    """Where the .env lives: next to the .exe when frozen, next to app.py otherwise.
    This is deliberately NOT the same as resource_path — .env must stay editable
    and outside the bundled/read-only PyInstaller archive."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


STATIC_DIR = resource_path("static")
ENV_PATH = config_dir() / ".env"
load_dotenv(ENV_PATH)

SHOP_DOMAIN = os.environ.get("SHOPIFY_SHOP_DOMAIN", "").strip()
CLIENT_ID = os.environ.get("SHOPIFY_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("SHOPIFY_CLIENT_SECRET", "").strip()

# In-memory cache of the short-lived access token obtained via the client
# credentials grant. Shopify no longer issues permanent tokens for custom
# apps created in the Dev Dashboard (since Jan 1, 2026) — instead you trade
# a Client ID + Client Secret for a token that expires roughly every 24h.
_token_cache: dict = {}


async def get_access_token() -> str:
    if not (SHOP_DOMAIN and CLIENT_ID and CLIENT_SECRET):
        raise HTTPException(
            500,
            f"Missing credentials in {ENV_PATH}. "
            "Set SHOPIFY_SHOP_DOMAIN, SHOPIFY_CLIENT_ID, and SHOPIFY_CLIENT_SECRET.",
        )

    cached = _token_cache.get("token")
    if cached and _token_cache["expires_at"] > time.time() + 60:
        return cached

    url = f"https://{SHOP_DOMAIN}/admin/oauth/access_token"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            url,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            data={
                "grant_type": "client_credentials",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            },
        )
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, f"Token exchange failed: {resp.text[:300]}")

    payload = resp.json()
    access_token = payload.get("access_token")
    expires_in = payload.get("expires_in", 3600)
    if not access_token:
        raise HTTPException(502, f"No access_token in Shopify's response: {payload}")

    _token_cache["token"] = access_token
    _token_cache["expires_at"] = time.time() + expires_in
    return access_token


@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/config-status")
async def config_status():
    """Lets the frontend show a clear message if .env isn't filled in yet,
    without ever exposing the secret itself."""
    missing = [
        name
        for name, val in [
            ("SHOPIFY_SHOP_DOMAIN", SHOP_DOMAIN),
            ("SHOPIFY_CLIENT_ID", CLIENT_ID),
            ("SHOPIFY_CLIENT_SECRET", CLIENT_SECRET),
        ]
        if not val
    ]
    return {"ok": not missing, "missing": missing, "env_path": str(ENV_PATH), "shop_domain": SHOP_DOMAIN}


@app.post("/graphql")
async def graphql_proxy(request: Request):
    access_token = await get_access_token()

    body = await request.body()
    url = f"https://{SHOP_DOMAIN}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(
                url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Shopify-Access-Token": access_token,
                },
            )
        except httpx.RequestError as exc:
            raise HTTPException(502, f"Could not reach Shopify: {exc}") from exc

    # Pass Shopify's response straight through, whatever it is
    try:
        payload = resp.json()
    except ValueError:
        raise HTTPException(resp.status_code, "Shopify returned a non-JSON response")

    return JSONResponse(content=payload, status_code=resp.status_code)


def _open_browser_soon():
    threading.Timer(1.2, lambda: webbrowser.open(f"http://{HOST}:{PORT}/")).start()


if __name__ == "__main__":
    # This branch only runs when double-clicked as the packaged .exe
    # (or via `python app.py`) — not when launched with `uvicorn app:app`.
    import uvicorn

    print("Linda's Natural Remedies — SKU Editor")
    print("Starting up... your browser will open automatically.")
    print("Leave this window open while you use the tool.")
    print("Close this window when you're done.\n")

    _open_browser_soon()
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
