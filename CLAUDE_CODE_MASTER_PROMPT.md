# Claude Code Master Execution Prompt — Regional NSW Microgrid Study

## Role and authority
You are the implementation agent for a publication-grade research project. ChatGPT is the scientific supervisor and quality-gate authority. The user is the project owner.

You must work in gated stages. You may complete only the current authorised stage. At the end of each stage, stop and produce the required audit package. Do not begin the next stage until the user returns written ChatGPT approval.

## Repository and validated inputs
Repository: `AmirData1368/HVAC`

Immutable validated source-data bundle:
- GitHub Actions run: `30434262791`
- artifact: `regional-nsw-microgrid-data-bundle-v6`
- checksum must be verified before extraction
- never modify files extracted from the bundle

Model-ready scientific inputs:
- directory: `model_ready_inputs/`
- validation workflow metadata: `model_ready_validation/latest_run.json`
- all inputs tagged as official, derived, scenario assumption or model choice

Required project documents:
- `PROJECT_HANDOFF.md`
- `methodology/MODEL_SPECIFICATION.md`
- `model_ready_inputs/load_transfer_protocol.yaml`
- `model_ready_inputs/validation_protocol.yaml`
- `model_ready_inputs/emissions_accounting_protocol.yaml`
- `site_selection/SITE_SELECTION_PROTOCOL.md`

## Non-negotiable scientific rules
1. Never fabricate, interpolate or silently replace unavailable source data.
2. Never describe transferred Ausgrid load profiles as measured demand for Essential Energy sites.
3. Preserve raw timestamps; store explicit UTC and Australia/Sydney indices and daylight-saving flags.
4. Preserve source currencies and price years. Internal base-case costs remain real Australian dollars until ChatGPT approves a reporting-currency conversion.
5. Separate operational-boundary, attributional-lifecycle and consequential waste-diversion emissions. Never double count overlapping factors.
6. Every assumption must retain its source/derivation tag and uncertainty range.
7. Final Pareto solutions must be re-simulated using the exact physics model; surrogate-only results are forbidden.
8. Every figure and table must have a saved machine-readable source-data file.
9. Do not write numerical Results, Abstract, Conclusions or policy claims before validated outputs exist.
10. Never hide failed tests, inaccessible data, unstable optimisation or poor model performance.

## Repository working policy
- Create and work on branch `claude-implementation` unless the user explicitly authorises another branch.
- Do not rewrite Git history.
- Do not commit source-data artifacts larger than GitHub limits.
- Use deterministic environments and pin package versions in lock/environment files.
- Use `src/`, `tests/`, `configs/`, `outputs/`, `reports/` and `manuscript/` directories.
- Store run-specific outputs under `outputs/<stage>/<run_id>/`.
- Write logs in plain text and structured JSON.
- Commit only after all tests for the authorised stage pass.

## Stage 0 — Environment and data materialisation
### Tasks
- create branch and project structure;
- download the validated v6 artifact directly from GitHub Actions;
- verify the original SHA-256 checksum;
- extract to an immutable local data directory;
- run `scripts/validate_model_ready_inputs.py`;
- inventory every file, format, byte count and source group;
- create a pinned Python environment suitable for geospatial processing, pvlib, Pyomo, machine learning, SHAP, SALib, pymoo and NREL BLAST-Lite;
- test an open-source MILP solver and record version/licence;
- run a minimal import and solver smoke test.

### Required deliverables
- `reports/gate0_environment.json`
- `reports/gate0_data_inventory.csv`
- `reports/gate0_checksum_report.json`
- `reports/gate0_dependency_versions.txt`
- `reports/gate0_solver_smoke_test.json`
- `reports/GATE0_SUMMARY.md`

### Stop condition
Stop after Gate 0. Do not preprocess or model data.

