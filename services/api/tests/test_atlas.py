"""The Atlas's walks (F10a): impact goes downstream nearest first, each node once at its shortest distance, through
cycles, within its depth and its node limit; a path is the fewest edges either way along each, none past its depth;
and a walk sees only the edges the database gave it, so what a person can't see stops it."""
from kl.atlas import shortest_path, walk

EDGES = [("src", "load", "reads"), ("load", "orders", "writes"), ("orders", "report", "reads"), ("report", "mart", "writes"),
         ("src", "orders", "feeds"), ("mart", "src", "feeds")]  # a shortcut to orders, and a cycle back to src


def downstream(edges):
    return lambda frontier: [e for e in edges if e[0] in frontier]


def touching(edges):
    return lambda frontier: [e for e in edges if e[0] in frontier or e[1] in frontier]


def test_impact_is_nearest_first_each_node_once_through_a_cycle():
    assert walk("src", downstream(EDGES), depth=6) == {"load": 1, "orders": 1, "report": 2, "mart": 3}


def test_impact_stops_at_its_depth_and_its_node_limit():
    assert walk("src", downstream(EDGES), depth=1) == {"load": 1, "orders": 1}
    assert len(walk("src", downstream(EDGES), depth=6, limit=2)) == 2


def test_a_path_is_the_fewest_edges_either_way_along_each():
    assert shortest_path("mart", "load", touching(EDGES), depth=6) == [("mart", "src", "feeds"), ("src", "load", "reads")]
    assert shortest_path("load", "load", touching(EDGES), depth=6) == []


def test_no_path_past_its_depth():
    chain = [("a", "b", "feeds"), ("b", "c", "feeds"), ("c", "d", "feeds")]
    assert shortest_path("a", "d", touching(chain), depth=2) == []
    assert len(shortest_path("a", "d", touching(chain), depth=3)) == 3


def test_a_walk_never_passes_an_edge_it_was_not_given():
    visible = [e for e in EDGES if e != ("load", "orders", "writes") and e != ("src", "orders", "feeds")]
    assert walk("src", downstream(visible), depth=6) == {"load": 1}
    assert shortest_path("src", "report", touching(visible), depth=6) == [("mart", "src", "feeds"), ("report", "mart", "writes")]
