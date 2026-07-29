from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests

import build_data_bundle_v4 as v4

b = v4.b


def _json_request(url: str, params: dict[str, Any], attempts: int = 6) -> tuple[dict[str, Any], str]:
    """ArcGIS request with lightweight GET/POST fallbacks.

    The NSW bioenergy services intermittently fail when geometry is requested. This
    function is deliberately used with returnGeometry=false; geometry is supplied by
    the official ABS SA2 boundary file already downloaded in the bundle.
    """
    last: Exception | None = None
    for attempt in range(attempts):
        methods = ("get", "post") if attempt % 2 == 0 else ("post", "get")
        for method in methods:
            try:
                kwargs = {
                    "timeout": max(b.TIMEOUT, 240),
                    "headers": {
                        "Accept": "application/json,text/plain,*/*",
                        "Referer": url.rsplit("/query", 1)[0],
                        "User-Agent": b.BROWSER_UA,
                    },
                }
                if method == "get":
                    r = b.SESSION.get(url, params=params, **kwargs)
                else:
                    r = b.SESSION.post(url, data=params, **kwargs)
                r.raise_for_status()
                ctype = (r.headers.get("content-type") or "").lower()
                if "json" not in ctype and not r.text.lstrip().startswith("{"):
                    raise RuntimeError(f"non-JSON {r.status_code} {ctype}: {r.text[:180]!r}")
                obj = r.json()
                if isinstance(obj, dict) and obj.get("error"):
                    raise RuntimeError(f"ArcGIS error: {obj['error']}")
                if not isinstance(obj, dict):
                    raise RuntimeError("ArcGIS response is not a JSON object")
                return obj, r.url
            except Exception as exc:
                last = exc
        time.sleep(min(30, 2 ** attempt))
    raise RuntimeError(f"ArcGIS GET/POST failed after {attempts} attempts: {last!r}")


def _service_metadata(service_url: str) -> tuple[dict[str, Any], str]:
    last: Exception | None = None
    for i in range(6):
        try:
            return b.request_json(service_url, {"f": "pjson"})
        except Exception as exc:
            last = exc
            time.sleep(min(20, 2 ** i))
    raise RuntimeError(f"service metadata unavailable: {last!r}")


def _layer_attributes(service_url: str, layer: dict[str, Any], dataset: str, out_dir: Path) -> tuple[int, Path]:
    lid = int(layer["id"])
    lname = re.sub(r"[^A-Za-z0-9._-]+", "_", str(layer.get("name", f"layer_{lid}")))[:100]
    qurl = f"{service_url.rstrip('/')}/{lid}/query"

    # Use service-level layer information instead of repeatedly loading each layer page;
    # those pages are the part of the old ArcGIS server that most often returns HTML.
    page_size = 200
    rows: list[dict[str, Any]] = []
    seen_oids: set[Any] = set()
    oid_field = "OBJECTID"
    offset = 0

    while True:
        params: dict[str, Any] = {
            "where": "1=1",
            "outFields": "*",
            "returnGeometry": "false",
            "f": "json",
            "resultOffset": offset,
            "resultRecordCount": page_size,
            "orderByFields": oid_field,
        }
        try:
            obj, used_url = _json_request(qurl, params)
        except Exception:
            # Some older 10.9 services reject resultOffset/orderBy. Walk the OID instead.
            where = "1=1" if not seen_oids else f"{oid_field}>{max(seen_oids)}"
            params = {
                "where": where,
                "outFields": "*",
                "returnGeometry": "false",
                "f": "json",
                "resultRecordCount": page_size,
                "orderByFields": oid_field,
            }
            obj, used_url = _json_request(qurl, params)

        feats = obj.get("features") or []
        if not feats:
            break
        added = 0
        for feat in feats:
            attrs = feat.get("attributes") if isinstance(feat, dict) else None
            if attrs is None and isinstance(feat, dict):
                attrs = feat.get("properties")
            if not isinstance(attrs, dict):
                continue
            oid = attrs.get(oid_field)
            if oid is not None and oid in seen_oids:
                continue
            if oid is not None:
                seen_oids.add(oid)
            attrs = dict(attrs)
            attrs["SOURCE_LAYER_ID"] = lid
            attrs["SOURCE_LAYER_NAME"] = layer.get("name")
            rows.append(attrs)
            added += 1

        if len(feats) < page_size or added == 0:
            break
        offset += len(feats)
        if offset > 100000:
            raise RuntimeError("pagination safety limit exceeded")
        time.sleep(0.1)

    if not rows:
        raise RuntimeError(f"layer {lid} returned no attribute records")

    df = pd.DataFrame(rows)
    required_any = {"REGION_NAME", "TOTAL_RESIDUES", "RESIDUE_TYPE", "BIOMASS_DESCRIPTION"}
    if not required_any.intersection(df.columns):
        raise RuntimeError(f"layer {lid} missing expected biomass fields: {list(df.columns)[:20]}")

    out = out_dir / f"{lid:02d}_{lname}_attributes.csv"
    df.to_csv(out, index=False)
    b.record_file(dataset, used_url, out, n_records=len(df),
                  note="Attribute-only ArcGIS query; join REGION_NAME/REGION_TYPE to official ABS boundaries")
    return len(df), out


