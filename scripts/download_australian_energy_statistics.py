from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import zipfile
from pathlib import Path
from urllib.parse import unquote

import pandas as pd
import requests

PAGE = "https://www.energy.gov.au/publications/australian-energy-update-2025"
MACHINE_READABLE_PAGE = (
    "https://www.energy.gov.au/publications/"
    "australian-energy-statistics-2025-machine-readable-files"
)
MACHINE_READABLE_ZIP = (
    "https://www.energy.gov.au/sites/default/files/2025-08/"
    "Australian%20Energy%20Statistics%202025%20machine%20readable%20files.zip.zip"
)
OUT = Path("model_ready_inputs/reference/australian_energy_statistics_2025")
OUT.mkdir(parents=True, exist_ok=True)

INDIVIDUAL_URLS = {
    "table_b": "https://www.energy.gov.au/sites/default/files/2025-08/australian_energy_statistics_2025_table_b.xlsx",
    "table_f": "https://www.energy.gov.au/sites/default/files/2025-08/australian_energy_statistics_2025_table_f.xlsx",
    "table_l": "https://www.energy.gov.au/sites/default/files/2025-08/australian_energy_statistics_2025_table_l.xlsx",
}
MINIMUM_BYTES = {"table_b": 40_000, "table_f": 1_000_000, "table_l": 25_000}
SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/126 Safari/537.36"
        ),
        "Accept-Language": "en-AU,en;q=0.9",
        "Referer": PAGE,
    }
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def valid_zip(path: Path, minimum: int = 1_000_000) -> bool:
    return path.exists() and path.stat().st_size >= minimum and zipfile.is_zipfile(path)


def valid_xlsx(path: Path, minimum: int) -> bool:
    return path.exists() and path.stat().st_size >= minimum and zipfile.is_zipfile(path)


