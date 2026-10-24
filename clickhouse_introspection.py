"""ClickHouse schema introspection and dependency-graph building.

All metadata is read from the system tables (`system.tables`, `system.columns`,
`system.databases`).  ClickHouse keeps no first-class "view references table"
mapping, so view / materialized-view / dictionary dependencies are derived by
regex-parsing each object's CREATE query.

The `clickhouse_connect` driver is imported lazily inside `SchemaIntrospector`,
so the pure-python parsing helpers stay importable unit-testable without the
driver installed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Engines that represent views (MaterializedView handled separately).
VIEW_ENGINES = frozenset({"View", "MaterializedView", "LiveView", "WindowView"})

# Tokens that can legally follow FROM/JOIN/TO in DDL but are not table names.
SQL_KEYWORDS = frozenset(
    {
        "select", "with", "where", "group", "order", "by", "limit", "offset",
        "settings", "format", "as", "on", "using", "values", "temporary",
        "table", "dictionary", "disk", "volume", "file", "url", "watermark",
        "step", "timezone", "first", "last", "interval", "any", "all",
    }
)

_FROM_JOIN = re.compile(
    r"""(?:\bFROM\b|\bJOIN\b)\s+
        (?: [`"]? (?P<db>\w+) [`"]? \s*\.\s* )?
        [`"]? (?P<table>(?>\w+)) [`"]?
        (?! \s* \( )""",
    re.VERBOSE | re.IGNORECASE,
)

# Materialized-view target: CREATE MATERIALIZED VIEW ... TO db.target AS SELECT ...
_MV_TARGET = re.compile(
    r"""\bTO\s+
        (?: [`"]? (?P<db>\w+) [`"]? \s*\.\s* )?
        [`"]? (?P<table>(?>\w+)) [`"]?
        (?! \s* \( )""",
    re.VERBOSE | re.IGNORECASE,
)

# Dictionary source, named-argument form:
# SOURCE(CLICKHOUSE(host 'localhost' port 9000 db 'shop' table 'users'))
_DICT_SOURCE = re.compile(
    r"""\bdb\s+[`'"]?(?P<db>\w+)[`'"]?\s+table\s+[`'"]?(?P<table>\w+)[`'"]?""",
    re.VERBOSE | re.IGNORECASE,
)

# CTE/column aliases written as `name AS (` — table candidates equal to these
# aliases are CTE references, not real tables.
_ALIASES = re.compile(r"""[`"]?(\w+)[`"]?\s+AS\s*\(""", re.IGNORECASE | re.VERBOSE)

_COMMENT_PATTERNS = (re.compile(r"--[^\n]*"), re.compile(r"/\*.*?\*/", re.DOTALL))


@dataclass(frozen=True)
class DBObject:
    """One object (table / view / dictionary) of a database."""

    database: str
    name: str
    engine: str
    kind: str  # table | view | mv | dictionary
    rows: int | None = None
    create_query: str = ""

    @property
    def id(self) -> str:
        return f"{self.database}.{self.name}"


@dataclass(frozen=True)
class DbEdge:
    """Directed dependency: source object uses target object."""

    src: str  # fully-qualified "database.name"
    dst: str
    kind: str  # view_source | mv_target | dict_source


@dataclass(frozen=True)
class SchemaSnapshot:
    """Graph of one database at one moment in time."""

    database: str
    objects: tuple[DBObject, ...]
    edges: tuple[DbEdge, ...]


def _strip_comments(sql: str) -> str:
    for pattern in _COMMENT_PATTERNS:
        sql = pattern.sub(" ", sql)
    return sql


def _kind(engine: str) -> str:
    if engine == "MaterializedView":
        return "mv"
    if engine in VIEW_ENGINES:
        return "view"
    if engine == "Dictionary":
        return "dictionary"
    return "table"


def parse_dependencies(
    *,
    create_query: str,
    own_database: str,
    own_name: str,
    kind: str,
) -> tuple[DbEdge, ...]:
    """Extract dependency edges of one object from its CREATE query.

    - view / mv: sources after FROM and JOIN (``view_source`` edges)
    - mv: the TO target (``mv_target`` edge)
    - dictionary: SOURCE(...) reference (``dict_source`` edge)

    Skips: comments, subquery sources ``FROM (SELECT ...``, function-like
    sources ``FROM numbers(10)``, SQL keywords (e.g. ``TO DISK`` in TTL), CTE
    aliases declared via ``WITH name AS (...)`` and self-edges.  Sources inside
    subqueries are kept — they are real dependencies.  Unqualified references
    resolve to ``own_database``.
    """
    text = _strip_comments(create_query or "")
    aliases = {m.group(1).lower() for m in _ALIASES.finditer(text)}
    own_id = f"{own_database}.{own_name}"
    seen: set[tuple[str, str, str]] = set()
    out: list[DbEdge] = []

    def add(db_match: str | None, table_match: str | None, edge_kind: str) -> None:
        table_ref = (table_match or "").strip("`\"")
        db_ref = (db_match or "").strip("`\"")
        if not table_ref:
            return
        if table_ref.lower() in SQL_KEYWORDS or table_ref.lower() in aliases:
            return
        if not db_ref:
            db_ref = own_database
        dst = f"{db_ref}.{table_ref}"
        if dst == own_id or (own_id, dst, edge_kind) in seen:
            return
        seen.add((own_id, dst, edge_kind))
        out.append(DbEdge(src=own_id, dst=dst, kind=edge_kind))

    if kind in {"view", "mv"}:
        for m in _FROM_JOIN.finditer(text):
            add(m.group("db"), m.group("table"), "view_source")
    if kind == "mv":
        for m in _MV_TARGET.finditer(text):
            add(m.group("db"), m.group("table"), "mv_target")
    if kind == "dictionary":
        for m in _DICT_SOURCE.finditer(text):
            add(m.group("db"), m.group("table"), "dict_source")

    return tuple(out)


def _escape_literal(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace("'", "\\'")


class SchemaIntrospector:
    """Thin wrapper around one clickhouse-connect client."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8123,
        username: str = "default",
        password: str = "",
        database: str = "default",
        secure: bool = False,
        connect_timeout: int = 10,
        query_timeout: int = 60,
    ):
        import clickhouse_connect  # lazy: keeps parser importable without the driver

        # Timeouts were renamed across driver versions (query_timeout ->
        # send_receive_timeout); pass only what the installed client accepts.
        timeout_kwargs: dict[str, int] = {}
        try:
            from inspect import signature

            from clickhouse_connect.driver.httpclient import HttpClient

            accepted = signature(HttpClient).parameters
            for key, value in (
                ("connect_timeout", connect_timeout),
                ("send_receive_timeout", query_timeout),
            ):
                if key in accepted:
                    timeout_kwargs[key] = value
        except Exception:  # unexpected package layout — connect with defaults
            pass

        self.client = clickhouse_connect.get_client(
            host=host,
            port=int(port),
            username=username,
            password=password or "",
            database=database or "default",
            secure=bool(secure),
            **timeout_kwargs,
        )
        self.database = self.client.database or "default"

    def databases(self) -> tuple[str, ...]:
        rows = self.client.query(
            "SELECT name FROM system.databases "
            "WHERE name NOT IN ('INFORMATION_SCHEMA', 'information_schema') "
            "ORDER BY name"
        ).result_rows
        return tuple(r[0] for r in rows)

    def objects(self, database: str | None = None) -> tuple[DBObject, ...]:
        db = database or self.database
        sql = (
            "SELECT name, engine, total_rows, create_table_query "
            f"FROM system.tables WHERE database = '{_escape_literal(db)}' "
            "ORDER BY name"
        )
        out: list[DBObject] = []
        for name, engine, rows, ddl in self.client.query(sql).result_rows:
            out.append(
                DBObject(
                    database=db,
                    name=name,
                    engine=engine or "",
                    kind=_kind(engine or ""),
                    rows=None if rows is None else int(rows),
                    create_query=ddl or "",
                )
            )
        return tuple(out)

    def build_snapshot(self, database: str | None = None) -> SchemaSnapshot:
        """Introspect a database and derive the dependency edges between objects."""
        db = database or self.database
        objs = self.objects(db)
        edges: list[DbEdge] = []
        for obj in objs:
            if obj.kind == "table":
                continue
            edges.extend(
                parse_dependencies(
                    create_query=obj.create_query,
                    own_database=obj.database,
                    own_name=obj.name,
                    kind=obj.kind,
                )
            )
        return SchemaSnapshot(database=db, objects=objs, edges=tuple(edges))

    def columns(self, database: str | None = None, table: str | None = None):
        db = database or self.database
        sql = (
            "SELECT name, type FROM system.columns "
            f"WHERE database = '{_escape_literal(db)}' AND table = '{_escape_literal(table or '')}' "
            "ORDER BY position"
        )
        return tuple((r[0], r[1]) for r in self.client.query(sql).result_rows)
