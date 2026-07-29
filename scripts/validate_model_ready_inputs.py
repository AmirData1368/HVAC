from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "model_ready_inputs"
REPORT_DIR = Path(__file__).resolve().parents[1] / "model_ready_validation"
REPORT_DIR.mkdir(exist_ok=True)

REQUIRED = {
    "technology_costs.csv": {"input_id", "parameter", "low", "central", "high", "source_type", "source_url"},
    "battery_parameters.csv": {"input_id", "parameter", "low", "central", "high", "source_type", "implementation_rule"},
    "financial_parameters.csv": {"input_id", "parameter", "low", "central", "high", "source_type"},
    "biomass_conversion_parameters.csv": {"input_id", "feedstock_group", "low", "central", "high", "source_type", "implementation_rule"},
    "resilience_scenarios.csv": {"scenario_id", "duration_hours", "critical_load_fraction", "source_type"},
    "source_register.csv": {"source_id", "publisher", "title", "year", "url"},
    "technology_lifecycle_emissions.csv": {
        "model_key", "factor_id", "technology", "min_g_co2e_per_kwh",
        "q1_g_co2e_per_kwh", "median_g_co2e_per_kwh", "q3_g_co2e_per_kwh",
        "max_g_co2e_per_kwh", "functional_unit", "scope", "source_type",
        "source_doi", "source_catalogue_url", "source_workbook_row",
    },
}

ALLOWED_SOURCE_TYPES = {
    "direct_official",
    "direct_official_range",
    "direct_official_dataset",
    "derived_from_official",
    "scenario_assumption",
    "model_choice",
    "derived_assumption",
    "derived_from_literature",
}

errors: list[str] = []
warnings: list[str] = []
stats: dict[str, object] = {}


def parse_number(value: str):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def read_csv(name: str):
    path = ROOT / name
    if not path.exists():
        errors.append(f"missing file: {name}")
        return [], set()
    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        fields = set(reader.fieldnames or [])
        missing = REQUIRED[name] - fields
        if missing:
            errors.append(f"{name}: missing columns {sorted(missing)}")
        rows = list(reader)
    stats[name] = {"rows": len(rows), "columns": len(fields)}
    return rows, fields


for name in REQUIRED:
    rows, fields = read_csv(name)
    if not rows:
        errors.append(f"{name}: no data rows")
        continue

    if name == "resilience_scenarios.csv":
        id_field = "scenario_id"
    elif name == "source_register.csv":
        id_field = "source_id"
    elif name == "technology_lifecycle_emissions.csv":
        id_field = "model_key"
    else:
        id_field = "input_id"
    ids = [row.get(id_field, "").strip() for row in rows]
    if any(not identifier for identifier in ids):
        errors.append(f"{name}: blank identifiers")
    if len(ids) != len(set(ids)):
        errors.append(f"{name}: duplicate identifiers")

    if name == "source_register.csv":
        for line, row in enumerate(rows, start=2):
            if not row.get("url", "").strip().startswith("http"):
                errors.append(f"{name}:{line}: invalid URL")
        continue

    for line, row in enumerate(rows, start=2):
        source_type = row.get("source_type", "").strip()
        if source_type not in ALLOWED_SOURCE_TYPES:
            errors.append(f"{name}:{line}: unsupported source_type {source_type!r}")
        if source_type in {
            "direct_official", "direct_official_range", "direct_official_dataset",
            "derived_from_official", "derived_from_literature",
        }:
            url = row.get("source_url", "") or row.get("source_catalogue_url", "")
            if not url.strip().startswith("http"):
                errors.append(f"{name}:{line}: sourced value lacks an HTTP source URL")
        if source_type == "scenario_assumption":
            text = " ".join([
                row.get("source_name", ""), row.get("source_locator", ""),
                row.get("method_source", ""), row.get("notes", ""),
            ]).lower()
            if not any(word in text for word in ("sensitivity", "stress", "uncertainty")):
                warnings.append(
                    f"{name}:{line}: scenario assumption should explicitly require sensitivity/stress testing"
                )

        if {"low", "central", "high"}.issubset(fields):
            low, central, high = (parse_number(row.get(key, "")) for key in ("low", "central", "high"))
            numeric_count = sum(value is not None for value in (low, central, high))
            if numeric_count == 3 and not (low <= central <= high):
                errors.append(f"{name}:{line}: numeric bounds not ordered ({low}, {central}, {high})")
            elif numeric_count not in (0, 3):
                errors.append(f"{name}:{line}: low/central/high must be all numeric or all text")

        if name == "technology_lifecycle_emissions.csv":
            values = [
                parse_number(row.get(column, ""))
                for column in (
                    "min_g_co2e_per_kwh", "q1_g_co2e_per_kwh", "median_g_co2e_per_kwh",
                    "q3_g_co2e_per_kwh", "max_g_co2e_per_kwh",
                )
            ]
            if any(value is None for value in values):
                errors.append(f"{name}:{line}: lifecycle quintile fields must all be numeric")
            elif not (values[0] <= values[1] <= values[2] <= values[3] <= values[4]):
                errors.append(f"{name}:{line}: lifecycle distribution is not ordered")
            if row.get("functional_unit") != "g CO2-e per kWh of electricity generated":
                errors.append(f"{name}:{line}: functional unit changed unexpectedly")
            if row.get("source_doi") != "10.7799/1819907":
                errors.append(f"{name}:{line}: NREL DOI provenance failed")

