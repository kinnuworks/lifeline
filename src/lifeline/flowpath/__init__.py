"""Bonus tool: trace the downstream flow path from a point and list settlements along it.

Entry points
  lifeline.flowpath.trace.run_flowpath(run_dir, lon, lat) -> dict, writes access/flowpath.geojson
  lifeline.flowpath.trace.trace_flowpath(dem, transform, crs, lon, lat, hand=None, places=None) -> dict
"""
