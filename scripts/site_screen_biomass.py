from __future__ import annotations

from pathlib import Path
from typing import Iterable

import geopandas as gpd
import pandas as pd

from site_screen_common import normalise_name, safe_float

CROP_DETAIL_LAYER_IDS = {1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15, 17, 18}
CATCHMENT_RADII_KM = (25, 50, 100)


def aggregate_crop(directory: Path) -> pd.DataFrame:
    totals, names = {}, {}
    for path in sorted(directory.glob("*attributes.csv")):
        if int(path.name.split("_", 1)[0]) not in CROP_DETAIL_LAYER_IDS:
            continue
        frame = pd.read_csv(path)
        for _, row in frame.iterrows():
            name = str(row.get("REGION_NAME", "")).strip()
            if not name:
                continue
            key = normalise_name(name)
            value = safe_float(row.get("TOTAL_RESIDUES"))
            if value == 0:
                value = sum(safe_float(row[column]) for column in frame.columns if column.endswith("_AVG_0_MC"))
            totals[key] = totals.get(key, 0.0) + value
            names[key] = name
    return pd.DataFrame({
        "region_key": list(totals), "source_region_name": [names[key] for key in totals],
        "crop_dry_tpy": [totals[key] for key in totals],
    })


def aggregate_livestock(directory: Path) -> pd.DataFrame:
    totals, names = {}, {}
    priorities = ("VS_TOTAL_AVG_0_MC", "VS_YARD_AVG_0_MC", "VS_AVG_0_MC")
    for path in sorted(directory.glob("*attributes.csv")):
        frame = pd.read_csv(path)
        selected = next((column for column in priorities if column in frame.columns), None)
        if selected is None:
            raise RuntimeError(f"No total volatile-solids field found in {path}")
        for _, row in frame.iterrows():
            name = str(row.get("REGION_NAME", "")).strip()
            if not name:
                continue
            key = normalise_name(name)
            totals[key] = totals.get(key, 0.0) + safe_float(row.get(selected))
            names[key] = name
    return pd.DataFrame({
        "region_key": list(totals), "source_region_name": [names[key] for key in totals],
        "manure_vs_tpy": [totals[key] for key in totals],
    })


def aggregate_waste(directory: Path) -> pd.DataFrame:
    frame = pd.read_csv(next(directory.glob("00_*attributes.csv")))
    output = frame[["REGION_NAME", "TOTAL_RESIDUES"]].copy()
    output["region_key"] = output.REGION_NAME.map(normalise_name)
    return output.rename(columns={
        "REGION_NAME": "source_region_name", "TOTAL_RESIDUES": "organic_waste_tpy",
    })[["region_key", "source_region_name", "organic_waste_tpy"]]


def aggregate_forestry(directory: Path) -> pd.DataFrame:
    totals, names = {}, {}
    for path in sorted(directory.glob("*attributes.csv")):
        frame = pd.read_csv(path)
        for _, row in frame.iterrows():
            name = str(row.get("REGION_NAME", "")).strip()
            if not name:
                continue
            key = normalise_name(name)
            value = safe_float(row.get("TOTAL_RESIDUES"))
            if value == 0:
                components = [
                    column for column in frame.columns
                    if column.endswith("_0_MC") and not column.startswith("TOTAL_")
                ]
                value = sum(safe_float(row[column]) for column in components)
            totals[key] = totals.get(key, 0.0) + value
            names[key] = name
    return pd.DataFrame({
        "region_key": list(totals), "source_region_name": [names[key] for key in totals],
        "forestry_dry_tpy": [totals[key] for key in totals],
    })


def repair_geometry(frame: gpd.GeoDataFrame, label: str) -> gpd.GeoDataFrame:
    repaired = frame.copy()
    invalid_before = int((~repaired.geometry.is_valid).sum())
    repaired.geometry = repaired.geometry.make_valid()
    repaired = repaired[repaired.geometry.notna() & ~repaired.geometry.is_empty].copy()
    invalid_after = int((~repaired.geometry.is_valid).sum())
    if invalid_after:
        raise RuntimeError(f"{label}: {invalid_after} geometries remain invalid after make_valid")
    repaired.attrs["invalid_before_repair"] = invalid_before
    return repaired


