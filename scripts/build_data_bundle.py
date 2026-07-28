from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import sys
import time
import zipfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

ROOT = Path(os.environ.get("BUNDLE_ROOT", "bundle"))
RAW = ROOT / "raw"
META = ROOT / "metadata"
DERIVED = ROOT / "derived"
LOGS = ROOT / "logs"
for p in (RAW, META, DERIVED, LOGS):
    p.mkdir(parents=True, exist_ok=True)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Regional-NSW-Microgrid-Research/1.0 (academic data collection)"})
TIMEOUT = 120


@dataclass
class Record:
    dataset: str
    source_url: str
    local_path: str
    status: str
    http_status: int | None = None
    bytes: int | None = None
    sha256: str | None = None
    note: str = ""


records: list[Record] = []


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_name(url: str, default: str) -> str:
    name = Path(urlparse(url).path).name or default
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)


def download(url: str, out: Path, dataset: str, *, min_bytes: int = 100, overwrite: bool = False) -> bool:
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and out.stat().st_size >= min_bytes and not overwrite:
        records.append(Record(dataset, url, str(out), "cached", bytes=out.stat().st_size, sha256=sha256(out)))
        return True
    try:
        with SESSION.get(url, stream=True, timeout=TIMEOUT, allow_redirects=True) as r:
            status = r.status_code
            r.raise_for_status()
            tmp = out.with_suffix(out.suffix + ".part")
            with tmp.open("wb") as f:
                for chunk in r.iter_content(1024 * 1024):
                    if chunk:
                        f.write(chunk)
            if tmp.stat().st_size < min_bytes:
                raise RuntimeError(f"download too small: {tmp.stat().st_size} bytes")
            tmp.replace(out)
            records.append(Record(dataset, url, str(out), "downloaded", status, out.stat().st_size, sha256(out)))
            return True
    except Exception as e:
        records.append(Record(dataset, url, str(out), "failed", note=repr(e)))
        return False


def get_text(url: str) -> str:
    r = SESSION.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def scrape_links(page_url: str, patterns: Iterable[str]) -> list[str]:
    html = get_text(page_url)
    soup = BeautifulSoup(html, "html.parser")
    pats = [re.compile(p, re.I) for p in patterns]
    links: list[str] = []
    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"])
        text = f"{a.get_text(' ', strip=True)} {href}"
        if any(p.search(text) for p in pats):
            links.append(href)
    return list(dict.fromkeys(links))


