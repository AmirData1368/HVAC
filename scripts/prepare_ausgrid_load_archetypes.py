from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

INTERVAL_MINUTES = 15
EXPECTED_INTERVALS = 96
SOURCE_CLOCK = "AEST in winter / AEDT in summer, local operational clock"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalise_station(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value).strip())
    text = re.sub(r"\bFY\s*20?\d{2}\b", "", text, flags=re.I)
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def parse_date(series: pd.Series) -> pd.Series:
    text = series.astype(str).str.strip()
    result = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    formats = ["%d%b%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%b-%y"]
    for fmt in formats:
        mask = result.isna()
        if not mask.any():
            break
        result.loc[mask] = pd.to_datetime(text.loc[mask], format=fmt, errors="coerce")
    mask = result.isna()
    if mask.any():
        result.loc[mask] = pd.to_datetime(text.loc[mask], dayfirst=True, errors="coerce")
    return result


def interval_minute(label: str) -> int | None:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", str(label).strip())
    if not match:
        return None
    hour, minute = map(int, match.groups())
    if hour == 24 and minute == 0:
        return 1440
    if 0 <= hour <= 23 and minute in {0, 15, 30, 45}:
        value = hour * 60 + minute
        return value if value > 0 else None
    return None


def robust_outlier_count(values: np.ndarray) -> int:
    finite = values[np.isfinite(values)]
    if finite.size < 20:
        return 0
    median = np.median(finite)
    mad = np.median(np.abs(finite - median))
    if mad <= 0:
        return int(np.sum(np.abs(finite - median) > max(abs(median) * 2.0, 1.0)))
    robust_z = 0.6745 * (finite - median) / mad
    return int(np.sum(np.abs(robust_z) > 12.0))


def daily_record(
    station: str,
    station_key: str,
    financial_year: int,
    local_date: pd.Timestamp,
    row_values: np.ndarray,
    source_file: str,
) -> tuple[dict, dict | None]:
    finite = np.isfinite(row_values)
    count = int(finite.sum())
    missing_fraction = 1.0 - count / EXPECTED_INTERVALS
    negatives = int(np.sum(row_values[finite] < 0)) if count else 0
    zeros = int(np.sum(row_values[finite] == 0)) if count else 0
    base = {
        "station_name": station,
        "station_key": station_key,
        "financial_year": financial_year,
        "local_operational_date": local_date.date().isoformat() if pd.notna(local_date) else "",
        "weekday": int(local_date.dayofweek) if pd.notna(local_date) else -1,
        "is_weekend": bool(local_date.dayofweek >= 5) if pd.notna(local_date) else False,
        "month": int(local_date.month) if pd.notna(local_date) else -1,
        "source_clock": SOURCE_CLOCK,
        "interval_count": count,
        "missing_fraction": missing_fraction,
        "negative_interval_count": negatives,
        "zero_interval_count": zeros,
        "source_file": source_file,
    }
    if count == 0:
        base.update({
            "daily_energy_mwh": np.nan,
            "mean_mw": np.nan,
            "peak_mw": np.nan,
            "minimum_mw": np.nan,
            "load_factor": np.nan,
            "p95_absolute_ramp_mw": np.nan,
            "peak_interval_end_minute": np.nan,
            "night_energy_share": np.nan,
            "morning_energy_share": np.nan,
            "daytime_energy_share": np.nan,
            "evening_energy_share": np.nan,
            "usable_for_shape": False,
        })
        return base, None

    raw = row_values.astype(float)
    usable = count >= 92 and negatives == 0 and np.nanmax(raw) > 0
    interpolated = pd.Series(raw).interpolate(limit_direction="both", limit=4).to_numpy()
    if usable and np.isfinite(interpolated).all():
        profile = interpolated / np.mean(interpolated)
    else:
        profile = None
    peak = float(np.nanmax(raw))
    mean = float(np.nanmean(raw))
    energy = float(np.nansum(raw) * INTERVAL_MINUTES / 60.0)
    ramps = np.abs(np.diff(interpolated)) if np.isfinite(interpolated).all() else np.array([])
    shares = {}
    windows = {
        "night": range(0, 24),
        "morning": range(24, 48),
        "daytime": range(48, 68),
        "evening": range(68, 96),
    }
    denominator = np.nansum(raw)
    for name, indexes in windows.items():
        shares[name] = float(np.nansum(raw[list(indexes)]) / denominator) if denominator > 0 else np.nan
    base.update({
        "daily_energy_mwh": energy,
        "mean_mw": mean,
        "peak_mw": peak,
        "minimum_mw": float(np.nanmin(raw)),
        "load_factor": mean / peak if peak > 0 else np.nan,
        "p95_absolute_ramp_mw": float(np.quantile(ramps, 0.95)) if ramps.size else np.nan,
        "peak_interval_end_minute": int((np.nanargmax(raw) + 1) * 15),
        "night_energy_share": shares["night"],
        "morning_energy_share": shares["morning"],
        "daytime_energy_share": shares["daytime"],
        "evening_energy_share": shares["evening"],
        "usable_for_shape": bool(profile is not None),
    })
    shape = None
    if profile is not None:
        shape = {
            "station_key": station_key,
            "financial_year": financial_year,
            "local_operational_date": base["local_operational_date"],
            "is_weekend": base["is_weekend"],
            "month": base["month"],
            **{f"shape_{(index + 1) * 15:04d}": float(value) for index, value in enumerate(profile)},
        }
    return base, shape


