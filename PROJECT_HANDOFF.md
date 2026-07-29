# Regional NSW Microgrid Project — Handoff Status

_Last updated: 29 July 2026 (Melbourne time)_

## Project objective
Build a publication-grade, open-source, Python-based framework for renewable microgrid planning in regional New South Wales that is substantially stronger than the reference HOMER paper. The study will combine:

- real public Australian datasets;
- physics-based solar, wind, biomass and battery models;
- machine-learning clustering, forecasting and surrogate modelling;
- multi-objective and robust optimisation;
- uncertainty, resilience, degradation, explainability and sensitivity analysis;
- full reproducibility for eventual execution in Claude Code under ChatGPT supervision.

## Validated data bundle
The validated dataset is `regional-nsw-microgrid-data-bundle-v6` from GitHub Actions run `30434262791`.

The bundle passed all core validation checks and contains approximately 1.1 GB extracted data across roughly 2,500 files.

### Confirmed contents
- Real Ausgrid 15-minute zone-substation demand archives for 2015–2025.
- More than 2,000 extracted zone-substation load CSV files.
- NASA POWER hourly weather and solar-resource data for 20 selected sites, 2015–2025.
- Essential Energy network GIS layers.
- NSW biomass layers for livestock, cropping, forestry and organic waste.
- ABS population, Census and SA2 boundary datasets.
- PV module, inverter and wind-turbine component databases.
- CSIRO GenCost documents.
- A model-ready, source-traceable extract of DCCEEW 2025 National Greenhouse Accounts factors.
- Download manifests, checksums, licence register and validation reports.

## Important scientific limitation already identified
Ausgrid measured load data and Essential Energy regional GIS represent different service territories. Ausgrid load must not be described as measured demand for the selected Essential Energy regional sites. It may be used for:

- Australian load archetype development;
- clustering;
- transfer learning;
- data-derived synthetic community profiles;
- external forecasting validation.

Any manuscript wording must clearly distinguish measured load from transferred or reconstructed regional load profiles.

## Remaining model-ready inputs to complete before Claude execution
1. Battery model inputs:
   - capital cost split by power and energy;
   - cycle and calendar degradation;
   - replacement threshold;
   - round-trip efficiency;
   - depth of discharge;
   - C-rate;
   - temperature sensitivity.

2. Biomass conversion and logistics:
   - lower heating values by feedstock;
   - moisture correction;
   - recoverable fraction;
   - collection and storage loss;
   - transport cost and emissions;
   - conversion efficiency;
   - generator minimum load and ramp limits;
   - seasonal availability.

3. Resilience and reliability assumptions:
   - failure rates;
   - repair times;
   - outage durations;
   - critical-load fraction;
   - compound heatwave, low-wind and low-solar scenarios.

4. Economic inputs:
   - diesel price series or official scenario range;
   - discount rate scenarios;
   - technology replacement and salvage assumptions;
   - O&M escalation;
   - carbon-price scenarios where applicable.

5. Validation design:
   - PV and wind physical-model benchmark checks;
   - time-zone and daylight-saving treatment;
   - leakage-free temporal train/validation/test splits;
   - leave-one-site-out validation;
   - exact re-simulation of final Pareto solutions.

## Planned machine-learning components
- clustering of sites and load/resource archetypes;
- probabilistic load, solar and wind forecasting;
- baseline comparison against persistence, seasonal naïve and classical models;
- surrogate modelling for expensive optimisation;
- active learning to refine the surrogate;
- SHAP explainability;
- global sensitivity analysis.

## Planned optimisation structure
- investment decisions: PV, wind, biomass generator, battery energy, battery power and converter capacity;
- hourly or sub-hourly dispatch;
- multiple objectives: cost, emissions, reliability and resilience;
- robust/stochastic scenarios with CVaR;
- final Pareto solutions re-evaluated using the exact physics-based simulator.

## Next-session sequence
1. Build the `Model-Ready Scientific Inputs` package.
2. Lock the geographical scope and final study-site selection protocol.
3. Define the load-transfer methodology and manuscript-safe terminology.
4. Finalise physical equations, decision variables, constraints and objective functions.
5. Prepare Claude Code execution prompt, folder structure, deliverables and quality gates.
6. Begin implementation only after ChatGPT approval of all inputs and assumptions.

## Governance rule
Claude must work in gated stages. It must not proceed to the next stage until ChatGPT has reviewed the code, data outputs, logs, figures and scientific claims from the current stage.
