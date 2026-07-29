# Model-Ready Scientific Inputs — Regional NSW Microgrid

Version: 1.0.0-draft
Date: 30 July 2026

## Purpose
This package converts the validated public-data bundle into explicit, auditable engineering assumptions for a Python-based regional microgrid study. It is designed for execution by Claude Code under gated ChatGPT review.

## Governing principles
1. Direct official values, derived values, and scenario assumptions are never mixed silently.
2. Source currency and price basis are preserved. Technology costs remain in Australian dollars until a reporting-currency policy is approved.
3. Ausgrid measured demand is not labelled as measured demand for Essential Energy sites.
4. Every final Pareto solution must be re-simulated with the exact physics model.
5. Battery degradation is modelled with NREL BLAST-Lite where supported; a transparent rainflow/equivalent-full-cycle fallback is retained for verification.
6. Biomass recovery and logistics values are uncertain inputs and must remain within the declared scenario ranges.

## Files
- `technology_costs.csv`: capital-cost and O&M inputs, with scale-sensitive battery costs.
- `battery_parameters.csv`: operating limits, efficiency, lifetime, and degradation configuration.
- `financial_parameters.csv`: discount rate, study horizon, escalation, and optional carbon-price assumptions.
- `biomass_conversion_parameters.csv`: feedstock energy content, recovery, losses, and electrical-conversion ranges.
- `resilience_scenarios.csv`: outage and compound-event stress tests.
- `source_register.csv`: source provenance and access information.
- `load_transfer_protocol.yaml`: scientifically safe methodology for transferring Ausgrid load archetypes to regional target sites.
- `validation_protocol.yaml`: mandatory data, ML, physics, surrogate, and optimisation quality gates.
- `../scripts/validate_model_ready_inputs.py`: automated schema and consistency checks.

## Cost architecture
Battery capital cost is separated into power and energy terms. Two scale classes are retained:
- regional/substation scale, derived from CSIRO GenCost duration-specific battery and balance-of-plant values;
- community scale, based on Aurecon's 250 kW / 500 kWh reference system.

The optimiser must choose the applicable scale class or use a continuous/piecewise interpolation; it must not mix both cost bases in the same design.

## Status
This package is executable, but values marked `scenario_assumption` must be tested through sensitivity/uncertainty analysis and must not be presented as measured facts.
