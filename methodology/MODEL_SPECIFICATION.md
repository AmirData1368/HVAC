# Physics–Machine-Learning–Optimisation Model Specification

Version: 1.0.0-draft  
Date: 30 July 2026

## 1. Core research question
How should solar photovoltaic, wind, bioenergy, battery power, battery energy and power-electronic capacity be jointly sized and operated for representative regional NSW load/resource archetypes when cost, greenhouse-gas emissions, reliability, degradation and resilience are evaluated under multi-year weather, transferred-load and feedstock uncertainty?

## 2. System boundary
The core model is an autonomous hybrid microgrid. An optional edge-of-grid comparator may be activated later, but it is not permitted to enter the core results until transparent electricity-price and grid-exchange data pass a separate data gate.

Core technologies:
- solar photovoltaic array;
- one selected wind-turbine model and integer turbine count;
- solid-biomass gasification/engine or biogas engine pathway, selected by site feedstock;
- lithium-iron-phosphate battery with independent power and energy sizing;
- bidirectional converter and internal alternating-current bus;
- optional emergency diesel generator only in an explicitly labelled comparator.

Core configurations to compare:
1. diesel-only baseline;
2. solar photovoltaic–battery;
3. solar photovoltaic–wind–battery;
4. solar photovoltaic–bioenergy–battery;
5. solar photovoltaic–wind–bioenergy–battery;
6. configuration 5 with emergency diesel.

## 3. Temporal design
- Raw measured source-domain load: 15-minute Ausgrid data.
- Target-site demand: probabilistic real-data-derived profiles produced by the approved transfer protocol.
- Planning dispatch: hourly.
- Operational validation: selected 15-minute normal and stress periods.
- Weather: all complete years from 2015–2025.
- Study horizon: 20–30 years, central value 25 years.
- Battery degradation: updated at least annually and after each representative stress sequence.

All 11 weather years remain available. Scenario reduction may be used for optimisation speed, but the final Pareto designs must be re-simulated across every retained historical year plus mandatory extreme scenarios.

## 4. Capacity decision variables
Continuous variables:
- `C_pv_dc` — installed photovoltaic direct-current capacity [kW];
- `C_bat_e` — installed battery energy capacity [kWh];
- `C_bat_p` — installed battery charge/discharge power [kW];
- `C_converter` — converter capacity [kW];
- `C_bio` — bioenergy generator capacity [kW];
- `M_store` — usable feedstock storage capacity [t or m³].

Discrete variables:
- `N_wind` — integer number of turbines;
- `m_wind` — selected turbine model;
- `m_bio` — solid-biomass or biogas conversion pathway;
- `m_bess_cost` — community-scale or regional/substation-scale cost class;
- `C_diesel` — optional emergency-diesel capacity, enabled only in configuration 6.

The optimiser may not mix community-scale and regional-scale BESS cost equations in one design.

## 5. Operational variables by time and scenario
- photovoltaic and wind power used and curtailed;
- battery charging and discharging power;
- battery stored energy, state of charge and state of health;
- bioenergy generator commitment, output, start-up and shut-down state;
- feedstock consumed, delivered and stored;
- diesel output and fuel use when enabled;
- served load, unmet load and curtailed load;
- converter flows and losses.

## 6. Solar photovoltaic model
Solar position, irradiance transposition, module temperature, direct-current power, inverter conversion and losses are evaluated with `pvlib ModelChain` or an equivalently validated implementation.

For each time step:

`P_pv_ac(t) = min[C_converter, f_inverter(P_dc_stc × f_pv(G_poa(t), T_cell(t)) × (1 - L_pv))]`

Mandatory effects:
- plane-of-array irradiance;
- cell-temperature derating;
- inverter part-load efficiency;
- soiling and availability losses;
- annual module degradation;
- clipping and curtailment.

## 7. Wind model
Wind speed is adjusted from reference height to hub height:

`v_h(t) = v_ref(t) × (h_h / h_ref)^alpha(t)`

Air-density correction is applied through an equivalent speed:

`v_eq(t) = v_h(t) × [rho(t) / rho_0]^(1/3)`

Generation is obtained from the selected manufacturer/NREL SAM power curve:

`P_wind(t) = N_wind × f_curve(v_eq(t)) × (1 - L_wind)`

Cut-in, rated, cut-out, availability and optional turbulence losses are enforced. The shear exponent is uncertain and must be sensitivity-tested when no measured vertical wind profile exists.

## 8. Bioenergy and feedstock inventory
For each feedstock class `f`:

`M_f(t+1) = (1 - loss_store,f × delta_t) M_f(t) + A_f(t) - F_f(t)`

where `A_f` is delivered feedstock and `F_f` is consumed feedstock. Annual use may not exceed the recoverable catchment resource after collection and storage losses.

For a solid feedstock:

`P_bio(t) = eta_e(t) × F(t) × LHV × 277.7778 / delta_t`

where `F` is tonnes consumed in the time step, `LHV` is GJ/t and power is kW. Equivalent unit-consistent equations are used for biogas in m³.

Mandatory constraints:
- pathway-specific energy content;
- moisture basis consistency;
- recoverable fraction;
- seasonal supply;
- storage loss;
- minimum stable load;
- ramp rate;
- generator availability;
- resource-weighted transport distance;
- transport cost and transport emissions;
- no double allocation of one feedstock stream.

## 9. Battery model
Stored energy evolves as:

`E(t+1) = E(t)(1 - sigma delta_t) + eta_ch P_ch(t) delta_t - P_dis(t) delta_t / eta_dis`

with:

`SOC_min × SOH(t) × C_bat_e <= E(t) <= SOC_max × SOH(t) × C_bat_e`

and:

`P_ch(t), P_dis(t) <= C_bat_p`

`C_bat_p <= C_rate × C_bat_e`

Simultaneous charging and discharging are forbidden. The primary ageing model is an LFP model from NREL BLAST-Lite. Exact package version, model class, temperature input, SOC history and cycling history must be recorded. A rainflow/equivalent-full-cycle model is retained only as a transparent fallback and cross-check.

Replacement or augmentation occurs when the selected economic life is reached or remaining usable capacity falls below the selected end-of-life threshold. Replacement, augmentation and fixed O&M may not be double counted.

## 10. Power balance
For every time step and scenario:

`P_pv + P_wind + P_bio + P_diesel + P_dis = P_load_served + P_ch + P_curtail + P_losses`

`P_load = P_load_served + P_unmet`

All terms use the same bus convention and units. Relative energy-balance residual must remain below `1e-6`.

## 11. Reliability metrics
- Expected energy not served: `EENS = sum(P_unmet delta_t)`.
- Loss-of-power-supply probability: `LPSP = EENS / sum(P_load delta_t)`.
- Loss-of-load hours: hours with non-zero unmet load.
- Renewable fraction: renewable energy used divided by served load, with reporting convention stated.
- Curtailment fraction: curtailed renewable energy divided by available renewable energy.

Reliability is both an objective and a feasibility gate. The manuscript must not rely on renewable fraction alone.

## 12. Resilience metrics
For each mandatory disruption scenario and sampled event start time:
- critical-load served fraction;
- survival probability with zero unmet critical load;
- hours of autonomy;
- critical expected energy not served;
- recovery time where restoration is modelled;
- resilience-triangle loss.

A normalised resilience index is:

`RI = 1 - [sum(P_unmet,critical delta_t) / sum(P_critical delta_t)]`

Results are reported for 24-, 72- and 168-hour events and 50%, 75% and 100% critical-load definitions, plus compound-weather and component-failure scenarios.

## 13. Economic model
Net present cost is:

`NPC = CAPEX + PV(O&M + fuel + transport + replacements + start-up costs) - PV(salvage)`

All base economic calculations use real Australian dollars and the selected real pre-tax discount rate. Source price years remain explicit. Currency conversion is prohibited until a reporting-currency policy and dated exchange-rate source are approved.

Levelised cost of served electricity is:

`LCOE = NPC / PV(served electrical energy)`

Unserved energy is not counted in the denominator. Cost of unserved energy may be reported separately but must not be hidden inside LCOE.