def process_file(path: Path, root: Path) -> tuple[dict, list[dict], list[dict]]:
    relative = str(path.relative_to(root))
    frame = pd.read_csv(path, low_memory=False)
    columns = {str(column).lower(): column for column in frame.columns}
    required = {"year", "zone substation", "date", "unit"}
    missing = required - set(columns)
    if missing:
        raise RuntimeError(f"{relative}: missing columns {sorted(missing)}")
    time_columns = []
    for column in frame.columns:
        minute = interval_minute(str(column))
        if minute is not None:
            time_columns.append((minute, column))
    time_columns.sort(key=lambda item: item[0])
    minutes = [minute for minute, _ in time_columns]
    if len(time_columns) != EXPECTED_INTERVALS or minutes != list(range(15, 1441, 15)):
        raise RuntimeError(
            f"{relative}: expected 96 unique quarter-hour interval endings, found {len(time_columns)}"
        )
    units = sorted(frame[columns["unit"]].dropna().astype(str).str.strip().str.upper().unique())
    if units != ["MW"]:
        raise RuntimeError(f"{relative}: unsupported units {units}")
    dates = parse_date(frame[columns["date"]])
    station_values = frame[columns["zone substation"]].dropna().astype(str).str.strip()
    if station_values.empty:
        raise RuntimeError(f"{relative}: no station name")
    station = station_values.mode().iloc[0]
    station_key = normalise_station(station)
    years = pd.to_numeric(frame[columns["year"]], errors="coerce")
    financial_year = int(years.dropna().mode().iloc[0]) if years.notna().any() else -1
    values = frame[[column for _, column in time_columns]].apply(pd.to_numeric, errors="coerce").to_numpy()

    daily, shapes = [], []
    for index in range(len(frame)):
        record, shape = daily_record(
            station, station_key, financial_year, dates.iloc[index], values[index], relative
        )
        daily.append(record)
        if shape:
            shapes.append(shape)

    finite = values[np.isfinite(values)]
    usable_days = sum(record["usable_for_shape"] for record in daily)
    file_qc = {
        "source_file": relative,
        "sha256": sha256(path),
        "station_name": station,
        "station_key": station_key,
        "financial_year": financial_year,
        "row_count": len(frame),
        "date_start": dates.min().date().isoformat() if dates.notna().any() else "",
        "date_end": dates.max().date().isoformat() if dates.notna().any() else "",
        "date_parse_failure_count": int(dates.isna().sum()),
        "interval_column_count": len(time_columns),
        "unit": "MW",
        "finite_interval_fraction": float(np.isfinite(values).mean()),
        "negative_interval_count": int(np.sum(finite < 0)),
        "zero_interval_count": int(np.sum(finite == 0)),
        "robust_outlier_interval_count": robust_outlier_count(values.ravel()),
        "usable_shape_days": usable_days,
        "eligible_for_archetype": bool(
            len(frame) >= 330
            and dates.isna().mean() == 0
            and np.isfinite(values).mean() >= 0.95
            and usable_days >= 300
        ),
    }
    return file_qc, daily, shapes


