"""Shortest-path routing over the Jakarta drivable road network.

EV-FLOW Epic 2.0 (Route & Battery). The road graph is built once with OSMnx —
offline via ``scripts/build_road_graph.py`` or lazily on first request — and
cached on disk as GraphML. At request time we load it with NetworkX, snap the
origin and destination to the nearest road nodes (great-circle), and run our own
Dijkstra over a plain adjacency map.

Design notes
------------
* The Dijkstra implementation (:func:`dijkstra` / :func:`reconstruct`) is pure and
  does no I/O, so it is unit-testable without any graph download.
* Runtime needs only ``networkx`` (to read GraphML). ``osmnx`` is required *only*
  to build the graph the first time; once cached it is never imported again.
* Heavy imports (networkx / osmnx) are deferred into functions so importing this
  module stays cheap and the rest of the API never depends on them.
"""
from __future__ import annotations

import heapq
import os
from math import inf
from pathlib import Path
from typing import Optional

# numpy and the data layer (pandas) are imported lazily inside the functions that
# need them, so the pure Dijkstra core stays import-light and unit-testable.

ROOT = Path(__file__).resolve().parent.parent
GRAPH_PATH = Path(os.getenv("ROAD_GRAPH_PATH", ROOT / "data" / "processed" / "jakarta_drive.graphml"))

# Same Jabodetabek bounding box used by the analysis notebook / data layer.
BBOX = {
    "south": float(os.getenv("JAKARTA_BBOX_SOUTH", -6.3760)),
    "west": float(os.getenv("JAKARTA_BBOX_WEST", 106.6894)),
    "north": float(os.getenv("JAKARTA_BBOX_NORTH", -6.0890)),
    "east": float(os.getenv("JAKARTA_BBOX_EAST", 106.9710)),
}

# Fallback when an edge has no travel_time attribute (km/h).
DEFAULT_SPEED_KMH = float(os.getenv("ROUTING_DEFAULT_SPEED_KMH", 40))


class GraphUnavailable(RuntimeError):
    """Raised when the road graph cannot be loaded or built."""


# ----------------------------------------------------------------------------- pure algorithm
def dijkstra(adj: dict, source, target=None, weight_idx: int = 0):
    """Single-source Dijkstra over an adjacency map.

    ``adj``: ``{node: [(neighbour, length_m, travel_time_s), ...]}``.
    ``weight_idx``: 0 minimises ``length_m``, 1 minimises ``travel_time_s``.

    Returns ``(dist, prev, edge_used)`` where ``edge_used[v]`` is the
    ``(length_m, time_s)`` of the chosen incoming edge to ``v`` — so a caller can
    sum *both* metrics along the reconstructed path regardless of which one was
    minimised.
    """
    dist = {source: 0.0}
    prev: dict = {}
    edge_used: dict = {}
    visited: set = set()
    pq = [(0.0, source)]
    while pq:
        d, u = heapq.heappop(pq)
        if u in visited:  # stale heap entry
            continue
        visited.add(u)
        if u == target:
            break
        for edge in adj.get(u, ()):  # edge = (neighbour, length_m, time_s)
            v = edge[0]
            nd = d + edge[1 + weight_idx]
            if nd < dist.get(v, inf):
                dist[v] = nd
                prev[v] = u
                edge_used[v] = (edge[1], edge[2])
                heapq.heappush(pq, (nd, v))
    return dist, prev, edge_used


def reconstruct(prev: dict, source, target) -> Optional[list]:
    """Rebuild the node path ``source -> target`` from a ``prev`` map.

    Returns the list of nodes, or ``None`` if ``target`` is unreachable.
    """
    if target != source and target not in prev:
        return None
    path = [target]
    while path[-1] != source:
        path.append(prev[path[-1]])
    path.reverse()
    return path


# ----------------------------------------------------------------------------- graph cache
_ADJ: Optional[dict] = None
_NODES: Optional[dict] = None  # node -> (lat, lon)
_NODE_IDS = None               # np.ndarray of node ids
_NODE_LAT = None               # np.ndarray of latitudes
_NODE_LON = None               # np.ndarray of longitudes
_STATION_SNAP = None           # (signature, np.ndarray of snapped node ids) — cache