## 14. Greenhouse-gas system boundary
The core emissions account includes:
- diesel combustion and upstream fuel emissions;
- biomass non-biogenic combustion gases where applicable;
- feedstock transport;
- avoided landfill emissions only in a separately reported consequential scenario;
- replacement-related battery emissions when a validated lifecycle factor is used;
- technology lifecycle factors for photovoltaic, wind and storage only after the NREL lifecycle dataset is ingested and its functional units are reconciled.

Until that lifecycle-input gate passes, the primary metric must be called `system-boundary greenhouse-gas emissions`, not full lifecycle emissions.

## 15. Objectives
The principal many-objective problem minimises:
1. net present cost;
2. system-boundary greenhouse-gas emissions;
3. expected energy not served;
4. resilience loss or 95% conditional value at risk of critical unmet energy.

The robust cost option is:

`min E[NPC] + lambda_cost × CVaR_0.95(NPC)`

The robust reliability option is:

`min E[EENS] + lambda_rel × CVaR_0.95(EENS)`

Weights are not fixed silently; multiple risk-aversion values are analysed.

## 16. Uncertainty and scenarios
Uncertain variables include:
- weather year and renewable drought;
- load archetype, annual energy and peak scaling;
- forecast error;
- equipment cost;
- discount rate;
- battery efficiency and degradation;
- feedstock recoverable fraction, moisture, seasonal supply and price;
- regional construction multiplier;
- component availability and repair duration.

Scenario generation uses historical years, block bootstrap, Latin hypercube/Sobol sampling and explicitly constructed compound events. Scenario reduction must preserve marginal distributions, correlations, extremes and annual energy.

## 17. Machine-learning roles
### 17.1 Site and load archetypes
Clustering selects representative sites and measured-load archetypes. Pseudo-target validation is mandatory before target profiles are released.

### 17.2 Probabilistic forecasting
Load, solar and wind forecasts are compared against persistence, seasonal-naïve and classical baselines. Candidate models may include CatBoost, temporal convolutional networks, LSTM and Temporal Fusion Transformer. Model selection is empirical, not predetermined.

Required outputs include quantiles 0.05, 0.25, 0.50, 0.75 and 0.95. Required evaluation includes rolling-origin validation, leave-one-site-out validation, MAE, RMSE, pinball loss, interval coverage and CRPS.

### 17.3 Surrogate modelling
The exact simulator is sampled with Latin hypercube or Sobol designs. Candidate surrogates include CatBoost, Extra Trees, Gaussian process regression and neural ensembles. The surrogate predicts cost, emissions, EENS, resilience, curtailment, renewable fraction, battery degradation and feedstock use.

Active learning adds exact simulations where predictive uncertainty, Pareto relevance or constraint ambiguity is high. Every final Pareto candidate is re-simulated exactly.

### 17.4 Explainability
SHAP and global sensitivity analysis are used to identify drivers, nonlinear thresholds and interactions. Scenario assumptions must appear in global sensitivity analysis.

## 18. Optimisation architecture
- Exact operational layer: Pyomo MILP or a mathematically equivalent open-source formulation.
- Outer capacity search: NSGA-III or MOEA/D for the four-objective problem.
- Verification: epsilon-constraint solutions on reduced benchmark cases.
- Acceleration: validated ML surrogate plus active learning.
- Reproducibility: at least 10 optimisation seeds, saved convergence histories and deterministic software environments.

## 19. Experimental matrix
For every selected site:
- all six configurations;
- deterministic central inputs;
- multi-year historical uncertainty;
- robust risk-aversion cases;
- 25/50/100 km biomass catchments;
- battery cost/degradation cases;
- feedstock recovery cases;
- resilience disruptions;
- diesel and no-diesel comparisons.

## 20. Non-negotiable release gates
Claude Code may not progress beyond a stage until ChatGPT approves:
1. data and unit audit;
2. load-transfer pseudo-target validation;
3. site-selection validation;
4. component-model benchmark tests;
5. exact dispatch feasibility and energy balance;
6. forecasting benchmark results;
7. surrogate independent-test performance;
8. optimisation convergence and exact Pareto re-simulation;
9. figure/table traceability;
10. manuscript claims against the actual source and model boundary.