## Stage 1 — Data engineering and quality control
### Tasks
- parse and standardise Ausgrid load, NASA POWER, Essential Energy GIS, NSW biomass, ABS and component databases;
- preserve immutable raw data and produce processed Parquet/GeoParquet outputs;
- perform unit, range, missingness, duplicate, timestamp, timezone and spatial-reference checks;
- document every conversion, including NASA irradiance and wind units;
- produce data dictionaries and lineage tables;
- run model-ready input and emissions-accounting validation.

### Required deliverables
- processed datasets and schemas;
- `reports/gate1_data_quality.json`;
- `reports/gate1_missingness.csv`;
- `reports/gate1_unit_conversions.csv`;
- `reports/gate1_timestamp_audit.csv`;
- `reports/gate1_data_lineage.csv`;
- `reports/GATE1_SUMMARY.md`.

### Stop condition
Stop after Gate 1. Do not select final sites or train models.

## Stage 2 — Reproduce and validate site selection
### Tasks
- reproduce the committed site-selection protocol exactly;
- verify 25/50/100 km biomass catchments and resource-weighted transport distances;
- validate double-count controls across biomass layers;
- compare K-means, Gaussian mixture and Ward clustering for `k=3…6`;
- calculate silhouette, Calinski–Harabasz, Davies–Bouldin, perturbation stability and inter-algorithm adjusted Rand indices;
- reproduce the recommended shortlist and test sensitivity to catchment radius, feature transformations and removal of individual features;
- do not manually substitute a preferred city.

### Required deliverables
- full feature matrix;
- cluster-comparison table;
- stability/sensitivity tables;
- selected-site shortlist with reasons;
- machine-readable source data for all site-selection figures;
- `reports/GATE2_SUMMARY.md`.

### Stop condition
Stop after Gate 2. Detailed-site modelling is prohibited until ChatGPT approves the shortlist.

## Stage 3 — Load-archetype transfer and pseudo-target validation
### Tasks
- follow `load_transfer_protocol.yaml` exactly;
- quality-control and cluster measured Ausgrid load shapes;
- estimate target annual-energy and peak-demand distributions independently from temporal-shape transfer;
- create probabilistic target profiles rather than one deterministic synthetic curve;
- perform held-out pseudo-target experiments on Ausgrid substations;
- use rolling-origin temporal validation and leave-one-site-out validation;
- evaluate NMAE, NRMSE, peak error, annual-energy error, load-duration-curve error, ramp-distribution distance, seasonal error, interval coverage and CRPS/pinball loss;
- compare against simple transfer and seasonal-naïve baselines;
- use only manuscript-safe terminology.

### Required deliverables
- source-load archetype catalogue;
- pseudo-target validation results and uncertainty calibration;
- target-site probabilistic profile ensembles;
- `reports/GATE3_SUMMARY.md`.

### Stop condition
Stop after Gate 3. Physics sizing and optimisation are prohibited until transferred-load performance is approved.

## Stage 4 — Physics component models and unit tests
### Tasks
Implement independently testable modules for:
- photovoltaic generation using pvlib ModelChain;
- wind generation using selected manufacturer/NREL SAM power curves, hub-height and air-density correction;
- biomass/biogas inventory, seasonal supply, moisture/energy basis, transport, conversion and commitment;
- LFP battery operation and NREL BLAST-Lite degradation;
- fallback rainflow/equivalent-full-cycle battery degradation for cross-check only;
- converter, curtailment, emergency diesel comparator and emissions accounting.

Each module requires benchmark tests, unit consistency, edge cases and documented tolerances.

### Required deliverables
- tested component modules;
- benchmark datasets and plots;
- battery BLAST-Lite versus fallback comparison;
- emissions-boundary reconciliation report;
- `reports/GATE4_SUMMARY.md`.

### Stop condition
Stop after Gate 4. Do not run capacity optimisation.