def _attribute_service(name: str, service_url: str, wanted_ids: list[int], minimum_nonempty: int) -> None:
    out_dir = b.RAW / "nsw_biomass_attributes" / name
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        meta, meta_url = _service_metadata(service_url)
        meta_path = out_dir / "service_metadata.json"
        b.save_json(meta, meta_path)
        b.record_file(name, meta_url, meta_path)
    except Exception as exc:
        b.records.append(b.Record(name, service_url, str(out_dir), "failed", note=repr(exc)))
        b.checks.append({"check": name, "passed": False, "value": 0, "detail": "service metadata failed"})
        return

    layer_map = {int(x["id"]): x for x in meta.get("layers", []) if x.get("type") == "Feature Layer"}
    catalog_rows: list[dict[str, Any]] = []
    completed = 0
    nonempty = 0
    for lid in wanted_ids:
        layer = layer_map.get(lid)
        if layer is None:
            b.records.append(b.Record(name, f"{service_url}/{lid}", str(out_dir), "failed",
                                      note="Layer ID absent from current service metadata"))
            continue
        try:
            n, path = _layer_attributes(service_url, layer, name, out_dir)
            completed += 1
            nonempty += int(n > 0)
            catalog_rows.append({"layer_id": lid, "layer_name": layer.get("name"),
                                 "records": n, "file": str(path)})
        except Exception as exc:
            b.records.append(b.Record(name, f"{service_url}/{lid}/query", str(out_dir), "failed", note=repr(exc)))

    pd.DataFrame(catalog_rows).to_csv(out_dir / "layer_catalogue.csv", index=False)
    b.checks.append({"check": name, "passed": nonempty >= minimum_nonempty,
                     "value": nonempty,
                     "detail": f"{completed}/{len(wanted_ids)} attribute layers complete; ABS SA2 geometry supplied separately"})


def spatial_and_biomass_v5() -> None:
    # Keep the already successful regional network GIS downloader.
    v4.selected_service(
        "essential_energy_uhc_2025",
        "https://portal.data.nsw.gov.au/arcgis/rest/services/Hosted/Essential_Energy_UHC_Data_2025/FeatureServer",
        [0, 1, 2],
        3,
    )
    _attribute_service(
        "biomass_livestock",
        "https://spatial.industry.nsw.gov.au/arcgis/rest/services/Bioenergy_Assessment/BiomassTool_Livestock/MapServer",
        [0, 1, 2],
        3,
    )
    _attribute_service(
        "biomass_cropping",
        "https://spatial.industry.nsw.gov.au/arcgis/rest/services/Bioenergy_Assessment/BiomassTool_Cropping/MapServer",
        list(range(19)),
        16,
    )
    _attribute_service(
        "biomass_forestry",
        "https://spatial.industry.nsw.gov.au/arcgis/rest/services/Bioenergy_Assessment/BiomassTool_Forestry/MapServer",
        list(range(7)),
        5,
    )
    _attribute_service(
        "biomass_organic_waste",
        "https://spatial.industry.nsw.gov.au/arcgis/rest/services/Bioenergy_Assessment/Waste_OrganicSolidWaste/MapServer",
        [0, 1, 2, 3],
        4,
    )


def _browser_attachment(page_url: str, regex: str, out: Path, dataset: str, min_bytes: int) -> bool:
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = browser.new_context(user_agent=b.BROWSER_UA, accept_downloads=True, locale="en-AU")
            page = context.new_page()
            page.goto(page_url, wait_until="domcontentloaded", timeout=240_000)
            page.wait_for_timeout(4000)
            rx = re.compile(regex, re.I)
            target = None
            href = ""
            for i in range(page.locator("a").count()):
                a = page.locator("a").nth(i)
                label = (a.inner_text(timeout=3000) or "").strip()
                candidate = a.get_attribute("href") or ""
                if rx.search(f"{label} {candidate}"):
                    target = a
                    href = requests.compat.urljoin(page.url, candidate)
                    break
            if target is None:
                raise RuntimeError(f"no attachment matched {regex!r}")

            # First use the authenticated browser request context. If that does not work,
            # navigate directly and capture the response bytes.
            response = context.request.get(href, headers={"Referer": page.url}, timeout=240_000)
            if response.ok and len(response.body()) >= min_bytes:
                out.write_bytes(response.body())
            else:
                nav = page.goto(href, wait_until="commit", timeout=240_000)
                if nav is None:
                    raise RuntimeError("attachment navigation returned no response")
                body = nav.body()
                if len(body) < min_bytes:
                    raise RuntimeError(f"attachment too small: {len(body)}")
                out.write_bytes(body)
            browser.close()
        if out.stat().st_size < min_bytes:
            raise RuntimeError(f"saved attachment too small: {out.stat().st_size}")
        b.record_file(dataset, href, out, note="Playwright browser-context attachment download")
        return True
    except Exception as exc:
        b.records.append(b.Record(dataset, page_url, str(out), "failed", note=f"browser attachment failed: {exc!r}"))
        return False


