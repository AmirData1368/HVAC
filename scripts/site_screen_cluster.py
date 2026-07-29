from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cluster import AgglomerativeClustering, KMeans
from sklearn.metrics import (
    adjusted_rand_score,
    calinski_harabasz_score,
    davies_bouldin_score,
    silhouette_score,
)
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import RobustScaler

RANDOM_SEED = 42


def cluster_candidates(features: pd.DataFrame, k_min: int = 3, k_max: int = 6):
    transformed = features.copy()
    for column in transformed:
        if column.endswith("_tpy_50km") or column in {"population_2021_50km", "population_density"}:
            transformed[column] = np.log1p(transformed[column].clip(lower=0))
    transformed = transformed.fillna(transformed.median(numeric_only=True))
    matrix = RobustScaler().fit_transform(transformed)
    rng = np.random.default_rng(RANDOM_SEED)
    rows, models = [], {}

    for k in range(k_min, min(k_max, len(matrix) - 1) + 1):
        kmeans = KMeans(n_clusters=k, random_state=RANDOM_SEED, n_init=100).fit(matrix)
        gmm = GaussianMixture(
            n_components=k, covariance_type="diag", reg_covar=1e-5,
            random_state=RANDOM_SEED, n_init=30,
        ).fit(matrix)
        ward = AgglomerativeClustering(n_clusters=k, linkage="ward").fit(matrix)
        stability = []
        for repeat in range(100):
            sample = np.sort(
                rng.choice(len(matrix), size=max(k + 2, int(0.8 * len(matrix))), replace=False)
            )
            perturbed = matrix[sample] + rng.normal(0.0, 0.03, matrix[sample].shape)
            labels = KMeans(
                n_clusters=k, random_state=RANDOM_SEED + repeat + 1, n_init=30
            ).fit_predict(perturbed)
            stability.append(adjusted_rand_score(kmeans.labels_[sample], labels))
        gmm_labels = gmm.predict(matrix)
        agreement = np.mean(
            [
                adjusted_rand_score(kmeans.labels_, gmm_labels),
                adjusted_rand_score(kmeans.labels_, ward.labels_),
                adjusted_rand_score(gmm_labels, ward.labels_),
            ]
        )
        rows.append(
            {
                "k": k,
                "silhouette": silhouette_score(matrix, kmeans.labels_),
                "calinski_harabasz": calinski_harabasz_score(matrix, kmeans.labels_),
                "davies_bouldin": davies_bouldin_score(matrix, kmeans.labels_),
                "kmeans_stability_ari": float(np.mean(stability)),
                "algorithm_agreement_ari": float(agreement),
            }
        )
        models[k] = kmeans

    comparison = pd.DataFrame(rows)
    comparison["score_silhouette"] = comparison.silhouette.rank(pct=True)
    comparison["score_calinski"] = comparison.calinski_harabasz.rank(pct=True)
    comparison["score_davies"] = (-comparison.davies_bouldin).rank(pct=True)
    comparison["score_stability"] = comparison.kmeans_stability_ari.rank(pct=True)
    comparison["score_agreement"] = comparison.algorithm_agreement_ari.rank(pct=True)
    score_columns = [
        "score_silhouette", "score_calinski", "score_davies",
        "score_stability", "score_agreement",
    ]
    comparison["composite_cluster_score"] = comparison[score_columns].mean(axis=1)
    best = comparison.sort_values(
        ["composite_cluster_score", "silhouette", "k"], ascending=[False, False, True]
    ).iloc[0]
    best_k = int(best.k)
    return models[best_k].labels_, comparison, best_k, matrix


def select_representatives(frame: pd.DataFrame, matrix: np.ndarray, labels: np.ndarray, k: int):
    result = frame.copy()
    result["cluster"] = labels
    centres = np.vstack([matrix[labels == cluster].mean(axis=0) for cluster in range(k)])
    result["distance_to_cluster_centroid"] = [
        float(np.linalg.norm(matrix[i] - centres[labels[i]])) for i in range(len(result))
    ]

    resources = [
        "ghi_kwh_m2_day", "wind50_mean_ms", "resource_complementarity",
        "crop_dry_tpy_50km", "manure_vs_tpy_50km", "organic_waste_tpy_50km",
        "forestry_dry_tpy_50km",
    ]
    score = result[resources].copy()
    for column in [c for c in resources if c.endswith("_50km")]:
        score[column] = np.log1p(score[column].clip(lower=0))
    score = (score - score.min()) / (score.max() - score.min()).replace(0, 1)
    result["resource_diversity_score"] = score.mean(axis=1)

    challenge = pd.DataFrame(index=result.index)
    challenge["heat"] = result.temp_p95_c.rank(pct=True)
    challenge["solar_variability"] = result.ghi_daily_cv.rank(pct=True)
    challenge["low_wind"] = (-result.wind50_mean_ms).rank(pct=True)
    dispatchable = result[
        ["crop_dry_tpy_50km", "manure_vs_tpy_50km", "organic_waste_tpy_50km", "forestry_dry_tpy_50km"]
    ].sum(axis=1)
    challenge["low_dispatchable_resource"] = (-np.log1p(dispatchable)).rank(pct=True)
    result["resilience_challenge_score"] = challenge.mean(axis=1)
    result["selection_reason"] = ""

    selected = []
    for cluster in sorted(result.cluster.unique()):
        index = result[result.cluster == cluster].sort_values(
            ["distance_to_cluster_centroid", "resource_diversity_score"],
            ascending=[True, False],
        ).index[0]
        selected.append(index)
        result.loc[index, "selection_reason"] = "cluster_medoid"

    for index, reason in [
        (result.resource_diversity_score.idxmax(), "resource_extreme"),
        (result.resilience_challenge_score.idxmax(), "resilience_challenge"),
    ]:
        if index not in selected:
            selected.append(index)
            result.loc[index, "selection_reason"] = reason
        else:
            result.loc[index, "selection_reason"] += ";" + reason
    result["selected_for_detailed_study"] = result.index.isin(selected)
    return result
