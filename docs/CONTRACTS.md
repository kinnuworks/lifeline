# Lifeline run contract

Every module reads and writes files inside one run folder. Nothing else is shared.

```
runs/<run_id>/
  meta.json                 request, chosen image pairs, timings, versions, warnings
  aoi.geojson               the requested area (EPSG:4326)
  grid.json                 {"crs": "EPSG:326xx", "transform": [a,b,c,d,e,f], "width": W, "height": H, "pixel_m": 20}
  rasters/                  all on the grid above, Cloud-Optimized GeoTIFF
    dem.tif                 float32 metres (Copernicus GLO-30, resampled)
    hand.tif                float32 metres above nearest drainage
    slope.tif               float32 degrees
    flood_prob.tif          float32 0..1, probability the ground was flooded or buried by debris
    flood_class.tif         uint8: 0 unchanged, 1 open water, 2 debris / scour, 255 not observed
    observed.tif            uint8: number of radar passes that could see the pixel (0 = blind)
    model_prob.tif          float32 0..1, the trained segmentation model's water probability (if run)
    preview_before.png      RGB quicklook of the before radar (for the website)
    preview_after.png
    preview_change.png
  osm/                      pre-event OpenStreetMap, snapshot 2026-07-27 unless the date is earlier
    roads.geojson           LineString, props: osm_id, highway, name, bridge (bool), length_m
    buildings.geojson       Polygon or Point, props: osm_id
    bridges.geojson         LineString, props: osm_id, name, highway
    places.geojson          Point, props: osm_id, name, name_ne, place (city/town/village/hamlet), population (if tagged)
    health.geojson          Point, props: osm_id, name, kind (hospital/clinic/health_post)
  damage/
    roads.geojson           roads + props: hit_prob 0..1, status ("destroyed_likely","damaged_possible","clear","unobserved")
    buildings.geojson       buildings + props: hit_prob, status
    bridges.geojson         bridges + props: hit_prob, status
  access/
    settlements.geojson     places + props: p_cutoff 0..1, cutoff (bool at p>=0.5), nearest_health, minutes_before, minutes_after, walk_minutes_after, reason
    chokepoints.json        [{rank, osm_id, kind ("bridge"|"road"), name, lon, lat, villages_reconnected, people_reconnected, names: [...]}]
    flowpath.geojson        optional, from the bonus tool
  facts.json                THE LEDGER. Every number the copilot or the situation report may say.
                            [{"id": "F001", "key": "villages_cut_off", "value": 12, "unit": "villages", "label_en": "...", "label_ne": "...", "source": "access/settlements.geojson"}]
  sitrep_en.pdf, sitrep_ne.pdf, sitrep.json
  validation/emsr927.json   only for the case study; reference maps are read here and nowhere else
```

Rules
- Copernicus EMS, UNOSAT and post-event OpenStreetMap never enter any file above except `validation/`. Two tests check this: one scans the pipeline source for references to them, one checks that the comparison writes only under `validation/`.
- Every number shown to a user must exist in `facts.json`. The copilot guard rejects any other number.
- Coordinates on the website are EPSG:4326; rasters stay on the run grid and are reprojected for display.

Case study defaults
- AOI: 84.93, 27.78, 85.45, 28.30 (Rasuwa / Nuwakot / Dhading corridor, wide enough to hold every delivered official map area), event date 2026-08-26.
- Radar pairs (Sentinel-1D, same orbit): track 85 asc 2026-08-16 -> 2026-08-28; track 121 desc 2026-08-19 -> 2026-08-31; track 19 desc 2026-08-24 -> 2026-09-05.
