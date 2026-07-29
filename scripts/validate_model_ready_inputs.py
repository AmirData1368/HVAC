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
}

ALLOWED_SOURCE_TYPES = {
    "direct_official",
    "direct_official_range",
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
        x = float(value)
        if not math.isfinite(x):
            return None
        return x
    except (TypeError, ValueError):
        return None


def read_csv(name: str):
    path = ROOT / name
    if not path.exists():
        errors.append(f"missing file: {name}")
        return [], []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
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

    id_field = "scenario_id" if name == "resilience_scenarios.csv" else "source_id" if name == "source_register.csv" else "input_id"
    ids = [r.get(id_field, "").strip() for r in rows]
    if any(not x for x in ids):
        errors.append(f"{name}: blank identifiers")
    if len(ids) != len(set(ids)):
        errors.append(f"{name}: duplicate identifiers")

    if name != "source_register.csv":
        for i, row in enumerate(rows, start=2):
            st = row.get("source_type", "").strip()
            if st not in ALLOWED_SOURCE_TYPES:
                errors.append(f"{name}:{i}: unsupported source_type {st!r}")
            if st in {"direct_official", "direct_official_range", "derived_from_official", "derived_from_literature"}:
                if not row.get("source_url", "").strip().startswith("http"):
                    errors.append(f"{name}:{i}: sourced value lacks an http source_url")
            if st == "scenario_assumption":
                text = " ".join([row.get("source_name", ""), row.get("source_locator", ""), row.get("method_source", ""), row.get("notes", "")]).lower()
                if "sensitivity" not in text and "stress" not in text and "uncertainty" not in text:
                    warnings.append(f"{name}:{i}: scenario assumption should explicitly require sensitivity/stress testing")

            if {"low", "central", "high"}.issubset(fields):
                low, central, high = (parse_number(row.get(k, "")) for k in ("low", "central", "high"))
                numeric_count = sum(x is not None for x in (low, central, high))
                if numeric_count == 3 and not (low <= central <= high):
                    errors.append(f"{name}:{i}: numeric bounds not ordered ({low}, {central}, {high})")
                elif numeric_count not in (0, 3):
                    errors.append(f"{name}:{i}: low/central/high must be all numeric or all text")
    else:
        for i, row in enumerate(rows, start=2):
            if not row.get("url", "").strip().startswith("http"):
                errors.append(f"{name}:{i}: invalid URL")

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
    with (ROOT / "battery_parameters.csv").open(newline="", encoding="utf-8") as f:
        battery = {r["input_id"]: r for r in csv.DictReader(f)}
    rte = float(battery["BAT_RTE"]["central"])
    eta_c = float(battery["BAT_CHARGE_EFF"]["central"])
    eta_d = float(battery["BAT_DISCHARGE_EFF"]["central"])
    if abs(eta_c * eta_d - rte) > 0.02:
        errors.append("battery central charge/discharge efficiencies are inconsistent with round-trip efficiency")
    soc_min = float(battery["BAT_SOC_MIN"]["central"])
    soc_max = float(battery["BAT_SOC_MAX"]["central"])
    if not 0 <= soc_min < soc_max <= 1:
        errors.append("battery SOC bounds invalid")
except Exception as exc:
    errors.append(f"battery cross-check failed: {exc!r}")

try:
    with (ROOT / "resilience_scenarios.csv").open(newline="", encoding="utf-8") as f:
        resilience = list(csv.DictReader(f))
    durations = {int(float(r["duration_hours"])) for r in resilience if r["category"] == "grid_outage"}
    critical = {float(r["critical_load_fraction"]) for r in resilience if r["category"] == "grid_outage"}
    if not {24, 72, 168}.issubset(durations):
        errors.append("resilience grid-outage scenarios must include 24, 72 and 168 hours")
    if not {0.5, 0.75, 1.0}.issubset(critical):
        errors.append("resilience scenarios must include 50%, 75% and 100% critical-load cases")
except Exception as exc:
    errors.append(f"resilience cross-check failed: {exc!r}")

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
