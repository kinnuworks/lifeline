---
title: Lifeline engine
emoji: 🛰️
colorFrom: gray
colorTo: yellow
sdk: docker
app_port: 7860
pinned: false
short_description: Flood damage and cut-off villages from free satellite data
---

# Lifeline engine

The live API behind the Lifeline dashboard. Give it an area and a flood date; it reads Sentinel-1, Sentinel-2, the Copernicus DEM and pre-flood OpenStreetMap, maps the damage, finds the villages cut off from care and writes the situation report in English and Nepali.

- `GET /health`
- `POST /runs {"bbox": [W, S, E, N], "date": "YYYY-MM-DD", "label": "..."}` then poll `GET /runs/{id}`
- `POST /ask {"run_id", "question", "lang"}`, `POST /flowpath {"run_id", "lon", "lat"}`

Code, method and results: see the project repository linked from the dashboard.

Contains modified Copernicus Sentinel data. Produced using Copernicus WorldDEM-30 © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018 provided under COPERNICUS by the European Union and ESA; all rights reserved. © OpenStreetMap contributors.
