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

import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from label_generator import generate_labels_pdf

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

# Optional: set UPDATE_GITHUB_REPO in .env (e.g. "yourname/shopify-sku-tool")
# to enable the "check for updates" banner. Leave unset to disable it entirely.
UPDATE_GITHUB_REPO = os.environ.get("UPDATE_GITHUB_REPO", "").strip()


def _read_app_version() -> str:
    try:
        return resource_path("VERSION").read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return "dev"


APP_VERSION = _read_app_version()

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


@app.get("/favicon.ico")
async def favicon():
    return FileResponse(STATIC_DIR / "favicon.ico", media_type="image/x-icon")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


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
    return {
        "ok": not missing,
        "missing": missing,
        "env_path": str(ENV_PATH),
        "shop_domain": SHOP_DOMAIN,
        "version": APP_VERSION,
    }


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


@app.post("/api/labels/pdf")
async def labels_pdf(request: Request):
    body = await request.json()
    items = body.get("items", [])
    if not items:
        raise HTTPException(400, "No items provided")
    if not isinstance(items, list) or len(items) > 500:
        raise HTTPException(400, "Provide a list of up to 500 {barcode, name} items")

    pdf_bytes = generate_labels_pdf(items)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=labels.pdf"},
    )


def _parse_version(v: str):
    try:
        return tuple(int(p) for p in v.strip().lstrip("vV").split("."))
    except (ValueError, AttributeError):
        return None


def _is_newer(latest: str, current: str) -> bool:
    lt, ct = _parse_version(latest), _parse_version(current)
    if lt is None or ct is None:
        return latest != current  # can't parse — treat any difference as "there's an update"
    return lt > ct


async def _fetch_latest_release() -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"https://api.github.com/repos/{UPDATE_GITHUB_REPO}/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "linda-sku-editor-updater"},
        )
    if resp.status_code != 200:
        raise HTTPException(502, f"GitHub returned {resp.status_code} checking for updates")
    return resp.json()


@app.get("/api/update-status")
async def update_status():
    """Checks GitHub's latest release for this repo. Disabled entirely
    unless UPDATE_GITHUB_REPO is set in .env — never phones home otherwise."""
    if not UPDATE_GITHUB_REPO:
        return {"enabled": False}
    try:
        data = await _fetch_latest_release()
    except HTTPException as exc:
        return {"enabled": True, "current": APP_VERSION, "error": exc.detail}
    except httpx.RequestError as exc:
        return {"enabled": True, "current": APP_VERSION, "error": str(exc)}

    latest_version = (data.get("tag_name") or "").strip().lstrip("vV")
    asset = next((a for a in data.get("assets", []) if a["name"].lower().endswith(".exe")), None)
    return {
        "enabled": True,
        "current": APP_VERSION,
        "latest": latest_version or None,
        "update_available": bool(latest_version) and _is_newer(latest_version, APP_VERSION),
        "download_url": asset["browser_download_url"] if asset else None,
        "release_url": data.get("html_url"),
        "is_packaged": getattr(sys, "frozen", False),
    }


