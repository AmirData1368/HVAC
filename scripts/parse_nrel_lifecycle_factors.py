from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
WORKBOOK = ROOT / "model_ready_inputs/reference/NREL_EF_Table_FINAL.xlsx"
OUT = ROOT / "model_ready_inputs"
OUT.mkdir(parents=True, exist_ok=True)


def numeric(value):
    try:
        number = float(value)
        return number if np.isfinite(number) else np.nan
    except (TypeError, ValueError):
        return np.nan


def clean_text(value) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def slug(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_")


def main() -> None:
    if not WORKBOOK.exists():
        raise FileNotFoundError(WORKBOOK)
    raw = pd.read_excel(WORKBOOK, sheet_name="EF_Table", header=None)
    if raw.shape[1] < 27:
        raise RuntimeError(f"Unexpected NREL workbook schema: {raw.shape}")

    rows = []
    category = ""
    for row_index in range(3, len(raw)):
        row = raw.iloc[row_index]
        if clean_text(row.iloc[0]):
            category = clean_text(row.iloc[0])
        technology = clean_text(row.iloc[1])
        if not technology:
            continue
        total = [numeric(row.iloc[i]) for i in range(22, 27)]
        if all(np.isnan(x) for x in total):
            continue
        tail = [clean_text(row.iloc[i]) for i in range(27, raw.shape[1])]
        source_text = " | ".join(x for x in tail if x)
        rows.append({
            "factor_id": "NREL_LCA_" + slug(technology),
            "category": category,
            "technology": technology,
            "min_g_co2e_per_kwh": total[0],
            "q1_g_co2e_per_kwh": total[1],
            "median_g_co2e_per_kwh": total[2],
            "q3_g_co2e_per_kwh": total[3],
            "max_g_co2e_per_kwh": total[4],
            "functional_unit": "g CO2-e per kWh of electricity generated",
            "scope": "total life cycle across upstream, operation and downstream as represented in source harmonisation dataset",
            "source_type": "direct_official_dataset",
            "source_publisher": "NREL/National Laboratory of the Rockies",
            "source_title": "Life Cycle Greenhouse Gas Emissions from Electricity Generation: Update",
            "source_doi": "10.7799/1819907",
            "source_catalogue_url": "https://data.nlr.gov/submissions/171",
            "source_workbook_url": "https://data.nlr.gov/system/files/171/EF_Table_FINAL.xlsx",
            "source_workbook_sheet": "EF_Table",
            "source_workbook_row": row_index + 1,
            "source_reference_and_notes": source_text,
        })

    complete = pd.DataFrame(rows)
    if complete.empty or complete.factor_id.duplicated().any():
        raise RuntimeError("Lifecycle table is empty or factor IDs are duplicated")
    complete.to_csv(OUT / "technology_lifecycle_emissions_all_nrel.csv", index=False)

    patterns = {
        "solar_photovoltaic": r"^Photovoltaic \(All Technologies\)$",
        "solar_photovoltaic_crystalline": r"^Photovoltaic - Crystalline Silicon \(All Technologies\)$",
        "wind": r"^Wind \(All Technologies\)$",
        "wind_land_based": r"^(Land[- ]based Wind|Onshore Wind)$",
        "battery_lithium_ion": r"(Lithium.?Ion|Li.?Ion).*(Battery|Storage)|(Battery|Storage).*(Lithium.?Ion|Li.?Ion)",
        "biopower_direct_combustion": r"^Direct Combustion$",
        "biopower_gasification": r"^Gasification$",
        "biopower_gasification_engine": r"^Gasification Engine$",
    }
    selected_parts = []
    missing = []
    for model_key, pattern in patterns.items():
        matches = complete[complete.technology.str.contains(pattern, case=False, regex=True, na=False)].copy()
        if matches.empty:
            missing.append({"model_key": model_key, "pattern": pattern})
        else:
            matches.insert(0, "model_key", model_key)
            selected_parts.append(matches)
    selected = pd.concat(selected_parts, ignore_index=True) if selected_parts else pd.DataFrame()
    selected.to_csv(OUT / "technology_lifecycle_emissions.csv", index=False)

    audit = {
        "status": "PASS",
        "workbook": str(WORKBOOK.relative_to(ROOT)),
        "workbook_shape": list(raw.shape),
        "parsed_technology_rows": len(complete),
        "selected_rows": len(selected),
        "selected_technologies": selected[["model_key", "technology"]].to_dict("records") if not selected.empty else [],
        "missing_optional_patterns": missing,
        "double_counting_rule": (
            "NREL total-lifecycle factors are a separate attributional accounting scenario. "
            "They must not be added to overlapping DCCEEW operational fuel factors without an explicit non-overlapping boundary reconciliation."
        ),
    }
    (OUT / "technology_lifecycle_emissions_audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print(json.dumps(audit, indent=2))


if __name__ == "__main__":
    main()
