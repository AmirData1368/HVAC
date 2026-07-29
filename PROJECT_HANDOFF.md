# Regional NSW Microgrid Project — Handoff Status

_Last updated: 30 July 2026 (Melbourne time)_

## Project objective
Build a publication-grade, open-source, Python-based framework for renewable microgrid planning in regional New South Wales that is substantially stronger than the reference HOMER paper. The study combines real Australian public data, physics-based component models, probabilistic machine learning, many-objective robust optimisation, degradation, resilience, explainability and full reproducibility under ChatGPT supervision.

## Validated source-data bundle
- GitHub Actions run: `30434262791`
- Artifact: `regional-nsw-microgrid-data-bundle-v6`
- Extracted size: approximately 1.1 GB across roughly 2,500 files
- All 14 core source-data checks passed

Confirmed source groups:
- Ausgrid 15-minute measured zone-substation demand, 2015–2025;
- NASA POWER hourly solar, wind and weather for 20 candidates, 2015–2025;
- Essential Energy network GIS and 2025–2034 capacity attributes;
- NSW livestock, cropping, forestry and organic-waste biomass layers;
- ABS population, Census and SA2 boundaries;
- PV module, inverter and wind-turbine libraries;
- CSIRO GenCost and DCCEEW operational factors;
- full checksums, licence register and data lineage.

## Scientific limitation that remains binding
Ausgrid measured load and Essential Energy target sites are different service territories. Ausgrid absolute megawatt values must never be labelled or transferred as measured target-site demand. Ausgrid provides temporal archetypes and source-domain validation only. Target scale uses independent Essential Energy network attributes and official statistical plausibility bounds.

## Model-ready scientific inputs — COMPLETE AND VALIDATED
Directory: `model_ready_inputs/`

Validated inputs now include:
- scale-sensitive PV, wind, biomass and battery costs;
- separate battery power and energy costs;
- LFP operating, efficiency, life, replacement and BLAST-Lite degradation configuration;
- discount rate, project horizon, escalation, salvage and regional CAPEX assumptions;
- feedstock energy content, recovery, losses, conversion efficiency, seasonality and dispatch limits;
- diesel price, truck fuel intensity, payload, handling, purchase and transport-cost ranges;
- outage, renewable-drought, heatwave, component-failure and supply-disruption scenarios;
- operational DCCEEW factors;
- official NREL lifecycle distributions for PV, wind, Li-ion storage and biopower;
- non-overlapping operational, attributional-lifecycle and consequential emissions protocols;
- target-load transfer and scaling protocols;
- mandatory data, ML, physics, surrogate and optimisation quality gates.

Latest successful validation:
- run `30492436522`
- zero errors and zero warnings in core model-ready validation;
- fuel/logistics validation passed;
- target-load scaling governance validation passed.

## Site-selection gate — APPROVED
Central clustering selected `k=5` and six detailed-study sites:
1. Suffolk Park — Byron Bay — cluster medoid;
2. Narrandera — resource extreme;
3. West Jemalong — Forbes — cluster medoid;
4. Bombala — cluster medoid;
5. Merrywinebone — Walgett–Lightning Ridge — cluster medoid;
6. Hallidays Point 11kV — cluster medoid and resilience challenge.

All spatial/data gates passed:
- 20 candidates;
- 11 weather years per site;
- no missing critical screening features;
- monotonic 25/50/100 km catchments;
- complete biomass coverage of NSW land within catchments;
- five clusters represented by six unique sites.

Nine-case sensitivity validation passed:
- median partition ARI: 0.7793;
- minimum partition ARI: 0.5847;
- median selection frequency of central shortlist: 0.8333.

The shortlist is approved but must be reproduced by Claude from the immutable source bundle before downstream modelling.

## Target-site demand scale anchors — COMPLETE
Official Essential Energy 2025–2034 attributes were matched to all six selected sites.

Derived proxy:
`network_capacity_implied_peak_proxy = n_1_nameplate_capacity - available_capacity_load_at_n_1`

This is an official-network-attribute-derived proxy, not measured demand.

2025 proxy values:
- Suffolk Park: 13.1 MW — radial/nonfirm flag;
- Narrandera: 10.3 MW;
- West Jemalong: 2.1 MW;
- Bombala: 5.0 MW;
- Merrywinebone: 2.6 MW — radial/nonfirm flag;
- Hallidays Point 11kV: 8.3 MW.

All matching, trajectory, year-range, owner and nonnegative-proxy checks passed.

## NREL lifecycle input gate — COMPLETE
Official workbook DOI `10.7799/1819907` was downloaded, hashed and parsed. Model-ready distributions include:
- photovoltaic;
- crystalline photovoltaic;
- wind;
- lithium-ion battery storage;
- direct-combustion biopower;
- gasification;
- gasification engine.

Operational, lifecycle and avoided-landfill accounting remain separate to prevent double counting.

## Methodology and Claude governance — COMPLETE
- `methodology/MODEL_SPECIFICATION.md`: full physics, economics, emissions, uncertainty, ML and optimisation formulation.
- `CLAUDE_CODE_MASTER_PROMPT.md`: stage-gated Claude Code execution plan from environment setup through manuscript production.
- Claude is forbidden from proceeding to a new stage without written ChatGPT approval.
- Every final Pareto point must be exactly re-simulated.
- Every figure and table must retain machine-readable source data.

## Work currently running
### Ausgrid source-load preparation v2
The first run identified three official interval-header variants: `24:00`, `24:00:00` and a trailing `00:00`. The corrected v2 parser handles all three while preserving the local AEST/AEDT operational-day convention. Current run: `30492636843`.

Expected outputs:
- QC for all 2,163 CSV files;
- daily features and normalised 96-interval shapes;
- station continuity and station-year archetype tables;
- explicit eligibility and failure reports.

### Australian Energy Statistics 2025
The workflow downloads official Tables B, F and L through direct verified government URLs with requests, curl and browser fallbacks. These tables provide independent NSW population/sector/electricity plausibility bounds for target-load scaling.

## Immediate next sequence
1. Close the corrected Ausgrid source-load preparation gate.
2. Close the Australian Energy Statistics download/parse gate.
3. Build Ausgrid source-load archetype clustering and pseudo-target shape validation.
4. Combine approved temporal shapes with official Essential Energy peak proxies and AES/ABS plausibility bounds.
5. Generate at least 100 probabilistic annual demand profiles per selected target site.
6. Run pseudo-target and uncertainty-coverage metrics.
7. Approve or reject the target-demand gate.
8. Only then hand Claude Code Stage 0 and begin implementation.

## Governance rule
No failed test, inaccessible source, unstable cluster, weak ML result, infeasible design or scientific limitation may be hidden. Claude must stop at every gate and wait for ChatGPT review.