@app.post("/api/self-update")
async def self_update():
    """Downloads the latest .exe and swaps it in via a tiny detached batch
    script, then restarts. Only works for the packaged .exe — running from
    source, use `git pull` instead."""
    if not getattr(sys, "frozen", False):
        raise HTTPException(400, "Auto-update only works in the packaged .exe. Use 'git pull' when running from source.")
    if not UPDATE_GITHUB_REPO:
        raise HTTPException(400, "UPDATE_GITHUB_REPO is not set in .env")

    data = await _fetch_latest_release()
    asset = next((a for a in data.get("assets", []) if a["name"].lower().endswith(".exe")), None)
    if not asset:
        raise HTTPException(502, "The latest GitHub release has no .exe file attached")

    exe_path = Path(sys.executable)
    new_path = exe_path.with_name(exe_path.stem + ".new.exe")
    log_path = exe_path.with_name("update_log.txt")

    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        download = await client.get(asset["browser_download_url"])
    if download.status_code != 200:
        raise HTTPException(502, f"Download failed ({download.status_code})")
    new_path.write_bytes(download.content)

    # A short batch script does the actual swap after this process exits —
    # Windows won't let a running .exe delete/overwrite itself directly.
    #
    # This does NOT just sleep a fixed couple of seconds and hope for the
    # best: PyInstaller onefile .exes unpack to a temp dir on launch and
    # have to tear that down on exit, which can take longer than a couple
    # of seconds (more so if antivirus is scanning). So instead it polls
    # for this exact process's PID to actually disappear from `tasklist`,
    # then retries the delete/move a few times in case something still has
    # a momentary lock on the file, logging each step to update_log.txt so
    # a failure is diagnosable afterwards instead of just a dead browser tab.
    my_pid = os.getpid()
    bat_path = exe_path.with_name("_update.bat")
    bat_path.write_text(
        "@echo off\r\n"
        f'cd /d "{exe_path.parent}"\r\n'
        f'echo [%date% %time%] Update started, waiting on PID {my_pid} to exit > "{log_path}"\r\n'
        "\r\n"
        ":waitexit\r\n"
        f'tasklist /fi "PID eq {my_pid}" 2>nul | find "{my_pid}" >nul\r\n'
        "if not errorlevel 1 (\r\n"
        "    timeout /t 1 /nobreak >nul\r\n"
        "    goto waitexit\r\n"
        ")\r\n"
        f'echo [%date% %time%] Old process exited, replacing exe >> "{log_path}"\r\n'
        "\r\n"
        "set DELRETRIES=0\r\n"
        ":retrydel\r\n"
        f'del /f /q "{exe_path}" 2>nul\r\n'
        f'if exist "{exe_path}" (\r\n'
        "    set /a DELRETRIES+=1\r\n"
        f'    echo [%date% %time%] Delete attempt %DELRETRIES% failed, retrying >> "{log_path}"\r\n'
        "    if %DELRETRIES% GEQ 15 goto updatefailed\r\n"
        "    timeout /t 1 /nobreak >nul\r\n"
        "    goto retrydel\r\n"
        ")\r\n"
        "\r\n"
        f'move /y "{new_path}" "{exe_path}" >nul\r\n'
        f'if not exist "{exe_path}" goto updatefailed\r\n'
        f'echo [%date% %time%] Swap complete, launching new exe >> "{log_path}"\r\n'
        f'start "" "{exe_path}"\r\n'
        f'del "%~f0"\r\n'
        "exit\r\n"
        "\r\n"
        ":updatefailed\r\n"
        f'echo [%date% %time%] Update FAILED — could not replace the exe. >> "{log_path}"\r\n'
        f'echo The old exe may be missing. If so, rename "{new_path.name}" to "{exe_path.name}" by hand. >> "{log_path}"\r\n'
        f'if exist "{new_path}" start "" "{new_path}"\r\n'
    )

    # CREATE_NO_WINDOW (not DETACHED_PROCESS): the batch script pipes console
    # commands together (tasklist | find), and DETACHED_PROCESS gives cmd no
    # console at all — each piped console program then has nothing to
    # inherit and spawns its own orphan console window instead of piping
    # cleanly, which is exactly the stuck "find" window this was causing.
    # CREATE_NO_WINDOW gives cmd a real console, just a hidden one, so the
    # pipe negotiates normally. The two flags are mutually exclusive.
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    startupinfo = None
    startupinfo_cls = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_cls is not None:
        startupinfo = startupinfo_cls()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)

    subprocess.Popen(
        ["cmd", "/c", str(bat_path)],
        creationflags=no_window | new_group,
        startupinfo=startupinfo,
        close_fds=True,
    )

    def _shutdown_soon():
        time.sleep(0.5)
        os._exit(0)

    threading.Thread(target=_shutdown_soon, daemon=True).start()
    return {"status": "updating", "message": "Update downloaded — restarting to install it."}


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
