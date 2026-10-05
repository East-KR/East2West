"""Route layout: keep follow-up screens beside their parents without card overlap."""
from east2west.pwtest.map import layout


def edges(*pairs):
    return [{"src": src, "dst": dst} for src, dst in pairs]


def test_late_parent_child_and_grandchild_stay_on_the_same_row():
    order = ["start", "a", "b", "c", "child", "grandchild"]
    links = edges(("start", "a"), ("start", "b"), ("start", "c"),
                  ("c", "child"), ("child", "grandchild"))
    pos, paths = layout(order, links, "start")
    assert [pos[n][0] for n in ("c", "child", "grandchild")] == [1, 2, 3]
    assert pos["child"][1] == pos["c"][1] == pos["grandchild"][1]
    assert paths["grandchild"] == ["start", "c", "child", "grandchild"]


def test_branch_siblings_use_nearest_free_rows_and_preserve_other_parent_alignment():
    order = ["start", "a", "b", "c", "a-next", "b-next", "c-first", "c-second", "c-third", "last"]
    links = edges(("start", "a"), ("start", "b"), ("start", "c"),
                  ("a", "a-next"), ("b", "b-next"),
                  ("c", "c-first"), ("c", "c-second"), ("c", "c-third"), ("c-third", "last"))
    pos, _ = layout(order, links, "start")
    for parent, child in (("a", "a-next"), ("b", "b-next"), ("c", "c-first")):
        assert pos[parent][1] == pos[child][1]
    assert sorted(pos[n][1] for n in ("c-first", "c-second", "c-third")) == [2, 3, 4]
    assert pos["last"][1] == pos["c-third"][1]
    assert len(set(pos.values())) == len(order)


def test_merge_prefers_the_middle_of_its_parents_and_cycles_keep_shortest_paths():
    order = ["start", "a", "b", "c", "join", "last"]
    links = edges(("start", "a"), ("start", "b"), ("start", "c"),
                  ("a", "join"), ("c", "join"), ("join", "last"), ("last", "c"))
    pos, paths = layout(order, links, "start")
    assert pos["join"][1] == (pos["a"][1] + pos["c"][1]) / 2
    assert pos["last"][1] == pos["join"][1]
    assert paths["c"] == ["start", "c"]
    assert len(set(pos.values())) == len(order)
    assert layout(order, links, "start") == (pos, paths)


def test_empty_and_disconnected_graphs_remain_deterministic():
    assert layout([], [], None) == ({}, {})
    assert layout(["a", "b", "c"], [], None) == ({"a": (0, 0), "b": (1, 0), "c": (2, 0)}, {})
