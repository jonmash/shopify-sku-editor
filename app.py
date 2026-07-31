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
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI(title="Linda's Natural Remedies — SKU Editor")

SHOPIFY_API_VERSION = "2026-07"
HOST = "127.0.0.1"
PORT = 8787


def resource_path(relative: str) -> Path:
    """Find bundled files whether running from source or as a frozen .exe."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / relative


STATIC_DIR = resource_path("static")


@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.post("/graphql")
async def graphql_proxy(request: Request):
    shop_domain = request.headers.get("x-shopify-shop-domain", "").strip()
    token = request.headers.get("x-shopify-access-token", "").strip()

    if not shop_domain or not token:
        raise HTTPException(400, "Missing shop domain or access token")
    if not shop_domain.endswith(".myshopify.com"):
        raise HTTPException(400, "Shop domain must end in .myshopify.com")

    body = await request.body()
    url = f"https://{shop_domain}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(
                url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Shopify-Access-Token": token,
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
