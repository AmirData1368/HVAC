from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import pearsonr
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

SOURCE_DIR = Path("load_transfer/source_artifact/results")
ANCHOR_PATH = Path("load_transfer/target_scaling/selected_site_network_capacity_trajectory.csv")
AES_AUDIT_PATH = Path(
    "model_ready_inputs/reference/australian_energy_statistics_2025/"
    "AES_2025_download_audit.json"
)
OUT = Path("load_transfer/final_target_demand_gate")
PROFILE_DIR = OUT / "profiles"
OUT.mkdir(parents=True, exist_ok=True)
PROFILE_DIR.mkdir(parents=True, exist_ok=True)

SCENARIOS_PER_SITE = 100
YEAR = 2025
INTERVALS_PER_DAY = 96
EXPECTED_INTERVALS = 365 * INTERVALS_PER_DAY
RANDOM_SEED = 260731


def safe_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def bool_series(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.lower().map({"true": True, "false": False}).fillna(False)


def shape_columns(frame: pd.DataFrame, prefix: str) -> list[str]:
    columns = [column for column in frame.columns if column.startswith(prefix)]
    return sorted(columns, key=lambda value: int(value.rsplit("_", 1)[-1]))


def normalise_shape(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    values = np.where(np.isfinite(values), values, np.nan)
    if np.isnan(values).all():
        return np.ones(INTERVALS_PER_DAY, dtype=float)
    values = pd.Series(values).interpolate(limit_direction="both").fillna(1.0).to_numpy()
    values = np.clip(values, 0.02, None)
    mean = float(np.mean(values))
    return values / mean if mean > 0 else np.ones_like(values)


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    station_year_path = SOURCE_DIR / "ausgrid_station_year_archetype_features.parquet"
    if not station_year_path.exists():
        raise FileNotFoundError(station_year_path)
    station_year = pd.read_parquet(station_year_path)
    required = {
        "station_key",
        "station_name",
        "financial_year",
        "eligible_for_archetype",
        "median_daily_load_factor",
        "annual_peak_mw",
    }
    missing = required - set(station_year.columns)
    if missing:
        raise RuntimeError(f"station-year input missing columns: {sorted(missing)}")
    station_year = station_year[bool_series(station_year["eligible_for_archetype"])].copy()
    if station_year.station_key.nunique() < 100:
        raise RuntimeError("fewer than 100 eligible Ausgrid stations")

    anchors = pd.read_csv(ANCHOR_PATH)
    anchors["year"] = pd.to_numeric(anchors["year"], errors="coerce").astype("Int64")
    anchors = anchors[anchors.year == YEAR].copy()
    anchors["radial_or_nonfirm_flag"] = bool_series(anchors["radial_or_nonfirm_flag"])
    if anchors.site_name.nunique() != 6:
        raise RuntimeError(f"expected six target sites, found {anchors.site_name.nunique()}")
    if (anchors.network_capacity_implied_peak_proxy_mw <= 0).any():
        raise RuntimeError("target peak proxies must be positive")

    if not AES_AUDIT_PATH.exists():
        raise FileNotFoundError(
            "Australian Energy Statistics audit is missing; the final demand gate cannot close"
        )
    aes_audit = json.loads(AES_AUDIT_PATH.read_text(encoding="utf-8"))
    if aes_audit.get("status") != "PASS":
        raise RuntimeError("Australian Energy Statistics audit is not PASS")
    return station_year, anchors, aes_audit


def build_station_level(station_year: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    all_shape = shape_columns(station_year, "all_shape_")
    if len(all_shape) != INTERVALS_PER_DAY:
        raise RuntimeError(f"expected 96 all-day shape columns, found {len(all_shape)}")
    scalar_columns = [
        "median_daily_load_factor",
        "median_p95_ramp_mw",
        "annual_peak_mw",
        "p95_daily_peak_mw",
    ]
    scalar_columns = [column for column in scalar_columns if column in station_year.columns]
    aggregations = {column: "median" for column in all_shape + scalar_columns}
    aggregations["station_name"] = "first"
    station = station_year.groupby("station_key", as_index=False).agg(aggregations)
    for index in station.index:
        station.loc[index, all_shape] = normalise_shape(
            station.loc[index, all_shape].to_numpy(dtype=float)
        )
    return station, all_shape


def cluster_source_archetypes(
    station: pd.DataFrame, all_shape: list[str]
) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    shape_matrix = station[all_shape].to_numpy(dtype=float)
    derived = pd.DataFrame(
        {
            "load_factor": pd.to_numeric(
                station["median_daily_load_factor"], errors="coerce"
            ).fillna(station["median_daily_load_factor"].median()),
            "morning_share": shape_matrix[:, 24:48].mean(axis=1),
            "day_share": shape_matrix[:, 48:68].mean(axis=1),
            "evening_share": shape_matrix[:, 68:96].mean(axis=1),
            "peak_interval": np.argmax(shape_matrix, axis=1) / 95.0,
        }
    )
    shape_pca = PCA(n_components=min(12, shape_matrix.shape[0] - 1), random_state=RANDOM_SEED)
    pca_values = shape_pca.fit_transform(StandardScaler().fit_transform(shape_matrix))
    features = np.column_stack(
        [pca_values[:, : min(8, pca_values.shape[1])], StandardScaler().fit_transform(derived)]
    )

    candidates: list[dict] = []
    fitted: dict[int, KMeans] = {}
    for k in range(3, 9):
        model = KMeans(n_clusters=k, n_init=50, random_state=RANDOM_SEED)
        labels = model.fit_predict(features)
        score = float(silhouette_score(features, labels))
        candidates.append({"k": k, "silhouette": score})
        fitted[k] = model
    selected = max(candidates, key=lambda item: item["silhouette"])
    labels = fitted[selected["k"]].labels_
    station = station.copy()
    station["source_load_archetype_cluster"] = labels

    cluster_rows = []
    for cluster, group in station.groupby("source_load_archetype_cluster"):
        matrix = group[all_shape].to_numpy(dtype=float)
        median_shape = np.median(matrix, axis=0)
        row = {
            "source_load_archetype_cluster": int(cluster),
            "station_count": int(len(group)),
            "median_load_factor": float(group.median_daily_load_factor.median()),
            "median_peak_interval_end_minute": int((np.argmax(median_shape) + 1) * 15),
        }
        row.update({column: float(value) for column, value in zip(all_shape, median_shape)})
        cluster_rows.append(row)
    cluster_summary = pd.DataFrame(cluster_rows)

    audit = {
        "candidate_cluster_counts": candidates,
        "selected_k": int(selected["k"]),
        "selected_silhouette": float(selected["silhouette"]),
        "station_count": int(len(station)),
        "explained_variance_first_8_pca": float(
            shape_pca.explained_variance_ratio_[:8].sum()
        ),
    }
    return station, audit, cluster_summary


def pseudo_target_validation(
    station: pd.DataFrame, all_shape: list[str]
) -> tuple[pd.DataFrame, dict]:
    records = []
    global_shape = np.median(station[all_shape].to_numpy(dtype=float), axis=0)
    for row in station.itertuples(index=False):
        cluster = int(row.source_load_archetype_cluster)
        peers = station[
            (station.source_load_archetype_cluster == cluster)
            & (station.station_key != row.station_key)
        ]
        if len(peers) < 3:
            prediction = global_shape.copy()
            peer_count = int(len(station) - 1)
            method = "global_leave_one_station_out_median"
        else:
            prediction = np.median(peers[all_shape].to_numpy(dtype=float), axis=0)
            peer_count = int(len(peers))
            method = "cluster_leave_one_station_out_median"
        observed = np.asarray([getattr(row, column) for column in all_shape], dtype=float)
        observed = normalise_shape(observed)
        prediction = normalise_shape(prediction)
        rmse = float(np.sqrt(np.mean((prediction - observed) ** 2)))
        mae = float(np.mean(np.abs(prediction - observed)))
        correlation = float(pearsonr(prediction, observed).statistic)
        records.append(
            {
                "station_key": row.station_key,
                "station_name": row.station_name,
                "source_load_archetype_cluster": cluster,
                "peer_count": peer_count,
                "method": method,
                "nrmse": rmse,
                "mae": mae,
                "pearson_correlation": correlation,
            }
        )
    frame = pd.DataFrame(records)
    summary = {
        "station_count": int(len(frame)),
        "median_correlation": float(frame.pearson_correlation.median()),
        "p10_correlation": float(frame.pearson_correlation.quantile(0.10)),
        "median_nrmse": float(frame.nrmse.median()),
        "p90_nrmse": float(frame.nrmse.quantile(0.90)),
        "median_mae": float(frame.mae.median()),
    }
    summary["passed"] = bool(
        summary["median_correlation"] >= 0.85
        and summary["p10_correlation"] >= 0.55
        and summary["median_nrmse"] <= 0.22
        and summary["p90_nrmse"] <= 0.40
    )
    return frame, summary


def get_row_shape(row: pd.Series, prefix: str) -> np.ndarray:
    columns = shape_columns(pd.DataFrame([row]), prefix)
    if len(columns) != INTERVALS_PER_DAY:
        return np.ones(INTERVALS_PER_DAY, dtype=float)
    return normalise_shape(row[columns].to_numpy(dtype=float))


def smooth_noise(rng: np.random.Generator, scale: float = 0.025) -> np.ndarray:
    raw = rng.normal(0.0, scale, INTERVALS_PER_DAY + 8)
    kernel = np.ones(9) / 9.0
    return np.convolve(raw, kernel, mode="valid")


def create_one_profile(
    source_row: pd.Series,
    peak_proxy: float,
    radial: bool,
    scenario_id: int,
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, dict]:
    all_shape = get_row_shape(source_row, "all_shape_")
    weekday_shape = get_row_shape(source_row, "weekday_shape_")
    weekend_shape = get_row_shape(source_row, "weekend_shape_")
    summer_shape = get_row_shape(source_row, "summer_shape_")
    winter_shape = get_row_shape(source_row, "winter_shape_")

    dates = pd.date_range(f"{YEAR}-01-01", f"{YEAR}-12-31", freq="D")
    daily_ar = np.empty(len(dates), dtype=float)
    state = 0.0
    for index in range(len(dates)):
        state = 0.82 * state + rng.normal(0.0, 0.075)
        daily_ar[index] = math.exp(state - 0.5 * 0.075**2)
    daily_ar = np.clip(daily_ar, 0.70, 1.35)

    values = np.empty(EXPECTED_INTERVALS, dtype=float)
    cursor = 0
    for index, date in enumerate(dates):
        base = weekend_shape if date.dayofweek >= 5 else weekday_shape
        if date.month in (12, 1, 2):
            shape = 0.68 * base + 0.32 * summer_shape
        elif date.month in (6, 7, 8):
            shape = 0.68 * base + 0.32 * winter_shape
        else:
            shape = 0.85 * base + 0.15 * all_shape
        shape = shape * np.exp(smooth_noise(rng))
        shape = normalise_shape(shape)
        values[cursor : cursor + INTERVALS_PER_DAY] = shape * daily_ar[index]
        cursor += INTERVALS_PER_DAY

    peak_sigma = 0.15 if radial else 0.08
    peak_multiplier = float(
        np.clip(
            math.exp(rng.normal(-0.5 * peak_sigma**2, peak_sigma)),
            0.65 if radial else 0.78,
            1.55 if radial else 1.28,
        )
    )
    sampled_peak = peak_proxy * peak_multiplier
    values = np.clip(values, 0.0, None)
    values *= sampled_peak / float(np.max(values))

    timestamps = pd.date_range(
        f"{YEAR}-01-01 00:15:00",
        periods=EXPECTED_INTERVALS,
        freq="15min",
    )
    frame = pd.DataFrame(
        {
            "scenario_id": np.int16(scenario_id),
            "timestamp_local_interval_end": timestamps,
            "load_mw": values.astype(np.float32),
        }
    )
    annual_energy = float(values.sum() * 0.25)
    achieved_peak = float(values.max())
    load_factor = annual_energy / (achieved_peak * 8760.0)
    summary = {
        "scenario_id": scenario_id,
        "source_station_key": str(source_row.station_key),
        "source_station_name": str(source_row.station_name),
        "source_financial_year": int(source_row.financial_year),
        "source_load_archetype_cluster": int(source_row.source_load_archetype_cluster),
        "sampled_peak_mw": achieved_peak,
        "annual_energy_mwh": annual_energy,
        "annual_load_factor": load_factor,
        "minimum_load_mw": float(values.min()),
    }
    return frame, summary


def generate_target_profiles(
    station_year: pd.DataFrame,
    station_clusters: pd.DataFrame,
    anchors: pd.DataFrame,
) -> tuple[pd.DataFrame, dict]:
    cluster_map = station_clusters.set_index("station_key")[
        "source_load_archetype_cluster"
    ]
    pool = station_year.copy()
    pool["source_load_archetype_cluster"] = pool.station_key.map(cluster_map)
    pool = pool.dropna(subset=["source_load_archetype_cluster"]).copy()
    pool["source_load_archetype_cluster"] = pool.source_load_archetype_cluster.astype(int)
    cluster_sizes = pool.source_load_archetype_cluster.value_counts()
    weights = pool.source_load_archetype_cluster.map(lambda value: 1.0 / cluster_sizes[value])
    weights = (weights / weights.sum()).to_numpy(dtype=float)

    summaries: list[dict] = []
    site_checks: list[dict] = []
    base_rng = np.random.default_rng(RANDOM_SEED)
    for site_index, anchor in enumerate(anchors.sort_values("site_name").itertuples(index=False)):
        site_rng = np.random.default_rng(base_rng.integers(0, 2**32 - 1) + site_index)
        site_frames: list[pd.DataFrame] = []
        selected_indexes = site_rng.choice(
            pool.index.to_numpy(),
            size=SCENARIOS_PER_SITE,
            replace=True,
            p=weights,
        )
        for scenario_id, source_index in enumerate(selected_indexes, start=1):
            source_row = pool.loc[source_index]
            frame, summary = create_one_profile(
                source_row=source_row,
                peak_proxy=float(anchor.network_capacity_implied_peak_proxy_mw),
                radial=bool(anchor.radial_or_nonfirm_flag),
                scenario_id=scenario_id,
                rng=site_rng,
            )
            frame.insert(0, "site_name", str(anchor.site_name))
            site_frames.append(frame)
            summary.update(
                {
                    "site_name": str(anchor.site_name),
                    "sa2_name": str(anchor.SA2_NAME21),
                    "network_capacity_implied_peak_proxy_mw": float(
                        anchor.network_capacity_implied_peak_proxy_mw
                    ),
                    "radial_or_nonfirm_flag": bool(anchor.radial_or_nonfirm_flag),
                }
            )
            summaries.append(summary)
        site_data = pd.concat(site_frames, ignore_index=True)
        site_path = PROFILE_DIR / f"{safe_name(str(anchor.site_name))}_2025_100_scenarios.parquet"
        site_data.to_parquet(site_path, index=False, compression="zstd")

        site_summary = pd.DataFrame(
            [record for record in summaries if record["site_name"] == str(anchor.site_name)]
        )
        proxy = float(anchor.network_capacity_implied_peak_proxy_mw)
        p05 = float(site_summary.sampled_peak_mw.quantile(0.05))
        p95 = float(site_summary.sampled_peak_mw.quantile(0.95))
        site_checks.append(
            {
                "site_name": str(anchor.site_name),
                "scenario_count": int(len(site_summary)),
                "profile_interval_count": int(len(site_data)),
                "expected_profile_interval_count": SCENARIOS_PER_SITE * EXPECTED_INTERVALS,
                "proxy_peak_mw": proxy,
                "scenario_peak_p05_mw": p05,
                "scenario_peak_p95_mw": p95,
                "proxy_covered_by_p05_p95": bool(p05 <= proxy <= p95),
                "minimum_load_mw": float(site_data.load_mw.min()),
                "median_annual_load_factor": float(site_summary.annual_load_factor.median()),
                "p05_annual_load_factor": float(site_summary.annual_load_factor.quantile(0.05)),
                "p95_annual_load_factor": float(site_summary.annual_load_factor.quantile(0.95)),
                "profile_file": str(site_path),
            }
        )

    summary_frame = pd.DataFrame(summaries)
    checks = {
        "site_checks": site_checks,
        "six_sites": len(site_checks) == 6,
        "one_hundred_scenarios_per_site": all(
            item["scenario_count"] == SCENARIOS_PER_SITE for item in site_checks
        ),
        "all_interval_counts_exact": all(
            item["profile_interval_count"] == item["expected_profile_interval_count"]
            for item in site_checks
        ),
        "all_proxy_peaks_covered": all(
            item["proxy_covered_by_p05_p95"] for item in site_checks
        ),
        "no_negative_load": all(item["minimum_load_mw"] >= 0 for item in site_checks),
        "load_factor_plausible": all(
            0.20 <= item["median_annual_load_factor"] <= 0.85 for item in site_checks
        ),
    }
    checks["passed"] = all(
        value for key, value in checks.items() if key not in {"site_checks", "passed"}
    )
    return summary_frame, checks


def main() -> None:
    station_year, anchors, aes_audit = load_inputs()
    station, all_shape = build_station_level(station_year)
    station_clusters, clustering_audit, cluster_summary = cluster_source_archetypes(
        station, all_shape
    )
    pseudo_frame, pseudo_summary = pseudo_target_validation(station_clusters, all_shape)
    scenario_summary, uncertainty_summary = generate_target_profiles(
        station_year, station_clusters, anchors
    )

    station_clusters.to_csv(OUT / "source_station_archetype_assignments.csv", index=False)
    cluster_summary.to_csv(OUT / "source_archetype_cluster_summary.csv", index=False)
    pseudo_frame.to_csv(OUT / "pseudo_target_leave_one_station_out.csv", index=False)
    scenario_summary.to_csv(OUT / "target_scenario_summary.csv", index=False)

    gates = {
        "aes_2025_official_inputs": aes_audit.get("status") == "PASS",
        "ausgrid_source_station_count": int(station.station_key.nunique()),
        "source_clustering": bool(
            3 <= clustering_audit["selected_k"] <= 8
            and clustering_audit["selected_silhouette"] >= 0.20
        ),
        "pseudo_target_validation": bool(pseudo_summary["passed"]),
        "probabilistic_profiles": bool(uncertainty_summary["passed"]),
        "target_site_count": int(anchors.site_name.nunique()),
        "scenarios_per_site": SCENARIOS_PER_SITE,
    }
    ready = bool(
        gates["aes_2025_official_inputs"]
        and gates["source_clustering"]
        and gates["pseudo_target_validation"]
        and gates["probabilistic_profiles"]
        and gates["target_site_count"] == 6
        and gates["scenarios_per_site"] >= 100
    )
    audit = {
        "status": "READY_FOR_CLAUDE" if ready else "NOT_READY",
        "github_run_id": os.getenv("GITHUB_RUN_ID"),
        "source_ausgrid_run_id": 30492636843,
        "source_ausgrid_artifact": "ausgrid-source-load-archetype-preparation-v1",
        "year": YEAR,
        "interval_minutes": 15,
        "scientific_terminology": {
            "ausgrid_role": (
                "measured source-domain temporal archetypes only; never measured target-site demand"
            ),
            "target_scale": (
                "Essential Energy official network-capacity-implied peak proxy with uncertainty"
            ),
            "target_profiles": (
                "probabilistic data-derived transferred demand scenarios, not measurements"
            ),
        },
        "gates": gates,
        "clustering": clustering_audit,
        "pseudo_target": pseudo_summary,
        "uncertainty_coverage": uncertainty_summary,
    }
    (OUT / "target_demand_gate_audit.json").write_text(
        json.dumps(audit, indent=2), encoding="utf-8"
    )
    readiness = {
        "status": audit["status"],
        "ready_for_claude": ready,
        "required_start": "CLAUDE_CODE_MASTER_PROMPT.md Stage 0",
        "governance": "Claude must stop at every stage gate for ChatGPT review.",
        "full_profile_artifact_directory": str(PROFILE_DIR),
        "compact_gate_audit": str(OUT / "target_demand_gate_audit.json"),
    }
    Path("READY_FOR_CLAUDE.json").write_text(
        json.dumps(readiness, indent=2), encoding="utf-8"
    )
    Path("CLAUDE_HANDOFF_READY.md").write_text(
        "# Claude Code handoff status\n\n"
        + ("**READY FOR CLAUDE — Stage 0 may begin.**\n\n" if ready else "**NOT READY.**\n\n")
        + "Use `CLAUDE_CODE_MASTER_PROMPT.md` and begin at Stage 0 only. "
        + "Do not relabel transferred demand scenarios as measured target-site load. "
        + "Stop after every stage for ChatGPT review.\n",
        encoding="utf-8",
    )
    print(json.dumps(audit, indent=2), flush=True)
    if not ready:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
