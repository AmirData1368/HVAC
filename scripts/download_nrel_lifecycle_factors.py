from __future__ import annotations

import hashlib
import json
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

URL = "https://data.nlr.gov/system/files/171/EF_Table_FINAL.xlsx"
OUT = Path("model_ready_inputs/reference")
OUT.mkdir(parents=True, exist_ok=True)
WORKBOOK = OUT / "NREL_EF_Table_FINAL.xlsx"


def download() -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://data.nlr.gov/submissions/171"})
    last = None
    for attempt in range(8):
        try:
            response = session.get(URL, timeout=240, allow_redirects=True)
            response.raise_for_status()
            if len(response.content) < 20_000:
                raise RuntimeError(f"download too small: {len(response.content)} bytes")
            WORKBOOK.write_bytes(response.content)
            if not zipfile.is_zipfile(WORKBOOK):
                raise RuntimeError("download is not a valid XLSX archive")
            return
        except Exception as exc:
            last = exc
            time.sleep(min(30, 2 ** attempt))
    raise RuntimeError(f"NREL lifecycle workbook download failed: {last!r}")


def main() -> None:
    download()
    excel = pd.ExcelFile(WORKBOOK)
    previews = {}
    for sheet in excel.sheet_names:
        frame = pd.read_excel(WORKBOOK, sheet_name=sheet, header=None, nrows=30)
        previews[sheet] = frame.fillna("").astype(str).values.tolist()
    digest = hashlib.sha256(WORKBOOK.read_bytes()).hexdigest()
    report = {
        "status": "PASS",
        "source_url": URL,
        "catalogue_url": "https://data.nlr.gov/submissions/171",
        "doi": "10.7799/1819907",
        "bytes": WORKBOOK.stat().st_size,
        "sha256": digest,
        "sheet_names": excel.sheet_names,
        "preview_first_30_rows": previews,
    }
    (OUT / "NREL_EF_Table_FINAL_audit.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "preview_first_30_rows"}, indent=2))


if __name__ == "__main__":
    main()