def bounded_download(url: str, path: Path, minimum: int, attempts: int = 3) -> None:
    """Download with strict time bounds so a CI run cannot hang for 45 minutes."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        temporary = path.with_suffix(path.suffix + ".part")
        temporary.unlink(missing_ok=True)
        try:
            with SESSION.get(
                url,
                timeout=(20, 90),
                stream=True,
                allow_redirects=True,
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                with temporary.open("wb") as file:
                    for chunk in response.iter_content(1024 * 1024):
                        if chunk:
                            file.write(chunk)
                if temporary.stat().st_size < minimum:
                    raise RuntimeError(
                        f"undersized download: {temporary.stat().st_size} bytes; "
                        f"content-type={content_type}"
                    )
                temporary.replace(path)
                return
        except Exception as exc:  # pragma: no cover - network-dependent
            last_error = exc
            temporary.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            if attempt < attempts:
                time.sleep(2**attempt)
    raise RuntimeError(f"download failed after {attempts} attempts: {url}: {last_error!r}")


def normalise_member_name(name: str) -> str:
    decoded = unquote(Path(name).name).lower()
    return re.sub(r"[^a-z0-9]+", " ", decoded).strip()


def identify_table_members(archive: zipfile.ZipFile) -> dict[str, str]:
    candidates: dict[str, list[str]] = {key: [] for key in INDIVIDUAL_URLS}
    for member in archive.namelist():
        if member.endswith("/") or not member.lower().endswith(".xlsx"):
            continue
        normalised = normalise_member_name(member)
        for key, letter in (("table_b", "b"), ("table_f", "f"), ("table_l", "l")):
            patterns = (
                rf"\btable\s*{letter}\b",
                rf"\baes\s*2025\s*{letter}\b",
                rf"\b2025\s*table\s*{letter}\b",
            )
            if any(re.search(pattern, normalised) for pattern in patterns):
                candidates[key].append(member)
    selected: dict[str, str] = {}
    for key, members in candidates.items():
        if not members:
            continue
        # Prefer the shortest, least nested canonical-looking name.
        selected[key] = sorted(members, key=lambda value: (value.count("/"), len(value)))[0]
    return selected


def extract_from_machine_readable_zip() -> tuple[dict[str, Path], dict[str, str]]:
    zip_path = OUT / "Australian_Energy_Statistics_2025_machine_readable.zip"
    if not valid_zip(zip_path):
        bounded_download(MACHINE_READABLE_ZIP, zip_path, minimum=1_000_000, attempts=3)
    if not valid_zip(zip_path):
        raise RuntimeError("official machine-readable archive is not a valid ZIP")

    outputs: dict[str, Path] = {}
    members_used: dict[str, str] = {}
    with zipfile.ZipFile(zip_path) as archive:
        members = identify_table_members(archive)
        for key in INDIVIDUAL_URLS:
            member = members.get(key)
            if member is None:
                continue
            destination = OUT / f"AES_2025_{key.upper()}.xlsx"
            with archive.open(member) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)
            if not valid_xlsx(destination, MINIMUM_BYTES[key]):
                destination.unlink(missing_ok=True)
                continue
            outputs[key] = destination
            members_used[key] = member
    return outputs, members_used


def download_individual_fallback(key: str) -> Path:
    path = OUT / f"AES_2025_{key.upper()}.xlsx"
    if valid_xlsx(path, MINIMUM_BYTES[key]):
        return path
    bounded_download(INDIVIDUAL_URLS[key], path, minimum=MINIMUM_BYTES[key], attempts=2)
    if not valid_xlsx(path, MINIMUM_BYTES[key]):
        raise RuntimeError(f"{key} fallback file is not a valid XLSX")
    return path


def extract_nsw_rows(path: Path, key: str) -> list[dict]:
    rows: list[dict] = []
    excel = pd.ExcelFile(path)
    for sheet in excel.sheet_names:
        frame = pd.read_excel(path, sheet_name=sheet, header=None, nrows=500)
        for index, series in frame.iterrows():
            values = ["" if pd.isna(value) else str(value).strip() for value in series.tolist()]
            combined = " | ".join(values).lower()
            exact_cells = {value.lower() for value in values if value}
            if "new south wales" in combined or "nsw" in exact_cells:
                rows.append(
                    {
                        "table": key,
                        "sheet": str(sheet),
                        "zero_based_row": int(index),
                        "values": values,
                    }
                )
    return rows


def main() -> None:
    machine_error: str | None = None
    members_used: dict[str, str] = {}
    try:
        paths, members_used = extract_from_machine_readable_zip()
    except Exception as exc:  # pragma: no cover - network-dependent
        machine_error = repr(exc)
        paths = {}

    source_method: dict[str, str] = {}
    for key in INDIVIDUAL_URLS:
        if key in paths:
            source_method[key] = "official_machine_readable_zip"
        else:
            paths[key] = download_individual_fallback(key)
            source_method[key] = "official_individual_attachment_fallback"

    report: dict = {
        "status": "PASS",
        "publication_page": PAGE,
        "machine_readable_page": MACHINE_READABLE_PAGE,
        "machine_readable_zip": MACHINE_READABLE_ZIP,
        "machine_readable_error_before_fallback": machine_error,
        "files": [],
    }
    all_nsw_rows: list[dict] = []
    for key, path in paths.items():
        excel = pd.ExcelFile(path)
        previews = {}
        for sheet in excel.sheet_names:
            frame = pd.read_excel(path, sheet_name=sheet, header=None, nrows=25)
            previews[str(sheet)] = frame.fillna("").astype(str).values.tolist()
        nsw_rows = extract_nsw_rows(path, key)
        all_nsw_rows.extend(nsw_rows)
        report["files"].append(
            {
                "key": key,
                "path": str(path),
                "source_method": source_method[key],
                "source_url": (
                    MACHINE_READABLE_ZIP
                    if source_method[key] == "official_machine_readable_zip"
                    else INDIVIDUAL_URLS[key]
                ),
                "archive_member": members_used.get(key),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
                "sheets": [str(value) for value in excel.sheet_names],
                "nsw_keyword_row_count": len(nsw_rows),
                "preview_first_25_rows": previews,
            }
        )

    checks = {
        "all_three_tables_present": set(paths) == set(INDIVIDUAL_URLS),
        "all_three_valid_xlsx": all(
            valid_xlsx(paths[key], MINIMUM_BYTES[key]) for key in INDIVIDUAL_URLS
        ),
        "all_three_have_sheets": all(
            len(pd.ExcelFile(paths[key]).sheet_names) > 0 for key in INDIVIDUAL_URLS
        ),
        "nsw_rows_discovered": len(all_nsw_rows) > 0,
    }
    report["checks"] = checks
    if not all(checks.values()):
        report["status"] = "FAIL"

    audit_path = OUT / "AES_2025_download_audit.json"
    audit_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    (OUT / "AES_2025_NSW_keyword_rows.json").write_text(
        json.dumps(all_nsw_rows, indent=2), encoding="utf-8"
    )
    compact = {
        "status": report["status"],
        "checks": checks,
        "files": [
            {key: value for key, value in item.items() if key != "preview_first_25_rows"}
            for item in report["files"]
        ],
    }
    print(json.dumps(compact, indent=2), flush=True)
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