def _build_adjacency(graph):
    """Convert a NetworkX (Multi)DiGraph into our adjacency map + node coords.

    GraphML attributes load as strings, so everything is coerced to float here.
    """
    adj: dict = {}
    nodes: dict = {}
    for n, ndata in graph.nodes(data=True):
        nodes[n] = (float(ndata["y"]), float(ndata["x"]))  # y=lat, x=lon
        adj.setdefault(n, [])
    for u, v, edata in graph.edges(data=True):
        length = float(edata.get("length", 0.0) or 0.0)
        tt = edata.get("travel_time")
        time_s = float(tt) if tt not in (None, "") else (length / 1000.0) / DEFAULT_SPEED_KMH * 3600.0
        adj.setdefault(u, []).append((v, length, time_s))
        adj.setdefault(v, [])
    return adj, nodes


def _load_or_build_graph():
    try:
        import networkx as nx
    except ImportError as e:  # pragma: no cover - environment dependent
        raise GraphUnavailable("networkx is not installed (pip install networkx)") from e

    if GRAPH_PATH.exists():
        return nx.read_graphml(GRAPH_PATH)

    # No cached graph -> build it with OSMnx (heavier; needs network access).
    try:
        import osmnx as ox
    except ImportError as e:  # pragma: no cover - environment dependent
        raise GraphUnavailable(
            f"road graph not found at {GRAPH_PATH} and osmnx is not installed to build it. "
            f"Run `python scripts/build_road_graph.py` first."
        ) from e

    try:  # osmnx >= 2 signature
        graph = ox.graph_from_bbox(
            bbox=(BBOX["west"], BBOX["south"], BBOX["east"], BBOX["north"]),
            network_type="drive",
        )
    except TypeError:  # osmnx 1.x signature
        graph = ox.graph_from_bbox(
            north=BBOX["north"], south=BBOX["south"],
            east=BBOX["east"], west=BBOX["west"], network_type="drive",
        )
    graph = ox.add_edge_speeds(graph)
    graph = ox.add_edge_travel_times(graph)
    GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)
    ox.save_graphml(graph, GRAPH_PATH)
    return graph


def _ensure_loaded() -> None:
    global _ADJ, _NODES, _NODE_IDS, _NODE_LAT, _NODE_LON
    if _ADJ is not None:
        return
    import numpy as np

    adj, nodes = _build_adjacency(_load_or_build_graph())
    if not nodes:
        raise GraphUnavailable("road graph has no nodes")
    ids = list(nodes.keys())
    _ADJ = adj
    _NODES = nodes
    _NODE_IDS = np.array(ids, dtype=object)
    _NODE_LAT = np.array([nodes[i][0] for i in ids], dtype=float)
    _NODE_LON = np.array([nodes[i][1] for i in ids], dtype=float)


def reload() -> None:
    """Drop the cached graph (e.g. after rebuilding the GraphML)."""
    global _ADJ, _NODES, _NODE_IDS, _NODE_LAT, _NODE_LON, _STATION_SNAP
    _ADJ = _NODES = _NODE_IDS = _NODE_LAT = _NODE_LON = _STATION_SNAP = None


# ----------------------------------------------------------------------------- public API
def nearest_node(lat: float, lon: float):
    """Snap a (lat, lon) to the nearest road-graph node. Returns (node, km)."""
    import numpy as np

    from .data import haversine_km

    _ensure_loaded()
    d = haversine_km(lat, lon, _NODE_LAT, _NODE_LON)
    i = int(np.argmin(d))
    return _NODE_IDS[i], float(d[i])


def shortest_path(orig_lat: float, orig_lon: float,
                  dest_lat: float, dest_lon: float, weight: str = "length") -> Optional[dict]:
    """Shortest driving path between two points.

    Snaps both ends to the road graph and runs Dijkstra. ``weight`` is
    ``"length"`` (metres, shortest) or ``"travel_time"`` (seconds, fastest).
    Returns a dict shaped like the :class:`api.models.Route` schema, or ``None``
    if the destination is unreachable from the origin.
    """
    _ensure_loaded()
    weight_idx = 1 if weight == "travel_time" else 0
    o_node, o_km = nearest_node(orig_lat, orig_lon)
    d_node, d_km = nearest_node(dest_lat, dest_lon)

    _, prev, edge_used = dijkstra(_ADJ, o_node, target=d_node, weight_idx=weight_idx)
    path = reconstruct(prev, o_node, d_node)
    if path is None:
        return None

    total_len = sum(edge_used[v][0] for v in path[1:])
    total_time = sum(edge_used[v][1] for v in path[1:])
    coords = [[_NODES[n][1], _NODES[n][0]] for n in path]  # [lon, lat]
    if len(coords) == 1:  # origin == destination node -> valid 2-point LineString
        coords = [coords[0], coords[0]]

    return {
        "weight": weight,
        "distance_m": round(total_len, 1),
        "duration_s": round(total_time, 1),
        "origin": {"lat": orig_lat, "lon": orig_lon,
                   "snapped_node": str(o_node), "snap_distance_km": round(o_km, 4)},
        "destination": {"lat": dest_lat, "lon": dest_lon,
                        "snapped_node": str(d_node), "snap_distance_km": round(d_km, 4)},
        "node_count": len(path),
        "geometry": {"type": "LineString", "coordinates": coords},
    }


