from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score, silhouette_score
from sklearn.preprocessing import RobustScaler, StandardScaler

from site_screen_cluster import select_representatives

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "site_selection/results"
RANDOM_SEED = 42

CENTRAL_FEATURES = [
    "ghi_kwh_m2_day", "ghi_daily_cv", "wind50_mean_ms", "wind50_p90_ms",
    "wind50_cv", "temp_mean_c", "temp_p95_c", "resource_complementarity",
    "cooling_degree_hours_24c_per_year", "crop_dry_tpy_50km", "manure_vs_tpy_50km",
    "organic_waste_tpy_50km", "forestry_dry_tpy_50km", "population_2021_50km",
    "population_density",
]
GROUPS = {
    "solar": ["ghi_kwh_m2_day", "ghi_daily_cv", "resource_complementarity"],
    "wind": ["wind50_mean_ms", "wind50_p90_ms", "wind50_cv"],
    "temperature": ["temp_mean_c", "temp_p95_c", "cooling_degree_hours_24c_per_year"],
    "biomass": ["crop_dry_tpy_50km", "manure_vs_tpy_50km", "organic_waste_tpy_50km", "forestry_dry_tpy_50km"],
    "population": ["population_2021_50km", "population_density"],
}


def prepare(frame: pd.DataFrame, columns: list[str], scaler: str = "robust") -> np.ndarray:
    data = frame[columns].copy()
    for column in data:
        if "_tpy_" in column or column.startswith("population"):
            data[column] = np.log1p(data[column].clip(lower=0))
    data = data.fillna(data.median(numeric_only=True))
    transformer = RobustScaler() if scaler == "robust" else StandardScaler()
    return transformer.fit_transform(data)


def replace_radius(columns: list[str], radius: int) -> list[str]:
    output = []
    for column in columns:
        if column.endswith("_50km"):
            output.append(column[:-5] + f"_{radius}km")
        else:
            output.append(column)
    return output


def main() -> None:
    frame = pd.read_csv(RESULTS / "site_feature_matrix.csv")
    comparison = pd.read_csv(RESULTS / "cluster_model_comparison.csv")
    k = int(comparison.sort_values("composite_cluster_score", ascending=False).iloc[0].k)
    central_labels = frame.cluster.astype(int).to_numpy()
    scenarios = [
        ("central_repeated", CENTRAL_FEATURES, "robust"),
        ("catchment_25km", replace_radius(CENTRAL_FEATURES, 25), "robust"),
        ("catchment_100km", replace_radius(CENTRAL_FEATURES, 100), "robust"),
        ("standard_scaler", CENTRAL_FEATURES, "standard"),
    ]
    for group, remove in GROUPS.items():
        scenarios.append((f"leave_out_{group}", [column for column in CENTRAL_FEATURES if column not in remove], "robust"))

    rows, selected_counts = [], {site: 0 for site in frame.site_name}
    scenario_selected = {}
    for name, columns, scaler in scenarios:
        matrix = prepare(frame, columns, scaler)
        labels = KMeans(n_clusters=k, random_state=RANDOM_SEED, n_init=100).fit_predict(matrix)
        representative = select_representatives(frame.drop(columns=["cluster"], errors="ignore"), matrix, labels, k)
        selected = representative.loc[representative.selected_for_detailed_study, "site_name"].tolist()
        scenario_selected[name] = selected
        for site in selected:
            selected_counts[site] += 1
        rows.append({
            "scenario": name,
            "feature_count": len(columns),
            "scaler": scaler,
            "adjusted_rand_index_vs_central": adjusted_rand_score(central_labels, labels),
            "silhouette": silhouette_score(matrix, labels),
            "selected_site_count": len(selected),
            "selected_sites": "; ".join(selected),
        })

    results = pd.DataFrame(rows)
    results.to_csv(RESULTS / "site_selection_sensitivity.csv", index=False)
    frequency = pd.DataFrame({
        "site_name": list(selected_counts),
        "selection_count": list(selected_counts.values()),
    })
    frequency["selection_frequency"] = frequency.selection_count / len(scenarios)
    frequency = frequency.merge(
        frame[["site_name", "cluster", "selected_for_detailed_study", "selection_reason"]],
        on="site_name", how="left",
    ).sort_values(["selection_frequency", "selected_for_detailed_study"], ascending=False)
    frequency.to_csv(RESULTS / "site_selection_frequency.csv", index=False)

    noncentral = results[results.scenario != "central_repeated"]
    central_frequency = frequency[frequency.selected_for_detailed_study].selection_frequency
    checks = [
        {
            "check": "central_reproduction",
            "passed": bool(results.loc[results.scenario == "central_repeated", "adjusted_rand_index_vs_central"].iloc[0] == 1.0),
        },
        {
            "check": "median_partition_stability",
            "passed": bool(noncentral.adjusted_rand_index_vs_central.median() >= 0.50),
            "value": float(noncentral.adjusted_rand_index_vs_central.median()),
        },
        {
            "check": "minimum_partition_stability",
            "passed": bool(noncentral.adjusted_rand_index_vs_central.min() >= 0.20),
            "value": float(noncentral.adjusted_rand_index_vs_central.min()),
        },
        {
            "check": "central_shortlist_frequency",
            "passed": bool(central_frequency.median() >= 0.40),
            "value": float(central_frequency.median()),
        },
    ]
    status = "PASS" if all(check["passed"] for check in checks) else "REVIEW_REQUIRED"
    audit = {
        "status": status,
        "best_k": k,
        "scenario_count": len(scenarios),
        "checks": checks,
        "scenario_selected_sites": scenario_selected,
        "interpretation": (
            "ARI quantifies partition stability, while site-selection frequency tests whether individual representatives remain preferred. "
            "Low stability does not justify hiding alternatives; it requires a broader or explicitly conditional detailed-study shortlist."
        ),
    }
    (RESULTS / "site_selection_sensitivity_audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print(json.dumps(audit, indent=2))


if __name__ == "__main__":
    main()