def join_geometry_and_values(path: Path, values: Iterable[pd.DataFrame]) -> gpd.GeoDataFrame:
    geometry = repair_geometry(gpd.read_file(path), path.name)
    geometry["region_key"] = geometry.REGION_NAME.map(normalise_name)
    for frame in values:
        value_columns = [column for column in frame if column not in {"region_key", "source_region_name"}]
        geometry = geometry.merge(frame[["region_key", *value_columns]], on="region_key", how="left")
    numeric = [column for column in geometry if column.endswith("_tpy")]
    geometry[numeric] = geometry[numeric].fillna(0.0)
    return geometry


def area_weighted_catchments(
    sites: gpd.GeoDataFrame,
    abs_sa2: gpd.GeoDataFrame,
    biomass_regions: gpd.GeoDataFrame,
    forestry_regions: gpd.GeoDataFrame,
) -> pd.DataFrame:
    crs = "EPSG:3577"
    sites = sites.to_crs(crs)
    abs_sa2 = repair_geometry(abs_sa2.to_crs(crs), "ABS SA2")
    biomass_regions = repair_geometry(biomass_regions.to_crs(crs), "biomass source regions")
    forestry_regions = repair_geometry(forestry_regions.to_crs(crs), "forestry source regions")
    for frame in (abs_sa2, biomass_regions, forestry_regions):
        frame["polygon_area_m2"] = frame.geometry.area
        if (frame.polygon_area_m2 <= 0).any():
            raise RuntimeError("Source-region geometry includes zero-area polygons")

    rows = []
    for site in sites.itertuples(index=False):
        record = {"site_name": site.site_name}
        for radius in CATCHMENT_RADII_KM:
            buffer = site.geometry.buffer(radius * 1000.0)
            buffer_area = float(buffer.area)
            suffix = f"_{radius}km"

            def allocate(
                regions: gpd.GeoDataFrame,
                columns: list[str],
                distances: bool = False,
                coverage_denominator: float | None = None,
            ):
                subset = regions[regions.intersects(buffer)].copy()
                if subset.empty:
                    values = {column: 0.0 for column in columns}
                    weighted = {
                        column.replace("_tpy", "_weighted_distance_km"): 0.0
                        for column in columns if column.endswith("_tpy")
                    }
                    return values, weighted, 0.0, 0, 0.0
                intersections = subset.geometry.intersection(buffer)
                intersection_area = intersections.area
                source_fraction = (intersection_area / subset.polygon_area_m2).clip(0.0, 1.0)
                values, weighted = {}, {}
                centroid_distance = intersections.centroid.distance(site.geometry) / 1000.0
                for column in columns:
                    allocated = subset[column].fillna(0.0) * source_fraction
                    values[column] = float(allocated.sum())
                    if distances and column.endswith("_tpy"):
                        key = column.replace("_tpy", "_weighted_distance_km")
                        weighted[key] = (
                            float((allocated * centroid_distance).sum() / allocated.sum())
                            if allocated.sum() > 0 else 0.0
                        )
                total_intersection_area = float(intersection_area.sum())
                denominator = coverage_denominator if coverage_denominator and coverage_denominator > 0 else buffer_area
                coverage = min(1.0, total_intersection_area / denominator)
                return values, weighted, coverage, len(subset), total_intersection_area

            population, _, nsw_land_fraction, pop_count, nsw_land_area = allocate(
                abs_sa2, ["population_2021"], coverage_denominator=buffer_area
            )
            biomass, bio_distance, bio_coverage, bio_count, _ = allocate(
                biomass_regions,
                ["crop_dry_tpy", "manure_vs_tpy", "organic_waste_tpy"],
                True,
                coverage_denominator=nsw_land_area,
            )
            forestry, forest_distance, forest_coverage, forest_count, _ = allocate(
                forestry_regions,
                ["forestry_dry_tpy"],
                True,
                coverage_denominator=nsw_land_area,
            )
            for key, value in {
                **population, **biomass, **forestry, **bio_distance, **forest_distance,
            }.items():
                record[key + suffix] = value
            record["nsw_land_fraction_of_buffer" + suffix] = nsw_land_fraction
            record["population_geometry_coverage" + suffix] = 1.0 if nsw_land_area > 0 else 0.0
            record["biomass_geometry_coverage_of_nsw_land" + suffix] = bio_coverage
            record["forestry_management_area_fraction_of_nsw_land" + suffix] = forest_coverage
            record["population_regions_intersected" + suffix] = pop_count
            record["biomass_regions_intersected" + suffix] = bio_count
            record["forestry_regions_intersected" + suffix] = forest_count
        rows.append(record)
    return pd.DataFrame(rows)