for yaml_name, required_phrases in {
    "load_transfer_protocol.yaml": [
        "forbidden_manuscript_terms",
        "measured demand of the selected Essential Energy site",
        "pseudo-target",
        "rolling_origin_only",
    ],
    "validation_protocol.yaml": [
        "energy_balance_relative_tolerance",
        "NREL_BLAST_Lite_LFP",
        "leave_one_site_out",
        "pareto_candidates_exact_resimulation_fraction: 1.0",
    ],
    "emissions_accounting_protocol.yaml": [
        "operational_boundary",
        "attributional_lifecycle_sensitivity",
        "consequential_waste_diversion",
        "no energy flow may receive both",
        "battery lifecycle factor is applied only to discharged electricity",
    ],
}.items():
    path = ROOT / yaml_name
    if not path.exists():
        errors.append(f"missing file: {yaml_name}")
        continue
    text = path.read_text(encoding="utf-8")
    stats[yaml_name] = {"bytes": path.stat().st_size}
    for phrase in required_phrases:
        if phrase not in text:
            errors.append(f"{yaml_name}: missing mandatory phrase {phrase!r}")

try:
    with (ROOT / "battery_parameters.csv").open(newline="", encoding="utf-8") as file:
        battery = {row["input_id"]: row for row in csv.DictReader(file)}
    rte = float(battery["BAT_RTE"]["central"])
    eta_charge = float(battery["BAT_CHARGE_EFF"]["central"])
    eta_discharge = float(battery["BAT_DISCHARGE_EFF"]["central"])
    if abs(eta_charge * eta_discharge - rte) > 0.02:
        errors.append("battery central charge/discharge efficiencies are inconsistent with round-trip efficiency")
    soc_min = float(battery["BAT_SOC_MIN"]["central"])
    soc_max = float(battery["BAT_SOC_MAX"]["central"])
    if not 0 <= soc_min < soc_max <= 1:
        errors.append("battery SOC bounds invalid")
except Exception as exc:
    errors.append(f"battery cross-check failed: {exc!r}")

try:
    with (ROOT / "resilience_scenarios.csv").open(newline="", encoding="utf-8") as file:
        resilience = list(csv.DictReader(file))
    durations = {int(float(row["duration_hours"])) for row in resilience if row["category"] == "grid_outage"}
    critical = {float(row["critical_load_fraction"]) for row in resilience if row["category"] == "grid_outage"}
    if not {24, 72, 168}.issubset(durations):
        errors.append("resilience grid-outage scenarios must include 24, 72 and 168 hours")
    if not {0.5, 0.75, 1.0}.issubset(critical):
        errors.append("resilience scenarios must include 50%, 75% and 100% critical-load cases")
except Exception as exc:
    errors.append(f"resilience cross-check failed: {exc!r}")

try:
    with (ROOT / "technology_lifecycle_emissions.csv").open(newline="", encoding="utf-8") as file:
        lifecycle = {row["model_key"]: row for row in csv.DictReader(file)}
    required_keys = {
        "solar_photovoltaic", "wind", "battery_lithium_ion",
        "biopower_direct_combustion", "biopower_gasification", "biopower_gasification_engine",
    }
    missing = required_keys - set(lifecycle)
    if missing:
        errors.append(f"lifecycle factors missing model keys: {sorted(missing)}")
except Exception as exc:
    errors.append(f"lifecycle-factor cross-check failed: {exc!r}")

report = {
    "status": "PASS" if not errors else "FAIL",
    "root": str(ROOT),
    "statistics": stats,
    "errors": errors,
    "warnings": warnings,
}
(REPORT_DIR / "model_ready_input_validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
sys.exit(0 if not errors else 2)
