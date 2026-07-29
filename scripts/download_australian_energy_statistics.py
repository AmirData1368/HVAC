from __future__ import annotations

import hashlib
import json
import subprocess
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

PAGE = "https://www.energy.gov.au/publications/australian-energy-update-2025"
OUT = Path("model_ready_inputs/reference/australian_energy_statistics_2025")
OUT.mkdir(parents=True, exist_ok=True)
TARGETS = {
    "table_b": "https://www.energy.gov.au/sites/default/files/2025-08/australian_energy_statistics_2025_table_b.xlsx",
    "table_f": "https://www.energy.gov.au/sites/default/files/2025-08/australian_energy_statistics_2025_table_f.xlsx",
    "table_l": "https://www.energy.gov.au/sites/default/files/2025-08/australian_energy_statistics_2025_table_l.xlsx",
}
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Accept-Language": "en-AU,en;q=0.9",
    "Referer": PAGE,
})


def valid_xlsx(path: Path, minimum: int) -> bool:
    return path.exists() and path.stat().st_size >= minimum and zipfile.is_zipfile(path)


def download_requests(url: str, path: Path, minimum: int) -> bool:
    last = None
    for attempt in range(8):
        try:
            with SESSION.get(url, timeout=(60, 600), stream=True, allow_redirects=True) as response:
                response.raise_for_status()
                temporary = path.with_suffix(".xlsx.part")
                with temporary.open("wb") as file:
                    for chunk in response.iter_content(1024 * 1024):
                        if chunk:
                            file.write(chunk)
                temporary.replace(path)
            if not valid_xlsx(path, minimum):
                raise RuntimeError(f"invalid or undersized XLSX: {path.stat().st_size if path.exists() else 0}")
            return True
        except Exception as exc:
            last = exc
            path.unlink(missing_ok=True)
            time.sleep(min(45, 2 ** attempt))
    print(f"requests download failed for {url}: {last!r}", flush=True)
    return False


def download_curl(url: str, path: Path, minimum: int) -> bool:
    command = [
        "curl", "--http1.1", "--location", "--fail", "--show-error", "--silent",
        "--retry", "12", "--retry-all-errors", "--retry-delay", "5",
        "--connect-timeout", "60", "--max-time", "1800",
        "--user-agent", SESSION.headers["User-Agent"], "--referer", PAGE,
        "--output", str(path), url,
    ]
    try:
        subprocess.run(command, check=True)
        return valid_xlsx(path, minimum)
    except Exception as exc:
        print(f"curl download failed for {url}: {exc!r}", flush=True)
        path.unlink(missing_ok=True)
        return False


def download_browser(url: str, path: Path, minimum: int) -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context(user_agent=SESSION.headers["User-Agent"], locale="en-AU")
            page = context.new_page()
            page.goto(PAGE, wait_until="domcontentloaded", timeout=240_000)
            response = context.request.get(url, headers={"Referer": PAGE}, timeout=900_000)
            if not response.ok:
                raise RuntimeError(f"browser request status={response.status}")
            path.write_bytes(response.body())
            browser.close()
        return valid_xlsx(path, minimum)
    except Exception as exc:
        print(f"browser download failed for {url}: {exc!r}", flush=True)
        path.unlink(missing_ok=True)
        return False


def download(key: str, url: str) -> Path:
    path = OUT / f"AES_2025_{key.upper()}.xlsx"
    minimum = 30_000 if key != "table_f" else 1_000_000
    if valid_xlsx(path, minimum):
        return path
    for method in (download_requests, download_curl, download_browser):
        if method(url, path, minimum):
            return path
    raise RuntimeError(f"{key}: all official attachment download methods failed")


def main() -> None:
    report = {"status": "PASS", "publication_page": PAGE, "files": []}
    for key, url in TARGETS.items():
        path = download(key, url)
        excel = pd.ExcelFile(path)
        previews = {}
        for sheet in excel.sheet_names:
            frame = pd.read_excel(path, sheet_name=sheet, header=None, nrows=25)
            previews[sheet] = frame.fillna("").astype(str).values.tolist()
        report["files"].append({
            "key": key,
            "path": str(path),
            "source_url": url,
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "sheets": excel.sheet_names,
            "preview_first_25_rows": previews,
        })
    (OUT / "AES_2025_download_audit.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "publication_page": PAGE,
        "files": [{key: value for key, value in item.items() if key != "preview_first_25_rows"} for item in report["files"]],
    }, indent=2))


if __name__ == "__main__":
    main()
