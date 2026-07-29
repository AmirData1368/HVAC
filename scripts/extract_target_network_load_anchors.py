from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd


def normalise(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--shortlist", type=Path, default=Path("site_selection/results/recommended_site_shortlist.csv"))
    parser.add_argument("--output-dir", type=Path, default=Path("load_transfer/target_scaling"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    shortlist = pd.read_csv(args.shortlist)
    secondary_path = (
        args.bundle_root / "raw/nsw_arcgis/essential_energy_uhc_2025/01_Secondary.geojson"
    )
    network = gpd.read_file(secondary_path)
    network["site_key"] = network.substation.map(normalise)
    shortlist["site_key"] = shortlist.site_name.map(normalise)
    selected = network.merge(
        shortlist[["site_key", "site_name", "SA2_NAME21", "cluster", "selection_reason"]],
        on="site_key", how="inner", suffixes=("_network", "_selected")
    )
    expected = set(shortlist.site_key)
    observed = set(selected.site_key)
    if expected != observed:
        raise RuntimeError(f"Selected-site network match failed: missing={sorted(expected-observed)}")

    selected["n_1_nameplate_capacity_mva"] = pd.to_numeric(
        selected.n_1_nameplate_capacity, errors="coerce"
    )
    selected["available_capacity_load_at_n_1_mva"] = pd.to_numeric(
        selected.available_capacity_load_at_n_1, errors="coerce"
    )
    selected["network_capacity_implied_peak_proxy_mw"] = (
        selected.n_1_nameplate_capacity_mva - selected.available_capacity_load_at_n_1_mva
    )
    selected["radial_or_nonfirm_flag"] = selected.n_1_nameplate_capacity_mva <= 0
    selected["proxy_status"] = np.where(
        selected.network_capacity_implied_peak_proxy_mw >= 0,
        "nonnegative_derived_proxy",
        "invalid_negative_proxy",
    )
    columns = [
        "site_name", "SA2_NAME21", "cluster", "selection_reason", "year",
        "voltage_level_primary", "voltage_level_secondary",
        "n_1_nameplate_capacity_mva", "available_capacity_load_at_n_1_mva",
        "available_capacity_generation_a", "network_capacity_implied_peak_proxy_mw",
        "radial_or_nonfirm_flag", "proxy_status", "extract_date", "owner", "dataset",
        "longitude", "latitude", "objectid",
    ]
    selected = selected[columns].sort_values(["site_name", "year"])
    selected.to_csv(args.output_dir / "selected_site_network_capacity_trajectory.csv", index=False)

    summaries = []
    for site_name, group in selected.groupby("site_name"):
        group = group.sort_values("year")
        proxy = group.network_capacity_implied_peak_proxy_mw
        summaries.append({
            "site_name": site_name,
            "SA2_NAME21": group.SA2_NAME21.iloc[0],
            "cluster": int(group.cluster.iloc[0]),
            "selection_reason": group.selection_reason.iloc[0],
            "primary_voltage_kv": float(group.voltage_level_primary.iloc[0]),
            "secondary_voltage_kv": float(group.voltage_level_secondary.iloc[0]),
            "n_1_nameplate_capacity_2025_mva": float(group.loc[group.year == 2025, "n_1_nameplate_capacity_mva"].iloc[0]),
            "available_load_capacity_2025_mva": float(group.loc[group.year == 2025, "available_capacity_load_at_n_1_mva"].iloc[0]),
            "implied_peak_proxy_2025_mw": float(group.loc[group.year == 2025, "network_capacity_implied_peak_proxy_mw"].iloc[0]),
            "implied_peak_proxy_2034_mw": float(group.loc[group.year == 2034, "network_capacity_implied_peak_proxy_mw"].iloc[0]),
            "implied_peak_proxy_min_2025_2034_mw": float(proxy.min()),
            "implied_peak_proxy_max_2025_2034_mw": float(proxy.max()),
            "compound_annual_peak_proxy_growth": float(
                (group.loc[group.year == 2034, "network_capacity_implied_peak_proxy_mw"].iloc[0]
                 / group.loc[group.year == 2025, "network_capacity_implied_peak_proxy_mw"].iloc[0]) ** (1 / 9) - 1
            ) if group.loc[group.year == 2025, "network_capacity_implied_peak_proxy_mw"].iloc[0] > 0 else np.nan,
            "radial_or_nonfirm_flag": bool(group.radial_or_nonfirm_flag.any()),
        })
    summary = pd.DataFrame(summaries).sort_values("cluster")
    summary.to_csv(args.output_dir / "selected_site_network_load_anchor_summary.csv", index=False)

    checks = [
        {"check": "all_selected_sites_matched", "passed": len(summary) == len(shortlist), "value": len(summary)},
        {"check": "ten_year_trajectory_per_site", "passed": bool((selected.groupby("site_name").year.nunique() == 10).all())},
        {"check": "nonnegative_implied_peak", "passed": bool((selected.network_capacity_implied_peak_proxy_mw >= 0).all())},
        {"check": "official_owner", "passed": bool((selected.owner == "Essential Energy").all())},
        {"check": "year_range", "passed": int(selected.year.min()) == 2025 and int(selected.year.max()) == 2034},
    ]
    report = {
        "status": "PASS" if all(check["passed"] for check in checks) else "FAIL",
        "checks": checks,
        "selected_site_count": len(summary),
        "source": str(secondary_path),
        "source_fields": ["n_1_nameplate_capacity", "available_capacity_load_at_n_1", "year"],
        "derived_equation": "network_capacity_implied_peak_proxy = n_1_nameplate_capacity - available_capacity_load_at_n_1",
        "interpretation": (
            "The result is a network-capacity-implied demand proxy derived from official attributes, not measured load. "
            "Sites with zero N-1 nameplate capacity are flagged radial_or_nonfirm and require wider uncertainty."
        ),
        "selected_site_summaries": summary.to_dict("records"),
    }
    (args.output_dir / "target_network_load_anchor_audit.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))
    if report["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
