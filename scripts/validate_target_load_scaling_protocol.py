from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "model_ready_inputs/target_load_scaling_protocol.yaml"
OUT = ROOT / "model_ready_validation/target_load_scaling_protocol_validation.json"
OUT.parent.mkdir(exist_ok=True)

required_phrases = [
    "absolute_MW_values_transferred: false",
    "network-capacity-implied peak-demand proxy",
    "n_1_nameplate_capacity - available_capacity_load_at_n_1",
    "radial_or_nonfirm",
    "pseudo_target_validation",
    "measured target-site load",
    "at least 100 annual profiles per target site",
    "ChatGPT approves",
]
errors = []
if not PROTOCOL.exists():
    errors.append(f"missing protocol: {PROTOCOL}")
    text = ""
else:
    text = PROTOCOL.read_text(encoding="utf-8")
    for phrase in required_phrases:
        if phrase not in text:
            errors.append(f"missing mandatory phrase: {phrase!r}")

report = {
    "status": "PASS" if not errors else "FAIL",
    "file": str(PROTOCOL.relative_to(ROOT)) if PROTOCOL.exists() else str(PROTOCOL),
    "bytes": PROTOCOL.stat().st_size if PROTOCOL.exists() else 0,
    "errors": errors,
    "scientific_rule": (
        "Absolute Ausgrid demand is never transferred; target scale requires an official-network-attribute proxy, "
        "independent statistical plausibility bounds and pseudo-target validation."
    ),
}
OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
sys.exit(0 if not errors else 2)
