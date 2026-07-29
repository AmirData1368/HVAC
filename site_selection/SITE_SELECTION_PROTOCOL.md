# Regional NSW Microgrid Site-Selection Protocol

Version: 1.0.0-draft  
Date: 30 July 2026

## Objective
Select a small, defensible set of representative regional NSW study sites from the 20 validated Essential Energy candidate substations. Selection must be data-driven and reproducible, not based on manually choosing well-known towns.

## Source domains
- Candidate substations and network attributes: Essential Energy UHC GIS.
- Solar, wind and temperature: NASA POWER hourly data, 2015–2025.
- Population and current SA2 geometry: ABS 2021 Census and ASGS Edition 3.
- Crop, livestock and organic-waste biomass: NSW Bioenergy Assessment merged SA2 source regions.
- Forestry biomass: NSW Bioenergy Assessment FCNSW management areas.

## Spatial catchments
Each candidate is evaluated using 25 km, 50 km and 100 km radial catchments in Australian Albers (EPSG:3577).

For a published source-region polygon `j` and site buffer `i`, the allocated resource is:

`allocated_ij = published_resource_j × area(intersection(i,j)) / area(j)`

The catchment total is the sum across intersecting polygons.

This is an explicit area-weighted proxy. It assumes resource density is uniform inside each published source region. The paper must report this assumption and test 25/50/100 km radii and recoverable-fraction uncertainty. It must not describe the allocated quantity as a point measurement at the substation.

## Double-counting controls
Crop totals use detailed non-overlapping component layers. Aggregate layers are excluded:
- exclude Cereal Straw aggregate layer 0;
- exclude Non-cereal Straw aggregate layer 8;
- exclude Sugarcane All Residues aggregate layer 16;
- include detailed layers 1–7, 9–15 and 17–18.

Organic waste uses the all-organic-waste layer only; its MSW, commercial-and-industrial, and construction-and-demolition sublayers are retained for later composition analysis but are not summed again.

Livestock uses total volatile solids for dairy, piggery and poultry. Forestry harvest and sawmill categories are summed because they are distinct residue streams, but later logistics modelling must prevent the same material from being allocated to more than one conversion pathway.

## Screening features
The central clustering uses:
- mean daily global horizontal irradiance;
- solar daily coefficient of variation;
- mean and 90th-percentile wind speed at 50 m;
- wind coefficient of variation;
- mean and 95th-percentile temperature;
- annual cooling-degree hours above 24 °C;
- daily solar–wind complementarity;
- crop residues within 50 km;
- manure volatile solids within 50 km;
- organic waste within 50 km;
- forestry residues within 50 km;
- area-weighted population within 50 km;
- population density of the containing 2021 SA2.

Skewed resource and population variables are transformed with `log1p`; all features are scaled with a robust scaler.

## Cluster-number and algorithm selection
Candidate values `k = 3…6` are evaluated using:
- K-means with 100 initialisations;
- diagonal-covariance Gaussian mixture modelling;
- Ward hierarchical clustering;
- silhouette coefficient;
- Calinski–Harabasz index;
- Davies–Bouldin index;
- K-means perturbation/subsampling stability using adjusted Rand index;
- agreement among the three algorithms using adjusted Rand index.

The selected `k` maximises a rank-averaged composite across these metrics. The full metric table is retained; no single favourable metric may be reported alone.

## Detailed-site shortlist
The shortlist contains:
1. one medoid-like site nearest the K-means centroid of each selected cluster;
2. one additional site with the highest balanced resource-diversity score when it is not already a medoid;
3. one additional resilience-challenging site when it is not already selected.

The intended final size is five to seven sites. All selected clusters must be represented.

The resource-diversity score combines solar, wind, solar–wind complementarity and logarithmic biomass resource quantities. The challenge score combines high temperature, high solar variability, low wind and low dispatchable biomass. These scores are screening devices, not techno-economic objective values.

## Mandatory validation gates
- at least 20 candidate sites;
- 11 complete weather years per site;
- no missing critical screening variables;
- 25 km totals ≤ 50 km totals ≤ 100 km totals;
- at least 90% source-region geometric coverage for every 50 km biomass catchment;
- five to seven shortlisted sites;
- all clusters represented;
- no duplicate site;
- exact output data and run configuration saved.

## Scientific wording
Allowed:
- area-weighted catchment biomass estimate;
- representative regional site;
- data-driven site archetype;
- source-region allocated resource estimate.

Forbidden:
- measured biomass at the selected substation;
- exact local feedstock availability;
- nationally representative Australian sites.

## Release rule
Site selection is provisional until the geometry, catchment, cluster-stability and shortlist validation reports all pass and ChatGPT approves the results. Claude Code may not begin detailed optimisation before this gate is closed.