def save_json(obj, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def arcgis_service(service_url: str, name: str):
    base_dir = RAW / "nsw_arcgis" / name
    base_dir.mkdir(parents=True, exist_ok=True)
    metadata_url = service_url.rstrip("/") + "?f=pjson"
    try:
        meta = SESSION.get(metadata_url, timeout=TIMEOUT).json()
        save_json(meta, base_dir / "service_metadata.json")
        records.append(Record(name, metadata_url, str(base_dir / "service_metadata.json"), "downloaded", 200,
                              (base_dir / "service_metadata.json").stat().st_size, sha256(base_dir / "service_metadata.json")))
    except Exception as e:
        records.append(Record(name, metadata_url, str(base_dir / "service_metadata.json"), "failed", note=repr(e)))
        return

    layers = []
    layers.extend(meta.get("layers", []))
    layers.extend(meta.get("tables", []))
    for layer in layers:
        lid = layer["id"]
        lname = re.sub(r"[^A-Za-z0-9._-]+", "_", layer.get("name", f"layer_{lid}"))[:100]
        layer_url = service_url.rstrip("/") + f"/{lid}"
        query_url = layer_url + "/query"
        params = {
            "where": "1=1",
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "geojson",
        }
        out = base_dir / f"{lid:02d}_{lname}.geojson"
        try:
            r = SESSION.get(query_url, params=params, timeout=TIMEOUT)
            if r.status_code == 200 and "application/json" in r.headers.get("content-type", ""):
                data = r.json()
                if "features" in data:
                    save_json(data, out)
                    records.append(Record(name, r.url, str(out), "downloaded", r.status_code, out.stat().st_size, sha256(out)))
                    continue
            # Fall back to Esri JSON for tables or services that reject GeoJSON.
            params["f"] = "json"
            r = SESSION.get(query_url, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            out = out.with_suffix(".json")
            save_json(r.json(), out)
            records.append(Record(name, r.url, str(out), "downloaded", r.status_code, out.stat().st_size, sha256(out)))
        except Exception as e:
            records.append(Record(name, query_url, str(out), "failed", note=repr(e)))
        time.sleep(0.25)


def essential_energy_loads():
    page = "https://www.essentialenergy.com.au/our-network/network-projects/zone-substation-reports"
    out_dir = RAW / "essential_energy_load"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        links = scrape_links(page, [r"zone.*substation.*load", r"\.zip(?:\?|$)"])
    except Exception as e:
        records.append(Record("Essential Energy zone-substation load", page, str(out_dir), "failed", note=repr(e)))
        links = []

    # Historical URL patterns retained as fallbacks if page HTML changes.
    years = [f"{y}-{str(y+1)[-2:]}" for y in range(2012, 2025)]
    fallbacks = []
    for yr in years:
        fallbacks += [
            f"https://www.essentialenergy.com.au/ext/zonesubs/EE-Zone-Substation-Load-Data-{yr}.zip",
            f"https://www.essentialenergy.com.au/ext/schools/EE-Zone-Substation-Load-Data-{yr}.zip",
        ]
    links = list(dict.fromkeys(links + fallbacks))

    successful_years: set[str] = set()
    for url in links:
        m = re.search(r"20\d{2}[-_]\d{2}", url)
        yr = m.group(0).replace("_", "-") if m else safe_name(url, "load")
        if yr in successful_years:
            continue
        out = out_dir / safe_name(url, f"load_{yr}.zip")
        if download(url, out, "Essential Energy zone-substation load", min_bytes=1000):
            successful_years.add(yr)

    # Extract valid ZIPs and inventory CSV schemas.
    inventory = []
    for z in out_dir.glob("*.zip"):
        try:
            with zipfile.ZipFile(z) as arc:
                extract_dir = out_dir / "extracted" / z.stem
                arc.extractall(extract_dir)
                for member in arc.namelist():
                    inventory.append({"archive": z.name, "member": member})
        except Exception as e:
            records.append(Record("Essential Energy extraction", str(z), str(z), "failed", note=repr(e)))
    pd.DataFrame(inventory).to_csv(META / "essential_energy_archive_inventory.csv", index=False)


def nsw_spatial_and_biomass():
    services = {
        "essential_energy_uhc_2025": "https://portal.data.nsw.gov.au/arcgis/rest/services/Hosted/Essential_Energy_UHC_Data_2025/FeatureServer",
        "biomass_livestock": "https://spatial.industry.nsw.gov.au/arcgis/rest/services/Bioenergy_Assessment/BiomassTool_Livestock/MapServer",
        "biomass_cropping": "https://spatial.industry.nsw.gov.au/arcgis/rest/services/Bioenergy_Assessment/BiomassTool_Cropping/MapServer",
        "biomass_forestry": "https://spatial.industry.nsw.gov.au/arcgis/rest/services/Bioenergy_Assessment/BiomassTool_Forestry/MapServer",
        "biomass_organic_waste": "https://spatial.industry.nsw.gov.au/arcgis/rest/services/Bioenergy_Assessment/Waste_OrganicSolidWaste/MapServer",
    }
    for name, url in services.items():
        arcgis_service(url, name)


def candidate_sites_from_gis(limit: int = 20) -> pd.DataFrame:
    files = list((RAW / "nsw_arcgis" / "essential_energy_uhc_2025").glob("*.geojson"))
    rows = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            for ft in data.get("features", []):
                geom = ft.get("geometry") or {}
                if geom.get("type") != "Point":
                    continue
                coords = geom.get("coordinates", [])
                if len(coords) < 2:
                    continue
                p = ft.get("properties") or {}
                name = next((str(v) for k, v in p.items() if v not in (None, "") and any(x in k.lower() for x in ("name", "substation", "site"))), "site")
                rows.append({"site_name": name, "longitude": coords[0], "latitude": coords[1], "source_file": f.name})
        except Exception:
            pass
    if not rows:
        # Geographically diverse NSW fallback coordinates; used only if the live GIS schema changes.
        rows = [
            {"site_name": "Broken Hill", "latitude": -31.95, "longitude": 141.45, "source_file": "fallback"},
            {"site_name": "Dubbo", "latitude": -32.25, "longitude": 148.60, "source_file": "fallback"},
            {"site_name": "Orange", "latitude": -33.28, "longitude": 149.10, "source_file": "fallback"},
            {"site_name": "Tamworth", "latitude": -31.09, "longitude": 150.93, "source_file": "fallback"},
            {"site_name": "Wagga Wagga", "latitude": -35.11, "longitude": 147.37, "source_file": "fallback"},
            {"site_name": "Griffith", "latitude": -34.29, "longitude": 146.05, "source_file": "fallback"},
            {"site_name": "Moree", "latitude": -29.47, "longitude": 149.84, "source_file": "fallback"},
            {"site_name": "Coffs Harbour", "latitude": -30.30, "longitude": 153.12, "source_file": "fallback"},
        ]
    df = pd.DataFrame(rows).drop_duplicates(subset=["latitude", "longitude"])
    # Spatially spread candidates by sorting longitude then latitude and sampling evenly.
    df = df.sort_values(["longitude", "latitude"]).reset_index(drop=True)
    if len(df) > limit:
        idx = [round(i) for i in pd.Series(range(limit)).map(lambda x: x * (len(df) - 1) / max(limit - 1, 1))]
        df = df.iloc[idx].drop_duplicates().reset_index(drop=True)
    df.to_csv(DERIVED / "candidate_sites.csv", index=False)
    return df


def nasa_power_weather():
    sites = candidate_sites_from_gis(limit=20)
    out_dir = RAW / "nasa_power_hourly"
    out_dir.mkdir(parents=True, exist_ok=True)
    parameters = [
        "ALLSKY_SFC_SW_DWN", "ALLSKY_SFC_SW_DNI", "ALLSKY_SFC_SW_DIFF",
        "T2M", "T2M_MAX", "T2M_MIN", "RH2M", "PS", "PRECTOTCORR",
        "WS10M", "WD10M", "WS50M", "WD50M",
    ]
    start = os.environ.get("NASA_START", "20150101")
    end = os.environ.get("NASA_END", "20251231")
    endpoint = "https://power.larc.nasa.gov/api/temporal/hourly/point"
    for _, row in sites.iterrows():
        slug = re.sub(r"[^A-Za-z0-9_-]+", "_", str(row.site_name))[:50]
        out = out_dir / f"{slug}_{row.latitude:.4f}_{row.longitude:.4f}_{start}_{end}.csv"
        params = {
            "parameters": ",".join(parameters),
            "community": "RE",
            "longitude": row.longitude,
            "latitude": row.latitude,
            "start": start,
            "end": end,
            "format": "CSV",
            "time-standard": "UTC",
        }
        url = requests.Request("GET", endpoint, params=params).prepare().url
        download(url, out, "NASA POWER hourly weather", min_bytes=1000)
        time.sleep(1.0)


def pvlib_databases():
    import pvlib
    out_dir = RAW / "pv_component_databases"
    out_dir.mkdir(parents=True, exist_ok=True)
    for dbname in ("CECMod", "CECInverter", "SandiaMod"):
        try:
            df = pvlib.pvsystem.retrieve_sam(dbname)
            out = out_dir / f"{dbname}.csv"
            df.to_csv(out)
            records.append(Record(f"PVLib {dbname}", "pvlib bundled SAM database", str(out), "exported", bytes=out.stat().st_size, sha256=sha256(out)))
        except Exception as e:
            records.append(Record(f"PVLib {dbname}", "pvlib bundled SAM database", str(out_dir), "failed", note=repr(e)))


def scrape_official_documents():
    targets = [
        (
            "AEMO inputs and assumptions",
            "https://www.aemo.com.au/energy-systems/major-publications/integrated-system-plan-isp/2026-integrated-system-plan-isp/2025-26-inputs-assumptions-and-scenarios",
            [r"inputs.*assumptions.*workbook", r"\.xlsm(?:\?|$)", r"\.xlsx(?:\?|$)"],
            RAW / "economics" / "aemo",
        ),
        (
            "DCCEEW National Greenhouse Accounts Factors",
            "https://www.dcceew.gov.au/climate-change/publications/national-greenhouse-accounts-factors-2025",
            [r"greenhouse.*factors", r"\.xlsx(?:\?|$)", r"\.pdf(?:\?|$)"],
            RAW / "emissions" / "dcceew",
        ),
        (
            "CSIRO GenCost",
            "https://www.csiro.au/en/research/technology-space/energy/electricity-transition/gencost",
            [r"gencost", r"\.pdf(?:\?|$)", r"\.xlsx(?:\?|$)"],
            RAW / "economics" / "csiro_gencost",
        ),
    ]
    for dataset, page, patterns, out_dir in targets:
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            links = scrape_links(page, patterns)
            for i, url in enumerate(links):
                ext = Path(urlparse(url).path).suffix.lower()
                if ext not in {".xlsx", ".xlsm", ".xls", ".pdf", ".docx", ".csv", ".zip"}:
                    continue
                download(url, out_dir / safe_name(url, f"file_{i}{ext}"), dataset, min_bytes=1000)
        except Exception as e:
            records.append(Record(dataset, page, str(out_dir), "failed", note=repr(e)))


def abs_catalogue():
    # ABS Data API catalogue and regional-data discovery are preserved locally so exact tables can be selected reproducibly.
    urls = {
        "dataflows_all": "https://api.data.abs.gov.au/dataflow/all/all/latest?format=csvfile",
        "codelists_all": "https://api.data.abs.gov.au/codelist/all/all/latest?format=csvfile",
    }
    out_dir = RAW / "abs_api_catalogue"
    for name, url in urls.items():
        download(url, out_dir / f"{name}.csv", "ABS Data API catalogue", min_bytes=500)


def create_licence_and_source_register():
    rows = [
        ["Essential Energy zone-substation load", "Essential Energy", "Measured half-hourly zone-substation active/reactive power", "Public webpage terms; verify reuse notice", "https://www.essentialenergy.com.au/our-network/network-projects/zone-substation-reports"],
        ["Essential Energy network GIS", "NSW Government Spatial Services", "Substation and network spatial attributes", "NSW Open Data terms shown by dataset", "https://portal.data.nsw.gov.au/"],
        ["NASA POWER", "NASA", "Hourly solar and meteorological data", "NASA data policy / attribution", "https://power.larc.nasa.gov/"],
        ["NSW Bioenergy Assessment", "NSW Government", "Livestock, cropping, forestry and organic-waste biomass layers", "NSW spatial-data terms shown by service", "https://spatial.industry.nsw.gov.au/arcgis/rest/services/Bioenergy_Assessment"],
        ["ABS Data API", "Australian Bureau of Statistics", "Population and regional statistical catalogue", "Creative Commons Attribution 4.0 unless dataset states otherwise", "https://www.abs.gov.au/statistics/application-programming-interfaces-apis/data-api-user-guide"],
        ["AEMO Inputs and Assumptions", "AEMO", "Technology costs and planning assumptions", "AEMO website copyright/terms", "https://www.aemo.com.au/"],
        ["National Greenhouse Accounts Factors", "DCCEEW", "Australian emissions factors", "Commonwealth copyright/attribution", "https://www.dcceew.gov.au/"],
        ["CSIRO GenCost", "CSIRO", "Generation and storage cost benchmarks", "CSIRO copyright/attribution", "https://www.csiro.au/"],
        ["PV component databases", "NREL/SAM via pvlib", "PV module and inverter parameter databases", "Database-specific licence; retain source metadata", "https://pvlib-python.readthedocs.io/"],
    ]
    pd.DataFrame(rows, columns=["dataset", "publisher", "use", "licence_note", "source"]).to_csv(META / "source_and_licence_register.csv", index=False)


def finalise():
    create_licence_and_source_register()
    df = pd.DataFrame([asdict(r) for r in records])
    df.to_csv(META / "download_manifest.csv", index=False)
    save_json([asdict(r) for r in records], META / "download_manifest.json")

    failures = df[df.status == "failed"] if not df.empty else pd.DataFrame()
    summary = {
        "generated_utc": pd.Timestamp.utcnow().isoformat(),
        "record_count": len(df),
        "successful_or_cached": int(df.status.isin(["downloaded", "cached", "exported"]).sum()) if not df.empty else 0,
        "failed": int((df.status == "failed").sum()) if not df.empty else 0,
        "bundle_bytes": sum(p.stat().st_size for p in ROOT.rglob("*") if p.is_file()),
        "notes": [
            "All raw files are retained unchanged where possible.",
            "The manifest records URL, size and SHA-256 checksum.",
            "NASA POWER weather is collected for up to 20 spatially diverse candidate sites for 2015-2025.",
            "A failed optional source does not silently become synthetic data; failures remain explicit in the manifest.",
        ],
    }
    save_json(summary, META / "bundle_summary.json")
    if not failures.empty:
        failures.to_csv(LOGS / "failed_downloads.csv", index=False)

    readme = f"""# Regional NSW Microgrid Data Bundle\n\nGenerated: {summary['generated_utc']}\n\nFiles recorded: {summary['record_count']}  \nSuccessful/cached/exported: {summary['successful_or_cached']}  \nFailed attempts: {summary['failed']}  \nTotal bytes: {summary['bundle_bytes']}\n\n## Core folders\n- `raw/essential_energy_load`: measured zone-substation load archives and extracted members\n- `raw/nsw_arcgis`: Essential Energy network and NSW biomass GIS layers\n- `raw/nasa_power_hourly`: hourly weather/resource data\n- `raw/economics`: AEMO and CSIRO cost assumptions\n- `raw/emissions`: official emissions factors\n- `raw/abs_api_catalogue`: ABS API discovery catalogues\n- `raw/pv_component_databases`: PV module and inverter databases\n- `derived/candidate_sites.csv`: weather-download sites\n- `metadata/download_manifest.csv`: URL, status, size and checksum for every retrieval\n- `metadata/source_and_licence_register.csv`: source and reuse notes\n\nCheck `logs/failed_downloads.csv` before modelling. No failed source should be treated as available.\n"""
    (ROOT / "README.md").write_text(readme, encoding="utf-8")


def main():
    essential_energy_loads()
    nsw_spatial_and_biomass()
    nasa_power_weather()
    pvlib_databases()
    scrape_official_documents()
    abs_catalogue()
    finalise()


if __name__ == "__main__":
    try:
        main()
    finally:
        if records:
            finalise()
