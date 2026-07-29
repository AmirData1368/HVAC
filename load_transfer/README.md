# Load Transfer and Probabilistic Target-Demand Package

Version: 1.0.0-draft  
Date: 30 July 2026

## Scientific role
Measured Ausgrid zone-substation demand is used only to learn Australian temporal demand archetypes, source-domain forecasting behaviour and uncertainty. The selected Essential Energy sites do not have matched public measured demand in this project.

Target-site demand is constructed in two independent layers:
1. **temporal shape layer** — learned from quality-controlled, normalised Ausgrid profiles;
2. **absolute scale layer** — estimated from target SA2 population, households, industry structure, official NSW electricity-consumption benchmarks and network-capacity constraints.

The two layers are combined probabilistically. Absolute Ausgrid megawatt values are never copied to a target site.

## Timestamp convention
Ausgrid states that the published zone-substation data use Australian Eastern Standard Time during winter and Australian Eastern Daylight Time during summer. The source files contain one local-date row and 96 interval-ending columns. The preprocessing package therefore:
- preserves the original date and interval label;
- sorts irregular FY2025 columns into chronological interval-ending order;
- treats `24:00` as the final interval of the stated local operational day;
- records the source clock convention as `AEST/AEDT local operational clock`;
- does not silently force ambiguous or nonexistent daylight-saving transition intervals into UTC;
- performs any UTC conversion only in a later explicitly audited long-format stage.

## Raw-data limitations
Ausgrid describes the data as raw SCADA/metering data that may include gaps, switching spikes and estimates based on assumed power factor. These issues are retained in QC reports and are not hidden by automatic smoothing.

## Outputs from source-domain preparation
- file-level schema and quality audit;
- station-name continuity map across financial years;
- daily demand features and normalised 96-interval shape descriptors;
- station-year archetype features;
- explicit eligibility flags for downstream load-shape modelling;
- artifact metadata and checksums.

## Release rule
No target-site demand ensemble may be generated until:
- source-domain QC passes;
- official absolute-scaling inputs pass;
- pseudo-target reconstruction outperforms simple baselines on held-out Ausgrid substations;
- ChatGPT approves the validation report.