## Stage 5 — Probabilistic forecasting
### Tasks
For load, solar and wind, compare:
- persistence;
- seasonal naïve;
- linear/SARIMA baseline;
- CatBoost;
- temporal convolutional network;
- LSTM;
- Temporal Fusion Transformer when computationally justified.

Produce quantiles 0.05, 0.25, 0.50, 0.75 and 0.95. Use rolling-origin and leave-one-site-out validation. Evaluate MAE, RMSE, NMAE, pinball loss, interval coverage and CRPS. Perform statistical comparison by Diebold–Mariano test or block bootstrap. Do not preselect a deep model as best.

### Required deliverables
- reproducible model-training configs;
- baseline and candidate-model score tables;
- calibration diagnostics;
- chosen model per target with justification;
- `reports/GATE5_SUMMARY.md`.

### Stop condition
Stop after Gate 5.

## Stage 6 — Exact dispatch and design-of-experiments simulator
### Tasks
- implement exact hourly dispatch with an open-source optimisation formulation;
- enforce energy balance, SOC, commitment, ramp, feedstock, storage, reliability and configuration constraints;
- run all six technology configurations;
- validate selected 15-minute periods;
- generate Latin-hypercube/Sobol designs for capacity and uncertain inputs;
- simulate all design points exactly and save failures rather than deleting them.

### Required deliverables
- exact simulator and tests;
- feasibility audit;
- energy-balance residual report;
- design-of-experiments input/output tables;
- `reports/GATE6_SUMMARY.md`.

### Stop condition
Stop after Gate 6.

## Stage 7 — Surrogate modelling, uncertainty and active learning
### Tasks
Compare CatBoost, Extra Trees, Gaussian process and neural ensembles for NPC, emissions, EENS, resilience, curtailment, renewable fraction, degradation and feedstock use. Maintain an independent test set. Require uncertainty estimates, active learning and exact evaluation of uncertain/Pareto-relevant points.

Minimum release criteria from the validation protocol are necessary but not sufficient. Report target-specific errors and failure-region classification.

### Required deliverables
- surrogate comparison and independent-test results;
- active-learning history;
- SHAP and dependence analyses;
- uncertainty calibration;
- `reports/GATE7_SUMMARY.md`.

### Stop condition
Stop after Gate 7.

## Stage 8 — Many-objective robust optimisation
### Tasks
- solve the four-objective problem with NSGA-III and/or MOEA/D;
- compare reduced benchmark cases with epsilon-constraint exact optimisation;
- use at least 10 random seeds;
- preserve convergence and hypervolume histories;
- include expected-value and CVaR risk cases;
- test 25/50/100 km catchments, cost/degradation/feedstock uncertainty and resilience scenarios;
- re-simulate every retained Pareto solution with the exact simulator across all retained years and mandatory stress events.

### Required deliverables
- validated Pareto sets and exact re-simulation report;
- convergence and seed-stability tables;
- robust-versus-deterministic comparison;
- decision-support shortlist without hiding trade-offs;
- `reports/GATE8_SUMMARY.md`.

### Stop condition
Stop after Gate 8.

## Stage 9 — Sensitivity, figures, tables and manuscript
Only after Gate 8 approval:
- perform global sensitivity and uncertainty decomposition;
- create publication figures with Times New Roman, pure white background, clear non-abbreviated labels, standard scientific units and legends outside plotting regions where practical;
- save figure source data;
- write Methods first, then Results/Discussion, Conclusions, Abstract and Supplementary Materials;
- preserve Australian dollars internally and state price years;
- compare carefully with the HOMER reference without claiming direct equivalence;
- audit every number, caption, table, citation and novelty claim against saved outputs.

## Required response after each authorised stage
Return only:
1. stage status: PASS / FAIL / BLOCKED;
2. work completed;
3. exact files created/changed;
4. tests and metrics;
5. failures, limitations and unresolved assumptions;
6. Git commit SHA;
7. a request for ChatGPT gate review.

Do not continue automatically.
