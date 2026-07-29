from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "model_ready_inputs/fuel_and_logistics_parameters.csv"
OUT = ROOT / "model_ready_validation/fuel_logistics_validation.json"
OUT.parent.mkdir(exist_ok=True)

required_columns = {
    "input_id", "parameter", "unit", "low", "central", "high", "price_basis",
    "source_type", "source_name", "implementation_rule", "notes",
}
required_ids = {
    "DIESEL_RETAIL_PRICE", "TRUCK_FUEL_INTENSITY", "TRUCK_PAYLOAD",
    "TRIP_DISTANCE_FACTOR", "NONFUEL_TRANSPORT_COST",
    "FEEDSTOCK_COLLECTION_HANDLING", "FEEDSTOCK_PURCHASE_PRICE",
}
errors, warnings = [], []

if not INPUT.exists():
    errors.append(f"missing file: {INPUT}")
    rows, fields = [], set()
else:
    with INPUT.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        fields = set(reader.fieldnames or [])
        rows = list(reader)
    missing_columns = required_columns - fields
    if missing_columns:
        errors.append(f"missing columns: {sorted(missing_columns)}")

ids = [row.get("input_id", "") for row in rows]
missing_ids = required_ids - set(ids)
if missing_ids:
    errors.append(f"missing input IDs: {sorted(missing_ids)}")
if len(ids) != len(set(ids)):
    errors.append("duplicate input IDs")

for line, row in enumerate(rows, start=2):
    try:
        low, central, high = (float(row[key]) for key in ("low", "central", "high"))
        if not all(math.isfinite(value) for value in (low, central, high)):
            raise ValueError("non-finite value")
        if not low <= central <= high:
            errors.append(f"line {line}: bounds not ordered")
        if low < 0:
            errors.append(f"line {line}: negative logistics input")
    except Exception as exc:
        errors.append(f"line {line}: invalid numeric range: {exc!r}")
    if row.get("source_type") == "derived_from_official":
        if not row.get("source_url", "").startswith("http"):
            errors.append(f"line {line}: official-derived input lacks source URL")
    if row.get("source_type") == "scenario_assumption":
        text = " ".join([row.get("source_locator", ""), row.get("notes", "")]).lower()
        if "sensitivity" not in text and "uncertainty" not in text:
            warnings.append(f"line {line}: scenario assumption lacks explicit sensitivity wording")

by_id = {row["input_id"]: row for row in rows if row.get("input_id")}
try:
    diesel = by_id["DIESEL_RETAIL_PRICE"]
    if diesel["unit"] != "AUD/L":
        errors.append("diesel price unit must remain AUD/L")
    if "2025-2026" not in diesel["price_basis"]:
        errors.append("diesel price must retain mixed-date observation warning")
    truck = by_id["TRUCK_FUEL_INTENSITY"]
    if truck["unit"] != "L/km":
        errors.append("truck fuel-intensity unit must remain L/km")
except Exception as exc:
    errors.append(f"cross-check failed: {exc!r}")

report = {
    "status": "PASS" if not errors else "FAIL",
    "file": str(INPUT.relative_to(ROOT)) if INPUT.exists() else str(INPUT),
    "rows": len(rows),
    "errors": errors,
    "warnings": warnings,
    "scientific_note": (
        "Diesel observations and logistics assumptions are uncertainty anchors, not a fitted stationary distribution."
    ),
}
OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
sys.exit(0 if not errors else 2)
