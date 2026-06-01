"""Build & cache the Jakarta drivable road graph used by the routing endpoint.

Run once (it downloads the road network for the Jabodetabek bbox and writes a
GraphML file). The API then loads that file with NetworkX at request time, so
the production server does not need OSMnx or any network access to route.

Usage:
    python scripts/build_road_graph.py

Requires ``osmnx`` (already in requirements.txt). Output path is controlled by
the ``ROAD_GRAPH_PATH`` env var (default ``data/processed/jakarta_drive.graphml``).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Make the `api` package importable when run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.routing import BBOX, GRAPH_PATH  # noqa: E402


def main() -> None:
    try:
        import osmnx as ox
    except ImportError:
        sys.exit("osmnx is required to build the graph: pip install osmnx")

    print(f"Building drivable road graph for Jabodetabek bbox: {BBOX}")
    try:  # osmnx >= 2 signature: bbox=(left, bottom, right, top)
        graph = ox.graph_from_bbox(
            bbox=(BBOX["west"], BBOX["south"], BBOX["east"], BBOX["north"]),
            network_type="drive",
        )
    except TypeError:  # osmnx 1.x signature
        graph = ox.graph_from_bbox(
            north=BBOX["north"], south=BBOX["south"],
            east=BBOX["east"], west=BBOX["west"], network_type="drive",
        )

    graph = ox.add_edge_speeds(graph)        # infer km/h per highway type
    graph = ox.add_edge_travel_times(graph)  # -> 'travel_time' (seconds) per edge

    GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)
    ox.save_graphml(graph, GRAPH_PATH)
    print(f"Saved {graph.number_of_nodes():,} nodes / {graph.number_of_edges():,} edges "
          f"-> {GRAPH_PATH}")


if __name__ == "__main__":
    main()
