"""Unit tests for the dependency regex parser (no ClickHouse server needed)."""
from clickhouse_introspection import parse_dependencies


def deps(create_query, *, db="shop", name="v", kind="view"):
    return parse_dependencies(
        create_query=create_query, own_database=db, own_name=name, kind=kind
    )


def as_set(edges):
    return {(e.dst, e.kind) for e in edges}


def test_qualified_and_unqualified_sources():
    edges = deps("CREATE VIEW v AS SELECT * FROM src JOIN db2.other ON c WHERE x = 1")
    assert ("shop.src", "view_source") in as_set(edges)
    assert ("db2.other", "view_source") in as_set(edges)


def test_subquery_and_function_sources_skipped():
    sql = "SELECT x FROM (SELECT 1) AS t UNION ALL SELECT * FROM numbers(10)"
    assert as_set(deps(sql)) == set()


def test_backticked_names():
    edges = deps("SELECT * FROM `shop`.`tbl` JOIN `tab` ON 1")
    assert ("shop.tbl", "view_source") in as_set(edges)
    assert ("shop.tab", "view_source") in as_set(edges)


def test_mv_target_and_source():
    sql = (
        "CREATE MATERIALIZED VIEW shop.mv TO shop.daily AS "
        "SELECT toStartOfDay(ts) d, sum(a) FROM shop.orders GROUP BY d"
    )
    pairs = as_set(deps(sql, kind="mv"))
    assert ("shop.daily", "mv_target") in pairs
    assert ("shop.orders", "view_source") in pairs


def test_ttl_to_disk_not_treated_as_table():
    sql = "TTL ts + INTERVAL 1 MONTH TO DISK 'slow' SETTINGS index_granularity = 8192"
    assert as_set(deps(sql, kind="mv")) == set()


def test_cte_alias_excluded():
    sql = "WITH t AS (SELECT 1 FROM shop.real_table) SELECT * FROM t"
    assert as_set(deps(sql)) == {("shop.real_table", "view_source")}


def test_comments_are_stripped():
    sql = "-- FROM fake_table\nSELECT * /* TO other */ FROM shop.real"
    assert as_set(deps(sql)) == {("shop.real", "view_source")}


def test_dictionary_named_source():
    ddl = (
        "CREATE DICTIONARY shop.d (k UInt64, v String) PRIMARY KEY k "
        "SOURCE(CLICKHOUSE(host 'localhost' port 9000 db 'shop' table 'users')) "
        "LIFETIME(MIN 300 MAX 360) LAYOUT(FLAT())"
    )
    assert as_set(deps(ddl, kind="dictionary")) == {("shop.users", "dict_source")}


def test_self_reference_skipped():
    edges = deps("SELECT * FROM v", db="shop", name="v")
    assert as_set(edges) == set()


def test_no_duplicate_edges():
    sql = "SELECT * FROM shop.repeated UNION ALL SELECT * FROM shop.repeated"
    edges = deps(sql)
    assert len(edges) == 1
