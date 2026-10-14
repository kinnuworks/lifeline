"""Who is cut off: road graph, Monte Carlo reachability, repair ranking.

Entry points
  lifeline.access.pipeline.run_access(run_dir, n_draws=1000, seed=7, unobserved_prior=0.15) -> summary dict
  lifeline.access.pipeline.summary(result, chokepoints, single_points, info) -> dict for the facts ledger
  lifeline.access.graph.load_graph(run_dir) / build_graph(roads_gdf) -> RoadGraph
  lifeline.access.montecarlo.analyse(run_dir, ...) -> AccessResult
  lifeline.access.chokepoints.rank_chokepoints(result) / single_points_of_failure(result)
"""
