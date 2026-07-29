from __future__ import annotations

import argparse
import json
from pathlib import Path

import geopandas as gpd
import pandas as pd

from site_screen_biomass import (
    aggregate_crop,
    aggregate_forestry,
    aggregate_livestock,
    aggregate_waste,
    area_weighted_catchments,
    join_geometry_and_values,
)
from site_screen_cluster import cluster_candidates, select_representatives
from site_screen_common import load_abs_population_and_boundaries, weather_features

CLUSTER_FEATURES = [
    "ghi_kwh_m2_day", "ghi_daily_cv", "wind50_mean_ms", "wind50_p90_ms",
    "wind50_cv", "temp_mean_c", "temp_p95_c", "resource_complementarity",
    "cooling_degree_hours_24c_per_year", "crop_dry_tpy_50km", "manure_vs_tpy_50km",
    "organic_waste_tpy_50km", "forestry_dry_tpy_50km", "population_2021_50km",
    "population_density",
]


def validate(frame: pd.DataFrame, shortlist: pd.DataFrame, k: int) -> dict:
    checks = []

    def add(name: str, passed: bool, detail: str):
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    add("candidate_count", len(frame) >= 20, f"{len(frame)} sites")
    add("weather_coverage", bool((frame.weather_years >= 11).all()), f"minimum={frame.weather_years.min()}")
    critical = [
        "ghi_kwh_m2_day", "wind50_mean_ms", "temp_p95_c",
        "crop_dry_tpy_50km", "manure_vs_tpy_50km", "organic_waste_tpy_50km",
    ]
    add("critical_features_complete", not frame[critical].isna().any().any(), "no missing critical values")
    monotonic = True
    for prefix in [
        "population_2021", "crop_dry_tpy", "manure_vs_tpy",
        "organic_waste_tpy", "forestry_dry_tpy",
    ]:
        monotonic &= bool((frame[f"{prefix}_25km"] <= frame[f"{prefix}_50km"] + 1e-6).all())
        monotonic &= bool((frame[f"{prefix}_50km"] <= frame[f"{prefix}_100km"] + 1e-6).all())
    add("catchment_totals_monotonic", monotonic, "25 km <= 50 km <= 100 km")
    add("all_clusters_represented", shortlist.cluster.nunique() == k, f"{shortlist.cluster.nunique()}/{k}")
    add("shortlist_size", 5 <= len(shortlist) <= 7, f"{len(shortlist)} selected")
    add("no_duplicate_sites", shortlist.site_name.is_unique, "unique site names")
    coverage_column = "biomass_geometry_coverage_of_nsw_land_50km"
    add(
        "biomass_geometry_coverage_of_nsw_land",
        bool((frame[coverage_column] >= 0.90).all()),
        f"minimum={frame[coverage_column].min():.3f}",
    )
    add(
        "candidate_sites_inside_nsw_land",
        bool((frame.nsw_land_fraction_of_buffer_25km > 0).all()),
        f"minimum_25km_land_fraction={frame.nsw_land_fraction_of_buffer_25km.min():.3f}",
    )
    return {"status": "PASS" if all(check["passed"] for check in checks) else "FAIL", "checks": checks}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--geometry-dir", type=Path, default=Path("site_selection/geometry"))
    parser.add_argument("--output-dir", type=Path, default=Path("site_selection/results"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    sites = pd.read_csv(args.bundle_root / "derived/candidate_sites.csv")
    points = gpd.GeoDataFrame(
        sites, geometry=gpd.points_from_xy(sites.longitude, sites.latitude), crs="EPSG:4326"
    )
    weather = weather_features(args.bundle_root, sites)
    abs_sa2 = load_abs_population_and_boundaries(args.bundle_root)
    bio_root = args.bundle_root / "raw/nsw_biomass_attributes"
    biomass = join_geometry_and_values(
        args.geometry_dir / "nsw_biomass_merged_sa2_regions.geojson",
        [
            aggregate_crop(bio_root / "biomass_cropping"),
            aggregate_livestock(bio_root / "biomass_livestock"),
            aggregate_waste(bio_root / "biomass_organic_waste"),
        ],
    )
    forestry = join_geometry_and_values(
        args.geometry_dir / "nsw_forestry_management_areas.geojson",
        [aggregate_forestry(bio_root / "biomass_forestry")],
    )
    catchments = area_weighted_catchments(points, abs_sa2, biomass, forestry)
    frame = sites.merge(weather, on="site_name").merge(catchments, on="site_name")

    containing = gpd.sjoin(points.to_crs(abs_sa2.crs), abs_sa2, how="left", predicate="within")
    containing = containing[
        ["site_name", "SA2_CODE21", "SA2_NAME21", "AREASQKM21", "population_2021"]
    ]
    frame = frame.merge(containing, on="site_name", how="left")
    frame["population_density"] = frame.population_2021 / frame.AREASQKM21

    labels, comparison, best_k, matrix = cluster_candidates(frame[CLUSTER_FEATURES])
    selected = select_representatives(frame, matrix, labels, best_k)
    shortlist = selected[selected.selected_for_detailed_study].copy().sort_values("cluster")
    validation = validate(selected, shortlist, best_k)

    selected.to_csv(args.output_dir / "site_feature_matrix.csv", index=False)
    comparison.to_csv(args.output_dir / "cluster_model_comparison.csv", index=False)
    shortlist.to_csv(args.output_dir / "recommended_site_shortlist.csv", index=False)
    (args.output_dir / "site_selection_validation.json").write_text(
        json.dumps(validation, indent=2), encoding="utf-8"
    )
    summary = {
        "status": validation["status"],
        "best_k": best_k,
        "candidate_sites": len(selected),
        "selected_sites": shortlist[
            ["site_name", "SA2_NAME21", "cluster", "selection_reason"]
        ].to_dict("records"),
        "validation": validation,
        "biomass_method": (
            "Area-weighted allocation of official source-region totals to 25/50/100 km buffers; "
            "uniform within-region density is an explicit proxy assumption; coverage is evaluated "
            "against NSW land inside each buffer rather than ocean or interstate area."
        ),
        "clustering_features": CLUSTER_FEATURES,
        "cluster_algorithms": ["KMeans", "GaussianMixture", "AgglomerativeWard"],
    }
    (args.output_dir / "site_selection_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    if validation["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