def _download_nga_factors() -> bool:
    page = "https://www.dcceew.gov.au/climate-change/publications/national-greenhouse-accounts-factors-2025"
    candidates = [
        ("https://www.dcceew.gov.au/sites/default/files/documents/national-greenhouse-account-factors-2025.xlsx",
         b.RAW / "emissions/dcceew/national_greenhouse_account_factors_2025.xlsx", 50_000, True),
        ("https://www.dcceew.gov.au/sites/default/files/documents/national-greenhouse-account-factors-2025.pdf",
         b.RAW / "emissions/dcceew/national_greenhouse_account_factors_2025.pdf", 500_000, False),
        ("https://www.dcceew.gov.au/sites/default/files/documents/national-greenhouse-account-factors-2025.docx",
         b.RAW / "emissions/dcceew/national_greenhouse_account_factors_2025.docx", 300_000, True),
    ]
    for url, out, minimum, office_zip in candidates:
        if b.download(url, out, "DCCEEW National Greenhouse Accounts Factors", min_bytes=minimum,
                      referer=page, validate_zip=False):
            return True
        if v4.curl_download(url, out, "DCCEEW National Greenhouse Accounts Factors",
                            minimum, page, office_zip=office_zip):
            return True
    # Browser links are visible on the official publication page even when the CDN
    # resets command-line downloads.
    browser_candidates = [
        (r"National Greenhouse Account Factors 2025.*XLSX", candidates[0][1], 50_000),
        (r"National Greenhouse Account Factors 2025.*PDF", candidates[1][1], 500_000),
        (r"National Greenhouse Account Factors 2025.*DOCX", candidates[2][1], 300_000),
    ]
    for rx, out, minimum in browser_candidates:
        if _browser_attachment(page, rx, out, "DCCEEW National Greenhouse Accounts Factors", minimum):
            return True
    return False


def official_documents_v5() -> None:
    # Reuse all successful ABS, CSIRO and optional-document logic from v4, but remove
    # the failed v4 NGA check before adding the resilient multi-format check below.
    direct = [
        ("ABS regional population SA2",
         "https://www.abs.gov.au/statistics/people/population/regional-population/2024-25/32180DS0003_2001-25.xlsx",
         b.RAW / "demographics/abs_population/32180DS0003_2001-25.xlsx", 100_000, False),
        ("ABS 2021 SA2 boundaries",
         "https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/edition-3-july-2021-june-2026/access-and-downloads/digital-boundary-files/SA2_2021_AUST_SHP_GDA2020.zip",
         b.RAW / "demographics/abs_boundaries/SA2_2021_AUST_SHP_GDA2020.zip", 10_000_000, True),
        ("ABS 2021 Census NSW SA2 DataPack",
         "https://www.abs.gov.au/census/find-census-data/datapacks/download/2021_GCP_SA2_for_NSW_short-header.zip",
         b.RAW / "demographics/abs_census/2021_GCP_SA2_for_NSW_short-header.zip", 5_000_000, True),
    ]
    for dataset, url, out, minimum, is_zip in direct:
        ok = b.download(url, out, dataset, min_bytes=minimum, validate_zip=is_zip)
        b.checks.append({"check": dataset, "passed": ok, "value": int(ok), "detail": str(out)})

    nga_ok = _download_nga_factors()
    b.checks.append({"check": "DCCEEW National Greenhouse Accounts Factors", "passed": nga_ok,
                     "value": int(nga_ok), "detail": "At least one official XLSX/PDF/DOCX format"})

    before = len(b.checks)
    b.download_matching_documents(
        "CSIRO GenCost",
        "https://www.csiro.au/en/research/technology-space/energy/electricity-transition/gencost",
        [r"gencost.*\.pdf", r"gencost.*\.xlsx"],
        b.RAW / "economics/csiro_gencost",
    )
    assert len(b.checks) > before

    # Supplementary but useful: do not create failing core checks.
    aemo_page = "https://www.aemo.com.au/energy-systems/major-publications/integrated-system-plan-isp/2026-integrated-system-plan-isp/2025-26-inputs-assumptions-and-scenarios"
    _browser_attachment(aemo_page, r"2025 Inputs and Assumptions Workbook",
                        b.RAW / "economics/aemo/2025_inputs_and_assumptions_workbook.xlsm",
                        "AEMO inputs and assumptions (supplementary)", 5_000_000)
    petroleum_page = "https://www.energy.gov.au/publications/australian-petroleum-statistics-2026"
    _browser_attachment(petroleum_page, r"Data Extract May 2026",
                        b.RAW / "fuel/australian_petroleum_statistics_may_2026.xlsx",
                        "Australian Petroleum Statistics (supplementary)", 1_000_000)


b.nsw_spatial_and_biomass = spatial_and_biomass_v5
b.official_economic_emissions_and_demographic_documents = official_documents_v5


if __name__ == "__main__":
    b.main()