def aggregate_station_year(daily: pd.DataFrame, shapes: pd.DataFrame) -> pd.DataFrame:
    rows = []
    shape_columns = [column for column in shapes if column.startswith("shape_")]
    for (station_key, financial_year), group in daily.groupby(["station_key", "financial_year"]):
        usable = group[group.usable_for_shape].copy()
        station_shapes = shapes[
            (shapes.station_key == station_key) & (shapes.financial_year == financial_year)
        ]
        row = {
            "station_key": station_key,
            "station_name": group.station_name.mode().iloc[0],
            "financial_year": int(financial_year),
            "days": len(group),
            "usable_days": int(group.usable_for_shape.sum()),
            "mean_missing_fraction": float(group.missing_fraction.mean()),
            "annual_energy_mwh_observed": float(group.daily_energy_mwh.sum(min_count=1)),
            "annual_peak_mw": float(group.peak_mw.max()),
            "median_daily_energy_mwh": float(group.daily_energy_mwh.median()),
            "median_daily_load_factor": float(group.load_factor.median()),
            "p95_daily_peak_mw": float(group.peak_mw.quantile(0.95)),
            "median_p95_ramp_mw": float(group.p95_absolute_ramp_mw.median()),
            "eligible_for_archetype": bool(
                group.usable_for_shape.sum() >= 300 and group.missing_fraction.mean() <= 0.05
            ),
        }
        for subset_name, mask in {
            "all": pd.Series(True, index=station_shapes.index),
            "weekday": ~station_shapes.is_weekend,
            "weekend": station_shapes.is_weekend,
            "summer": station_shapes.month.isin([12, 1, 2]),
            "winter": station_shapes.month.isin([6, 7, 8]),
        }.items():
            subset = station_shapes.loc[mask, shape_columns]
            if subset.empty:
                for column in shape_columns:
                    row[f"{subset_name}_{column}"] = np.nan
            else:
                median_shape = subset.median(axis=0)
                for column in shape_columns:
                    row[f"{subset_name}_{column}"] = float(median_shape[column])
        rows.append(row)
    return pd.DataFrame(rows)


