from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd

import build_data_bundle_v5 as v5

b = v5.b


def _install_model_ready_nga_factors() -> bool:
    """Install a verified, model-ready extract of the official 2025 NGA Factors.

    The DCCEEW publication page and attachment URLs are public, but the attachment CDN
    repeatedly resets GitHub-hosted runners. The values required by this microgrid model
    are therefore stored as a transparent, version-controlled extract. Every row retains
    the official PDF URL, table number, page and units. This is not labelled as a raw
    DCCEEW download.
    """
    source = Path("reference_data/dcceew_nga_factors_2025_model_inputs.csv")
    out_dir = b.RAW / "emissions" / "dcceew"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "dcceew_nga_factors_2025_model_inputs.csv"
    if not source.exists():
        b.records.append(b.Record(
            "DCCEEW 2025 model-ready emission factors", str(source), str(out),
            "failed", note="Version-controlled reference extract missing from repository"))
        return False

    df = pd.read_csv(source)
    required_columns = {
        "factor_id", "category", "item", "use_case", "source_table",
        "pdf_page", "source_url", "notes"
    }
    required_ids = {
        "ELEC_NSW_LOCATION", "ELEC_OFFGRID_CFI", "DIESEL_STATIONARY",
        "DRY_WOOD", "PRIMARY_SOLID_BIOMASS", "LANDFILL_BIOGAS",
        "OTHER_BIOGAS", "BIOMETHANE", "DIESEL_HEAVY_EURO_IV",
        "WASTE_FOOD", "WASTE_GARDEN", "WASTE_WOOD", "WASTE_SLUDGE"
    }
    if not required_columns.issubset(df.columns):
        b.records.append(b.Record(
            "DCCEEW 2025 model-ready emission factors", str(source), str(out),
            "failed", note=f"Missing columns: {sorted(required_columns - set(df.columns))}"))
        return False
    missing_ids = required_ids - set(df["factor_id"].dropna().astype(str))
    if missing_ids:
        b.records.append(b.Record(
            "DCCEEW 2025 model-ready emission factors", str(source), str(out),
            "failed", note=f"Missing core factor IDs: {sorted(missing_ids)}"))
        return False
    if df["source_url"].nunique() != 1 or not df["source_url"].iloc[0].endswith(
            "national-greenhouse-account-factors-2025.pdf"):
        b.records.append(b.Record(
            "DCCEEW 2025 model-ready emission factors", str(source), str(out),
            "failed", note="Official PDF provenance validation failed"))
        return False

    shutil.copy2(source, out)
    b.record_file(
        "DCCEEW 2025 model-ready emission factors",
        "https://www.dcceew.gov.au/climate-change/publications/national-greenhouse-accounts-factors-2025",
        out, status="derived", n_records=len(df),
        note=("Version-controlled extract from official 2025 NGA PDF; each row includes "
              "table, page, units and official source URL; raw attachment CDN blocks cloud runners"))

    links = pd.DataFrame([
        {
            "format": "publication_page",
            "url": "https://www.dcceew.gov.au/climate-change/publications/national-greenhouse-accounts-factors-2025",
            "availability": "public",
            "note": "Official publication page"
        },
        {
            "format": "PDF",
            "url": "https://www.dcceew.gov.au/sites/default/files/documents/national-greenhouse-account-factors-2025.pdf",
            "availability": "public_but_cloud_runner_connection_resets",
            "note": "Official 63-page report, CC BY 4.0"
        },
        {
            "format": "XLSX",
            "url": "https://www.dcceew.gov.au/sites/default/files/documents/national-greenhouse-account-factors-2025.xlsx",
            "availability": "public_but_cloud_runner_connection_resets",
            "note": "Official 2025 workbook"
        },
        {
            "format": "DOCX",
            "url": "https://www.dcceew.gov.au/sites/default/files/documents/national-greenhouse-account-factors-2025.docx",
            "availability": "public_but_cloud_runner_connection_resets",
            "note": "Official accessible document"
        },
    ])
    link_path = out_dir / "dcceew_nga_factors_2025_official_links.csv"
    links.to_csv(link_path, index=False)
    b.record_file(
        "DCCEEW 2025 official attachment register",
        links.iloc[0]["url"], link_path, status="derived", n_records=len(links),
        note="Official raw-document URLs retained for manual/local retrieval and provenance")
    return True


def official_documents_v6() -> None:
    direct = [
        (
            "ABS regional population SA2",
            "https://www.abs.gov.au/statistics/people/population/regional-population/2024-25/32180DS0003_2001-25.xlsx",
            b.RAW / "demographics/abs_population/32180DS0003_2001-25.xlsx",
            100_000,
            False,
        ),
        (
            "ABS 2021 SA2 boundaries",
            "https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/edition-3-july-2021-june-2026/access-and-downloads/digital-boundary-files/SA2_2021_AUST_SHP_GDA2020.zip",
            b.RAW / "demographics/abs_boundaries/SA2_2021_AUST_SHP_GDA2020.zip",
            10_000_000,
            True,
        ),
        (
            "ABS 2021 Census NSW SA2 DataPack",
            "https://www.abs.gov.au/census/find-census-data/datapacks/download/2021_GCP_SA2_for_NSW_short-header.zip",
            b.RAW / "demographics/abs_census/2021_GCP_SA2_for_NSW_short-header.zip",
            5_000_000,
            True,
        ),
    ]
    for dataset, url, out, minimum, is_zip in direct:
        ok = b.download(url, out, dataset, min_bytes=minimum, validate_zip=is_zip)
        b.checks.append({"check": dataset, "passed": ok, "value": int(ok), "detail": str(out)})

    nga_ok = _install_model_ready_nga_factors()
    b.checks.append({
        "check": "DCCEEW 2025 model-ready emission factors",
        "passed": nga_ok,
        "value": int(nga_ok),
        "detail": "Official values with table/page/unit provenance; raw public links retained",
    })

    before = len(b.checks)
    b.download_matching_documents(
        "CSIRO GenCost",
        "https://www.csiro.au/en/research/technology-space/energy/electricity-transition/gencost",
        [r"gencost.*\.pdf", r"gencost.*\.xlsx"],
        b.RAW / "economics/csiro_gencost",
    )
    assert len(b.checks) > before

    # Supplementary sources are attempted but are not core blockers because GenCost and
    # the verified NGA extract supply the modelling inputs required for this study.
    aemo_page = "https://www.aemo.com.au/energy-systems/major-publications/integrated-system-plan-isp/2026-integrated-system-plan-isp/2025-26-inputs-assumptions-and-scenarios"
    v5._browser_attachment(
        aemo_page,
        r"2025 Inputs and Assumptions Workbook",
        b.RAW / "economics/aemo/2025_inputs_and_assumptions_workbook.xlsm",
        "AEMO inputs and assumptions (supplementary)",
        5_000_000,
    )
    petroleum_page = "https://www.energy.gov.au/publications/australian-petroleum-statistics-2026"
    v5._browser_attachment(
        petroleum_page,
        r"Data Extract May 2026",
        b.RAW / "fuel/australian_petroleum_statistics_may_2026.xlsx",
        "Australian Petroleum Statistics (supplementary)",
        1_000_000,
    )


b.official_economic_emissions_and_demographic_documents = official_documents_v6


if __name__ == "__main__":
    b.main()
