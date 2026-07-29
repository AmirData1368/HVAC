from __future__ import annotations

import math
import re
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd


def normalise_name(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def safe_float(value: object) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else 0.0
    except (TypeError, ValueError):
        return 0.0


def read_nasa_power_csv(path: Path) -> pd.DataFrame:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    marker = next((i for i, line in enumerate(lines) if "-END HEADER-" in line), None)
    if marker is None:
        raise RuntimeError(f"NASA POWER header terminator missing: {path}")
    return pd.read_csv(path, skiprows=marker + 1).replace(-999, np.nan)


def weather_features(bundle_root: Path, sites: pd.DataFrame) -> pd.DataFrame:
    annual_dir = bundle_root / "raw/nasa_power_hourly/annual"
    output = []
    for row in sites.itertuples(index=False):
        slug = re.sub(r"[^A-Za-z0-9_-]+", "_", str(row.site_name))[:50]
        files = sorted(annual_dir.glob(f"{slug}_{row.latitude:.4f}_{row.longitude:.4f}_*.csv"))
        if not files:
            raise RuntimeError(f"No NASA POWER files found for {row.site_name}")
        data = pd.concat([read_nasa_power_csv(path) for path in files], ignore_index=True)
        data.index = pd.to_datetime(
            dict(year=data.YEAR, month=data.MO, day=data.DY, hour=data.HR), utc=True
        )
        daily = data.resample("D").agg(
            ALLSKY_SFC_SW_DWN=("ALLSKY_SFC_SW_DWN", "sum"),
            ALLSKY_SFC_SW_DNI=("ALLSKY_SFC_SW_DNI", "sum"),
            WS50M=("WS50M", "mean"),
            T2M=("T2M", "mean"),
        )
        ghi = daily.ALLSKY_SFC_SW_DWN / 1000.0
        dni = daily.ALLSKY_SFC_SW_DNI / 1000.0
        correlation = daily[["ALLSKY_SFC_SW_DWN", "WS50M"]].corr().iloc[0, 1]
        output.append(
            {
                "site_name": row.site_name,
                "weather_years": len(files),
                "weather_start_year": int(data.YEAR.min()),
                "weather_end_year": int(data.YEAR.max()),
                "ghi_kwh_m2_day": float(ghi.mean()),
                "ghi_daily_cv": float(ghi.std() / ghi.mean()),
                "dni_kwh_m2_day": float(dni.mean()),
                "wind50_mean_ms": float(data.WS50M.mean()),
                "wind50_p90_ms": float(data.WS50M.quantile(0.90)),
                "wind50_cv": float(data.WS50M.std() / data.WS50M.mean()),
                "temp_mean_c": float(data.T2M.mean()),
                "temp_p95_c": float(data.T2M.quantile(0.95)),
                "cooling_degree_hours_24c_per_year": float(
                    np.maximum(data.T2M - 24.0, 0).sum() / len(files)
                ),
                "solar_wind_daily_corr": float(correlation),
                "resource_complementarity": float(1.0 - abs(correlation)),
            }
        )
    return pd.DataFrame(output)


def load_abs_population_and_boundaries(bundle_root: Path) -> gpd.GeoDataFrame:
    boundary_zip = bundle_root / "raw/demographics/abs_boundaries/SA2_2021_AUST_SHP_GDA2020.zip"
    census_zip = bundle_root / "raw/demographics/abs_census/2021_GCP_SA2_for_NSW_short-header.zip"
    with tempfile.TemporaryDirectory(prefix="nsw_site_screen_") as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(boundary_zip) as archive:
            archive.extractall(tmp_path / "boundaries")
        with zipfile.ZipFile(census_zip) as archive:
            archive.extractall(tmp_path / "census")
        shp = next((tmp_path / "boundaries").rglob("SA2_2021_AUST_GDA2020.shp"))
        census_csv = next((tmp_path / "census").rglob("2021Census_G01_NSW_SA2.csv"))
        boundaries = gpd.read_file(shp)
        boundaries = boundaries[boundaries.STE_CODE21.astype(str) == "1"].copy()
        population = pd.read_csv(census_csv, dtype={"SA2_CODE_2021": str})[
            ["SA2_CODE_2021", "Tot_P_P"]
        ].rename(columns={"Tot_P_P": "population_2021"})
        boundaries.SA2_CODE21 = boundaries.SA2_CODE21.astype(str)
        boundaries = boundaries.merge(
            population, left_on="SA2_CODE21", right_on="SA2_CODE_2021", how="left"
        )
        return boundaries[
            ["SA2_CODE21", "SA2_NAME21", "AREASQKM21", "population_2021", "geometry"]
        ].copy()
