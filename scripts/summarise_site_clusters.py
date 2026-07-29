from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "site_selection/results"

GROUPS = {
    "solar_resource": ["ghi_kwh_m2_day", "dni_kwh_m2_day"],
    "wind_resource": ["wind50_mean_ms", "wind50_p90_ms"],
    "hot_climate": ["temp_p95_c", "cooling_degree_hours_24c_per_year"],
    "solar_wind_complementarity": ["resource_complementarity"],
    "crop_biomass": ["crop_dry_tpy_50km"],
    "manure_resource": ["manure_vs_tpy_50km"],
    "organic_waste_resource": ["organic_waste_tpy_50km"],
    "forestry_resource": ["forestry_dry_tpy_50km"],
    "population_and_demand_proxy": ["population_2021_50km", "population_density"],
}


def main() -> None:
    matrix = pd.read_csv(RESULTS / "site_feature_matrix.csv")
    shortlist = pd.read_csv(RESULTS / "recommended_site_shortlist.csv")
    cluster_median = matrix.groupby("cluster").median(numeric_only=True)
    score = pd.DataFrame(index=cluster_median.index)
    for group, columns in GROUPS.items():
        values = cluster_median[columns].copy()
        for column in values:
            if column.endswith("_50km") or column.startswith("population"):
                values[column] = np.log1p(values[column].clip(lower=0))
            values[column] = values[column].rank(pct=True)
        score[group] = values.mean(axis=1)

    profiles = []
    for cluster in cluster_median.index:
        ranked = score.loc[cluster].sort_values(ascending=False)
        top = ranked.index[:3].tolist()
        members = matrix[matrix.cluster == cluster].site_name.tolist()
        selected = shortlist[shortlist.cluster == cluster].site_name.tolist()
        profiles.append({
            "cluster": int(cluster),
            "machine_label": " + ".join(top[:2]),
            "primary_characteristic": top[0],
            "secondary_characteristic": top[1],
            "tertiary_characteristic": top[2],
            "member_count": len(members),
            "members": "; ".join(members),
            "selected_representatives": "; ".join(selected),
            **{f"score_{column}": float(score.loc[cluster, column]) for column in score.columns},
            **{
                f"median_{column}": float(cluster_median.loc[cluster, column])
                for column in [
                    "ghi_kwh_m2_day", "wind50_mean_ms", "temp_p95_c",
                    "crop_dry_tpy_50km", "manure_vs_tpy_50km",
                    "organic_waste_tpy_50km", "forestry_dry_tpy_50km",
                    "population_2021_50km",
                ]
            },
        })
    profiles_frame = pd.DataFrame(profiles).sort_values("cluster")
    profiles_frame.to_csv(RESULTS / "cluster_profiles.csv", index=False)

    columns = [
        "site_name", "SA2_NAME21", "longitude", "latitude", "cluster", "selection_reason",
        "ghi_kwh_m2_day", "wind50_mean_ms", "temp_p95_c", "resource_complementarity",
        "crop_dry_tpy_50km", "manure_vs_tpy_50km", "organic_waste_tpy_50km",
        "forestry_dry_tpy_50km", "population_2021_50km",
        "crop_dry_weighted_distance_km_50km", "manure_vs_weighted_distance_km_50km",
        "organic_waste_weighted_distance_km_50km", "forestry_dry_weighted_distance_km_50km",
        "resource_diversity_score", "resilience_challenge_score",
    ]
    shortlist[columns].to_csv(RESULTS / "selected_site_summary.csv", index=False)

    report = {
        "status": "PASS",
        "cluster_count": len(profiles_frame),
        "selected_site_count": len(shortlist),
        "cluster_labels": profiles_frame[["cluster", "machine_label", "selected_representatives"]].to_dict("records"),
        "label_warning": (
            "Machine labels are relative screening descriptors derived from cluster percentile ranks. "
            "They are not causal claims and require ChatGPT approval before manuscript use."
        ),
    }
    (RESULTS / "cluster_profile_audit.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