def _snap_stations(ids: list, lats, lons):
    """Snap every station to its nearest road node.

    O(stations × nodes); cached per station set (keyed by count + first/last id),
    since the station list is static and loaded once. The first call after startup
    pays the snapping cost, subsequent requests reuse it.
    """
    global _STATION_SNAP
    import numpy as np

    from .data import haversine_km

    _ensure_loaded()
    sig = (len(ids), ids[0] if ids else None, ids[-1] if ids else None)
    if _STATION_SNAP is not None and _STATION_SNAP[0] == sig:
        return _STATION_SNAP[1]
    nodes = np.empty(len(ids), dtype=object)
    for i in range(len(ids)):
        d = haversine_km(float(lats[i]), float(lons[i]), _NODE_LAT, _NODE_LON)
        nodes[i] = _NODE_IDS[int(np.argmin(d))]
    _STATION_SNAP = (sig, nodes)
    return nodes


def nearest_station_route(orig_lat: float, orig_lon: float,
                          station_ids: list, station_lats, station_lons,
                          weight: str = "length", max_range_km: Optional[float] = None) -> Optional[dict]:
    """Nearest charging station reachable by road from the origin, plus the route.

    Runs a single single-source Dijkstra from the origin (cost to every node), then
    picks the station with the lowest cost. ``max_range_km`` (EV remaining range)
    does not change the choice — the nearest station is always the best candidate —
    but flags ``within_range`` when that nearest station's road distance exceeds it.
    Returns ``None`` if no station is reachable by road.
    """
    from .data import haversine_km

    _ensure_loaded()
    weight_idx = 1 if weight == "travel_time" else 0
    o_node, o_km = nearest_node(orig_lat, orig_lon)
    dist, prev, edge_used = dijkstra(_ADJ, o_node, target=None, weight_idx=weight_idx)

    snapped = _snap_stations(list(station_ids), station_lats, station_lons)
    best_cost, best_i, best_node = inf, -1, None
    considered = 0
    for i, node in enumerate(snapped):
        c = dist.get(node, inf)
        if c == inf:
            continue
        considered += 1
        if c < best_cost:
            best_cost, best_i, best_node = c, i, node
    if best_i < 0:
        return None

    path = reconstruct(prev, o_node, best_node)
    total_len = sum(edge_used[v][0] for v in path[1:]) if len(path) > 1 else 0.0
    total_time = sum(edge_used[v][1] for v in path[1:]) if len(path) > 1 else 0.0
    coords = [[_NODES[n][1], _NODES[n][0]] for n in path]
    if len(coords) == 1:
        coords = [coords[0], coords[0]]

    within = max_range_km is None or (total_len / 1000.0) <= max_range_km
    slat, slon = float(station_lats[best_i]), float(station_lons[best_i])
    nlat, nlon = _NODES[best_node]
    snap_km = float(haversine_km(slat, slon, nlat, nlon))

    return {
        "station_id": station_ids[best_i],
        "candidates_considered": considered,
        "within_range": within,
        "route": {
            "weight": weight,
            "distance_m": round(total_len, 1),
            "duration_s": round(total_time, 1),
            "origin": {"lat": orig_lat, "lon": orig_lon,
                       "snapped_node": str(o_node), "snap_distance_km": round(o_km, 4)},
            "destination": {"lat": slat, "lon": slon, "snapped_node": str(best_node),
                            "snap_distance_km": round(snap_km, 4),
                            "station_id": str(station_ids[best_i])},
            "node_count": len(path),
            "geometry": {"type": "LineString", "coordinates": coords},
        },
    }
