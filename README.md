# Barcode Editor — Linda's Natural Remedies

A small local tool for bulk-editing Shopify product **barcodes** and printing
matching barcode label sheets, without paying for Matrixify.

Shopify's built-in bulk editor doesn't expose the barcode field, so this
exists to fill that gap: a simple web page (running on your own computer)
that lists every product/variant, lets you edit barcodes inline, and
generates print-ready label sheets from them.

## What it does

- **Lists all products and variants** from your Shopify store, grouped by product.
- **Edit barcodes inline** — changed fields highlight yellow until saved.
- **Auto-assign barcodes** — click "Auto" on any blank barcode field to fill
  in the next unused code in the `LNR001`–`LNR999` format.
- **Duplicate warning** — if two rows end up with the same barcode (even
  before saving), both are highlighted in orange so you can catch it. It
  won't block saving, it's just a heads-up.
- **Search / Refresh** — filter by product title or barcode; Refresh reloads
  everything fresh from Shopify.
- **Print labels** — click the printer icon on any row to fill a full label
  sheet (36 labels) with that product's barcode as a DataMatrix code plus a
  short human-readable name underneath. Matches the exact layout of Linda's
  existing pre-cut 4"×6" Avery label sheets — same grid, same positions.

## Requirements

- Python 3.10+
- A Shopify custom app (see [Setting up Shopify access](#setting-up-shopify-access) below)

## Setup

1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
2. Copy `.env.example` to `.env` (same folder) and fill in your real values:
   ```
   SHOPIFY_SHOP_DOMAIN=your-store.myshopify.com
   SHOPIFY_CLIENT_ID=your_client_id
   SHOPIFY_CLIENT_SECRET=your_client_secret
   ```
   `.env` is gitignored — your credentials never get committed.
3. Run it:
   ```
   uvicorn app:app --reload --port 8787
   ```
4. Open **http://localhost:8787** in your browser.

The page will show "Checking connection…" and then either connect
automatically or tell you exactly which `.env` value is missing.

## Setting up Shopify access

Shopify no longer shows a copyable Admin API token in the dashboard — instead
you get a Client ID + Client Secret, which this app exchanges for a
short-lived access token automatically (and refreshes before it expires, so
you never have to think about it).

1. Shopify admin → **Settings → Apps and sales channels → Develop apps** →
   **Build apps in Dev Dashboard**.
2. **Create an app**. When asked for an App URL, use:
   `https://shopify.dev/apps/default-app-home` (this app has no OAuth
   redirect flow, so it doesn't matter what's here).
3. Under **Access** / **Configuration**, add these Admin API scopes:
   - `read_products`
   - `write_products`
4. **Release** the app, then **Install** it on your store.
5. Once installed, the **Credentials** page shows your **Client ID** and
   **Client secret** (click reveal/rotate if needed) — copy both into `.env`.
6. Your **shop domain** is the `*.myshopify.com` one — find it at
   Settings → Domains, or in your admin URL:
   `https://admin.shopify.com/store/<this-part>` → `<this-part>.myshopify.com`

## Running it as a Windows .exe (no Python required)

A GitHub Actions workflow (`.github/workflows/build-windows.yml`) builds a
standalone `.exe` automatically — push this repo to GitHub, check the
**Actions** tab, and download `LindaSKUEditor.exe` from the workflow run's
artifacts.

To use the exe:
1. Put `LindaSKUEditor.exe` and a filled-in `.env` file in the same folder.
2. Double-click the exe. A small console window opens and your browser
   launches automatically to the tool.
3. Close the console window when you're done.

Windows will likely show a "Windows protected your PC" warning the first
time (the exe isn't code-signed) — click "More info" → "Run anyway." It only
asks once per machine.

## Auto-updating

The app can check GitHub for newer releases and update itself with one
click — no manual downloading or file-copying.

**To enable it:** add to `.env`:
```
UPDATE_GITHUB_REPO=yourname/your-repo-name
```
Leave it unset and the feature stays completely off (no network calls to
GitHub at all).

**To publish a new version:**
1. Bump the version in the `VERSION` file (e.g. `1.1.0`) and commit it.
2. Tag and push:
   ```
   git tag v1.1.0
   git push --tags
   ```
3. GitHub Actions builds the `.exe` and publishes it as a GitHub Release
   automatically (see `.github/workflows/build-windows.yml`).

**What the user sees:** next time the app starts, if the Release's version
is newer than the running one, an amber banner appears with an "Update now"
button. Clicking it downloads the new `.exe`, swaps it in, and restarts —
takes a few seconds, no manual steps.

**How the swap works, and its limits:**
- Only works for the packaged `.exe` — running from source (`python app.py`)
  shows a "Download from GitHub" link instead, since there's no single file
  to swap.
- A running `.exe` can't overwrite itself directly on Windows, so the app
  downloads the new version, writes a tiny batch script that waits a moment,
  deletes the old exe, renames the new one into place, and relaunches it —
  then the app exits so the batch script can finish the swap.
- This mechanism was written from well-established Windows patterns but
  **hasn't been tested on an actual Windows machine yet** — worth doing one
  real update run yourself before relying on it unattended. If it ever
  fails partway through, worst case is `LindaSKUEditor.exe` is missing and
  `LindaSKUEditor.new.exe` sits next to it — just rename the `.new.exe` by
  hand to recover.
- The update check itself (not the install) runs on every startup and fails
  silently if GitHub is unreachable — it never blocks normal use.

## Project structure

```
app.py                       FastAPI backend — serves the page, proxies
                              Shopify API calls, generates label PDFs,
                              handles update checks
label_generator.py           Builds the label sheet PDF (DataMatrix + text)
static/index.html            The whole UI (Tailwind, vanilla JS)
requirements.txt             Python dependencies
VERSION                      This build's version number (bump + tag to release)
.env.example                 Template for your local .env (copy, don't edit)
.gitignore                   Keeps .env and build artifacts out of git
.github/workflows/           GitHub Actions job that builds the Windows exe
                              and publishes tagged releases
```

## Notes & limitations

- Credentials live in a plain-text `.env` file next to the app — fine for a
  single trusted computer, but don't share the file or commit it.
- Access tokens are cached in memory and refresh automatically every ~24
  hours (Shopify's limit, not this app's).
- The label sheet layout is measured to match a specific existing Avery
  4"×6" (4-column × 9-row) sheet. If you switch label stock, the positions
  in `label_generator.py` will need updating to match the new sheet.
