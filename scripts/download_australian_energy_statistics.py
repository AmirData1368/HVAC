from __future__ import annotations

import hashlib
import json
import re
import time
import zipfile
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

PAGE = "https://www.energy.gov.au/publications/australian-energy-update-2025"
OUT = Path("model_ready_inputs/reference/australian_energy_statistics_2025")
OUT.mkdir(parents=True, exist_ok=True)
TARGETS = {
    "table_b": re.compile(r"Table B:.*population.*state and territory", re.I),
    "table_f": re.compile(r"Table F:.*industry and by fuel", re.I),
    "table_l": re.compile(r"Table L:.*consumption of electricity", re.I),
}
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0", "Accept-Language": "en-AU,en;q=0.9"})


def discover_requests() -> dict[str, str]:
    response = SESSION.get(PAGE, timeout=120)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    found = {}
    for anchor in soup.find_all("a", href=True):
        text = " ".join(anchor.get_text(" ", strip=True).split())
        href = urljoin(PAGE, anchor["href"])
        for key, pattern in TARGETS.items():
            if key not in found and pattern.search(f"{text} {href}"):
                found[key] = href
    return found


def discover_browser() -> dict[str, str]:
    from playwright.sync_api import sync_playwright

    found = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page(locale="en-AU")
        page.goto(PAGE, wait_until="domcontentloaded", timeout=240_000)
        page.wait_for_timeout(4_000)
        for index in range(page.locator("a").count()):
            anchor = page.locator("a").nth(index)
            text = " ".join((anchor.inner_text(timeout=3_000) or "").split())
            href = urljoin(page.url, anchor.get_attribute("href") or "")
            for key, pattern in TARGETS.items():
                if key not in found and pattern.search(f"{text} {href}"):
                    found[key] = href
        browser.close()
    return found


def download(key: str, url: str) -> Path:
    path = OUT / f"AES_2025_{key.upper()}.xlsx"
    last = None
    for attempt in range(8):
        try:
            response = SESSION.get(url, headers={"Referer": PAGE}, timeout=240, allow_redirects=True)
            response.raise_for_status()
            if len(response.content) < 20_000:
                raise RuntimeError(f"download too small: {len(response.content)} bytes")
            path.write_bytes(response.content)
            if not zipfile.is_zipfile(path):
                raise RuntimeError("attachment is not a valid XLSX archive")
            return path
        except Exception as exc:
            last = exc
            time.sleep(min(30, 2 ** attempt))
    raise RuntimeError(f"{key} download failed: {last!r}")


def main() -> None:
    found = discover_requests()
    if set(found) != set(TARGETS):
        found.update(discover_browser())
    missing = set(TARGETS) - set(found)
    if missing:
        raise RuntimeError(f"Could not discover official attachments: {sorted(missing)}; found={found}")

    report = {"status": "PASS", "publication_page": PAGE, "files": []}
    for key in TARGETS:
        path = download(key, found[key])
        excel = pd.ExcelFile(path)
        previews = {}
        for sheet in excel.sheet_names:
            frame = pd.read_excel(path, sheet_name=sheet, header=None, nrows=25)
            previews[sheet] = frame.fillna("").astype(str).values.tolist()
        report["files"].append({
            "key": key,
            "path": str(path),
            "source_url": found[key],
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "sheets": excel.sheet_names,
            "preview_first_25_rows": previews,
        })
    (OUT / "AES_2025_download_audit.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "publication_page": PAGE,
        "files": [{k: v for k, v in item.items() if k != "preview_first_25_rows"} for item in report["files"]],
    }, indent=2))


if __name__ == "__main__":
    main()
