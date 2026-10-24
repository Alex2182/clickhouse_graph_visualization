"""Regression test for _build_graph (offline, no ClickHouse server).

Requires the runtime deps (streamlit / streamlit-agraph) to be installed.
"""
import pytest

try:
    import app as appmod
except Exception as exc:  # runtime deps missing
    pytest.skip(f"runtime deps not importable: {exc}", allow_module_level=False)

import streamlit as st  # noqa
from clickhouse_introspection import DBObject, DbEdge, SchemaSnapshot

SNAPSHOT = SchemaSnapshot(
    database="shop",
    objects=(
        DBObject(database="shop", name="v_user_totals", engine="View", kind="view"),
        DBObject(database="shop", name="users", engine="MergeTree", kind="table"),
        DBObject(database="other_db", name="ext_table", engine="MergeTree", kind="table"),
    ),
    edges=(
        DbEdge(src="shop.v_user_totals", dst="shop.users", kind="view_source"),
        DbEdge(src="shop.v_user_totals", dst="other_db.ext_table", kind="view_source"),
    ),
)


def _init_filters() -> None:
    st.session_state["types"] = list(appmod.TYPE_LABELS)
    st.session_state["show_system"] = False
    st.session_state["show_external"] = True


def test_build_graph_sizes_and_pseudo_nodes():
    _init_filters()
    nodes, edges = appmod._build_graph(SNAPSHOT)

    sizes = {n.id: n.size for n in nodes}
    # pseudo-node for the cross-database reference is added
    assert set(sizes) == {"shop.v_user_totals", "shop.users", "other_db.ext_table"}
    # view has 2 outgoing edges, the targets 1 each
    assert sizes["shop.v_user_totals"] == 16 + 2 * 2
    assert sizes["shop.users"] == 16 + 2

    pairs = {(e.source, e.to) for e in edges}  # agraph Edge: .source/.to, no .target
    assert pairs == {
        ("shop.v_user_totals", "shop.users"),
        ("shop.v_user_totals", "other_db.ext_table"),
    }


HIDDEN_SNAPSHOT = SchemaSnapshot(
    database="shop",
    objects=(
        DBObject(database="shop", name="v_report", engine="View", kind="view"),
        DBObject(database="shop", name="users", engine="MergeTree", kind="table"),
        DBObject(database="shop", name="x_events", engine="MergeTree", kind="table"),
        DBObject(database="shop", name="x_events_local", engine="MergeTree", kind="table"),
    ),
    edges=(
        DbEdge(src="shop.v_report", dst="shop.users", kind="view_source"),
        DbEdge(src="shop.v_report", dst="shop.x_events", kind="view_source"),
        DbEdge(src="shop.mv_pipeline", dst="shop.x_events_local", kind="mv_target"),
    ),
)


def test_hidden_x_tables_not_rendered():
    _init_filters()
    nodes, edges = appmod._build_graph(HIDDEN_SNAPSHOT)

    node_ids = {n.id for n in nodes}
    assert node_ids == {"shop.v_report", "shop.users"}  # x_* are gone

    pairs = {(e.source, e.to) for e in edges}
    # the edge into the hidden x_events is dropped, normal one stays
    assert pairs == {("shop.v_report", "shop.users")}


def test_hidden_x_prefix_matches_only_name_part():
    assert appmod._is_hidden("shop.x_events")
    assert appmod._is_hidden("shop.x_events_local")
    # database names starting with x_ must not trigger the filter
    assert not appmod._is_hidden("x_db.users")
    assert not appmod._is_hidden("shop.users")


def test_build_graph_respects_type_filter():
    _init_filters()
    st.session_state["types"] = ["view"]
    nodes, edges = appmod._build_graph(SNAPSHOT)
    st.session_state["types"] = list(appmod.TYPE_LABELS)

    node_ids = {n.id for n in nodes}
    # referenced endpoints stay visible as pseudo-nodes even with the filter on
    assert {"shop.v_user_totals", "shop.users", "other_db.ext_table"} == node_ids
