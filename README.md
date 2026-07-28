# Regional NSW Renewable Microgrid Data Bundle

This repository builds a reproducible, source-traceable data bundle for physics-based and machine-learning modelling of solar-wind-bioenergy-battery microgrids in regional New South Wales, Australia.

The automated workflow downloads and validates public data from Essential Energy, NSW Government ArcGIS services, NASA POWER, ABS, AEMO, DCCEEW, CSIRO and the Australian Government petroleum statistics portal. It also exports public PV component databases and produces a SHA-256 manifest.

Generated datasets are published as GitHub Actions artifacts rather than committed to Git.
