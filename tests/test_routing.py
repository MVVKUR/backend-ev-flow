"""Unit tests for the pure Dijkstra core of the routing module.

These exercise the algorithm on a tiny hand-built graph — no road-network
download or networkx/osmnx required.
"""
import pytest

from api.routing import dijkstra, reconstruct


def make_adj():
    """Tiny directed graph. Edge = (neighbour, length_m, travel_time_s).

    Shortest by LENGTH:      A -> B -> C -> D  (100+100+100 = 300 m)
    Fastest by TRAVEL_TIME:  A -> C -> D       (5+5 = 10 s; the A->C edge is a
                             fast road, so it wins on time but loses on distance)
    """
    return {
        "A": [("B", 100.0, 10.0), ("C", 250.0, 5.0)],
        "B": [("C", 100.0, 9.0), ("D", 400.0, 40.0)],
        "C": [("D", 100.0, 5.0)],
        "D": [],
    }


@pytest.mark.unit
def test_shortest_by_length():
    adj = make_adj()
    dist, prev, _ = dijkstra(adj, "A", target="D", weight_idx=0)
    assert reconstruct(prev, "A", "D") == ["A", "B", "C", "D"]
    assert dist["D"] == 300.0  # beats A-C-D (350) and A-B-D (500)


@pytest.mark.unit
def test_fastest_by_time_picks_a_different_path():
    adj = make_adj()
    dist, prev, _ = dijkstra(adj, "A", target="D", weight_idx=1)
    assert reconstruct(prev, "A", "D") == ["A", "C", "D"]  # 10 s vs A-B-C-D = 24 s
    assert dist["D"] == 10.0


@pytest.mark.unit
def test_edge_used_records_both_metrics():
    adj = make_adj()
    _, _, edge_used = dijkstra(adj, "A", weight_idx=0)
    # incoming edge to D on the shortest-length path is C -> D = (100 m, 5 s)
    assert edge_used["D"] == (100.0, 5.0)


@pytest.mark.unit
def test_unreachable_returns_none():
    adj = {"A": [], "B": []}
    _, prev, _ = dijkstra(adj, "A")
    assert reconstruct(prev, "A", "B") is None


@pytest.mark.unit
def test_source_equals_target():
    adj = make_adj()
    _, prev, _ = dijkstra(adj, "A", target="A")
    assert reconstruct(prev, "A", "A") == ["A"]


@pytest.mark.unit
def test_single_source_reaches_all():
    adj = make_adj()
    dist, _, _ = dijkstra(adj, "A")  # no target -> full single-source
    assert set(dist) == {"A", "B", "C", "D"}
    assert dist["A"] == 0.0