def station_continuity(file_qc: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for station_key, group in file_qc.groupby("station_key"):
        years = sorted(set(int(value) for value in group.financial_year if value > 0))
        rows.append({
            "station_key": station_key,
            "canonical_station_name": group.station_name.mode().iloc[0],
            "first_financial_year": min(years) if years else np.nan,
            "last_financial_year": max(years) if years else np.nan,
            "year_count": len(years),
            "financial_years": ";".join(map(str, years)),
            "eligible_year_count": int(group.eligible_for_archetype.sum()),
            "all_file_names": ";".join(sorted(group.station_name.unique())),
        })
    return pd.DataFrame(rows).sort_values(
        ["eligible_year_count", "year_count", "canonical_station_name"], ascending=[False, False, True]
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("load_transfer/results"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(args.input_root.rglob("*.csv"))
    if len(files) < 2000:
        raise RuntimeError(f"Expected at least 2,000 Ausgrid CSVs, found {len(files)}")

    file_rows, daily_rows, shape_rows, failures = [], [], [], []
    schema_counter = Counter()
    for number, path in enumerate(files, start=1):
        try:
            with path.open(encoding="utf-8", errors="replace") as file:
                schema_counter[file.readline().strip()] += 1
            file_qc, daily, shapes = process_file(path, args.input_root)
            file_rows.append(file_qc)
            daily_rows.extend(daily)
            shape_rows.extend(shapes)
        except Exception as exc:
            failures.append({"source_file": str(path.relative_to(args.input_root)), "error": repr(exc)})
        if number % 100 == 0:
            print(f"processed {number}/{len(files)} files", flush=True)

    file_qc = pd.DataFrame(file_rows)
    daily = pd.DataFrame(daily_rows)
    shapes = pd.DataFrame(shape_rows)
    continuity = station_continuity(file_qc)
    station_year = aggregate_station_year(daily, shapes)

    file_qc.to_csv(args.output_dir / "ausgrid_file_qc.csv", index=False)
    continuity.to_csv(args.output_dir / "ausgrid_station_continuity.csv", index=False)
    station_year.to_parquet(args.output_dir / "ausgrid_station_year_archetype_features.parquet", index=False)
    daily.to_parquet(args.output_dir / "ausgrid_daily_features.parquet", index=False)
    pd.DataFrame(failures).to_csv(args.output_dir / "ausgrid_preparation_failures.csv", index=False)

    schema_audit = {
        "status": "PASS" if not failures else "REVIEW_REQUIRED",
        "source_page": "https://www.ausgrid.com.au/about-us/about-ausgrid/research-data-sets/distribution-zone-substation-data",
        "source_clock": SOURCE_CLOCK,
        "raw_file_count": len(files),
        "successfully_parsed_files": len(file_qc),
        "failed_files": len(failures),
        "unique_header_count": len(schema_counter),
        "header_counts": [{"header": header, "count": count} for header, count in schema_counter.items()],
        "observed_units": sorted(file_qc.unit.unique()) if not file_qc.empty else [],
        "financial_year_counts": file_qc.financial_year.value_counts().sort_index().to_dict(),
        "clock_warning": (
            "The public files use AEST in winter and AEDT in summer. Daily archetype features retain the official local operational day. "
            "UTC conversion and transition-hour reconciliation are deferred to the audited long-format stage."
        ),
    }
    (args.output_dir / "ausgrid_schema_audit.json").write_text(
        json.dumps(schema_audit, indent=2), encoding="utf-8"
    )
    quality_checks = [
        {"check": "raw_file_count", "passed": len(files) >= 2000, "value": len(files)},
        {"check": "parse_success_fraction", "passed": len(file_qc) / len(files) >= 0.99, "value": len(file_qc) / len(files)},
        {"check": "eligible_station_years", "passed": int(station_year.eligible_for_archetype.sum()) >= 500, "value": int(station_year.eligible_for_archetype.sum())},
        {"check": "continuous_source_stations", "passed": int((continuity.eligible_year_count >= 5).sum()) >= 50, "value": int((continuity.eligible_year_count >= 5).sum())},
        {"check": "daily_records", "passed": len(daily) >= 700000, "value": len(daily)},
    ]
    audit = {
        "status": "PASS" if all(check["passed"] for check in quality_checks) else "FAIL",
        "checks": quality_checks,
        "raw_file_count": len(files),
        "parsed_file_count": len(file_qc),
        "daily_record_count": len(daily),
        "shape_record_count": len(shapes),
        "station_year_count": len(station_year),
        "station_count": len(continuity),
        "eligible_station_year_count": int(station_year.eligible_for_archetype.sum()),
        "scientific_limitations": [
            "Ausgrid identifies occasional metering errors, data gaps, switching spikes and estimates based on assumed power factor.",
            "Absolute Ausgrid megawatt values are source-domain measurements and must not be transferred directly to Essential Energy target sites.",
            "The fixed 96-column daily format does not uniquely resolve daylight-saving transition intervals for UTC conversion; raw local clock labels are preserved.",
        ],
    }
    (args.output_dir / "load_archetype_preparation_audit.json").write_text(
        json.dumps(audit, indent=2), encoding="utf-8"
    )
    print(json.dumps(audit, indent=2))
    if audit["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
