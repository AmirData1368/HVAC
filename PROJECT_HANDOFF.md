# Regional NSW Microgrid Project — Final Handoff Status

_Last updated: 31 July 2026 (Melbourne time)_

## FINAL STATUS

**READY FOR CLAUDE CODE.**

- Readiness marker: `READY_FOR_CLAUDE.json`
- Claude instructions: `CLAUDE_CODE_MASTER_PROMPT.md`
- Required starting point: **Stage 0 only**
- Claude must stop at every stage gate for ChatGPT review.
- Final target-demand workflow run: `30610703165`
- Full handoff artifact: `regional-nsw-final-target-demand-gate-v1`
- Artifact digest: `sha256:f6923606d1fb0f2f181c786973454d6d8988484f69f65d015df5425b98639271`

## Project objective

Build a publication-grade, open-source, Python-based framework for renewable microgrid planning in regional New South Wales that is substantially stronger than the reference HOMER study. The framework combines real Australian public data, physics-based component models, probabilistic machine learning, many-objective robust optimisation, degradation, resilience, explainability and reproducible article production.

## Validated source-data bundle — COMPLETE

- GitHub Actions run: `30434262791`
- Artifact: `regional-nsw-microgrid-data-bundle-v6`
- Extracted size: approximately 1.1 GB across roughly 2,500 files
- All 14 core source-data checks passed

Confirmed sources include Ausgrid measured 15-minute demand, NASA POWER weather, Essential Energy network GIS and capacity attributes, NSW biomass layers, ABS Census and population data, technology libraries, CSIRO GenCost, DCCEEW factors, checksums, licences and lineage.

## Model-ready scientific inputs — COMPLETE

Directory: `model_ready_inputs/`

Validated inputs include scale-sensitive PV, wind, biomass and battery costs; separate battery power and energy costs; LFP degradation and replacement assumptions; project finance; biomass recovery and logistics; diesel and transport assumptions; outage and compound-event scenarios; operational and lifecycle emissions protocols; and mandatory data, ML, physics, surrogate and optimisation quality gates.

Core validation passed with zero errors and zero warnings. Fuel/logistics and target-load governance checks also passed.

## Site-selection gate — COMPLETE

Six detailed-study sites are approved:

1. Suffolk Park — Byron Bay
2. Narrandera
3. West Jemalong — Forbes
4. Bombala
5. Merrywinebone — Walgett–Lightning Ridge
6. Hallidays Point 11kV — Old Bar–Manning Point–Red Head

The five-cluster site structure passed nine sensitivity cases. Median partition ARI was 0.7793, minimum ARI was 0.5847, and median central-shortlist selection frequency was 0.8333.

## Target demand scale — COMPLETE

Official Essential Energy 2025–2034 attributes were matched to all six sites.

`network_capacity_implied_peak_proxy = n_1_nameplate_capacity - available_capacity_load_at_n_1`

2025 peak proxies:

- Suffolk Park: 13.1 MW — radial/nonfirm uncertainty flag
- Narrandera: 10.3 MW
- West Jemalong: 2.1 MW
- Bombala: 5.0 MW
- Merrywinebone: 2.6 MW — radial/nonfirm uncertainty flag
- Hallidays Point 11kV: 8.3 MW

These are official-network-attribute-derived proxies, not measured local demand.

## Australian Energy Statistics gate — COMPLETE WITH DISCLOSED FALLBACK

The official Australian Energy Statistics 2025 publication and state pages are used for state-level plausibility context. Direct XLSX reads repeatedly timed out from the hosted GitHub Actions network, so no workbook download is claimed. The fallback audit records official energy.gov.au values and URLs and explicitly prohibits treating state values as measured local loads.

## Ausgrid source archetypes — COMPLETE

- Source run: `30492636843`
- Raw files processed: 2,163
- Failed files: 0
- Eligible source stations used in final clustering: 197
- Selected source-load clusters: 4
- Silhouette coefficient: 0.273236
- First eight PCA components explained 98.4834% of shape variance

## Pseudo-target validation — PASSED

Leave-one-station-out transfer validation across 197 source stations produced:

- Median Pearson correlation: 0.947464
- 10th-percentile correlation: 0.858629
- Median normalized RMSE: 0.062977
- 90th-percentile normalized RMSE: 0.103790
- Median MAE: 0.052377

## Probabilistic target profiles — COMPLETE

- Six target sites
- 100 annual scenarios per site
- 600 annual scenarios total
- 15-minute resolution
- 35,040 intervals per scenario
- 21,024,000 generated target-load intervals total
- All interval counts exact
- No negative load values
- Each official peak proxy covered by its scenario 5th–95th percentile range
- All site-level load-factor plausibility checks passed

Full profile Parquet files are stored in the workflow artifact. Compact scenario summaries and validation outputs are committed under `load_transfer/final_target_demand_gate/`.

## Binding scientific terminology

- Ausgrid data are **measured source-domain temporal archetypes**.
- Ausgrid absolute MW values must never be presented as measured demand at the six target sites.
- Essential Energy attributes provide the **network-capacity-implied peak proxy**.
- Final target profiles are **probabilistic data-derived transferred demand scenarios**, not measurements.

## Methodology and governance — COMPLETE

- `methodology/MODEL_SPECIFICATION.md` contains the physical, economic, emissions, uncertainty, machine-learning and optimisation formulation.
- `CLAUDE_CODE_MASTER_PROMPT.md` contains the stage-gated execution plan.
- Every final Pareto point must be exactly re-simulated.
- Every figure and table must retain machine-readable source data.
- No failed test, inaccessible source, weak result, infeasible design or scientific limitation may be hidden.

## Claude start instruction

Claude Code may now be given the repository and instructed to open `CLAUDE_CODE_MASTER_PROMPT.md`, begin **Stage 0**, produce only the Stage 0 deliverables, and stop for ChatGPT approval before proceeding.
