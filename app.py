"""Interactive ClickHouse schema graph (Streamlit).

Run:  streamlit run app.py

Connection is configured in the UI: a form on the start screen, and a sidebar
expander ("⚙️ Параметры подключения") after the connection is established.
Env vars CLICKHOUSE_HOST / CLICKHOUSE_PORT / CLICKHOUSE_USER / CLICKHOUSE_PASSWORD /
CLICKHOUSE_DATABASE / CLICKHOUSE_SECURE only provide the form defaults.
"""
from __future__ import annotations

import os
from collections import defaultdict

import streamlit as st
import clickhouse_introspection as chi
from streamlit_agraph import Config, Edge, Node, agraph

st.set_page_config(page_title="ClickHouse — схема-граф", page_icon="🗄", layout="wide")

NODE_COLORS = {
    "table": "#4c9aff",
    "view": "#b37feb",
    "mv": "#ff9f1c",
    "dictionary": "#2ecc71",
}
KIND_LABELS_RU = {"table": "таблица", "view": "представление", "mv": "мат. представление", "dictionary": "словарь"}
TYPE_LABELS = {
    "table": "Таблицы",
    "view": "Представления",
    "mv": "Материализованные представления",
    "dictionary": "Словари",
}
EDGE_LABELS = {"view_source": "использует", "mv_target": "TO-цель", "dict_source": "источник"}

# Technical shard-local tables feeding Distributed tables are not shown:
# neither as nodes, nor as edge endpoints / pseudo-nodes.
HIDDEN_PREFIXES = ("x_",)


def _is_hidden(node_id: str) -> bool:
    name = node_id.rsplit(".", 1)[-1] if "." in node_id else node_id
    return name.startswith(HIDDEN_PREFIXES)


def _init_state() -> None:
    defaults = {
        "ch_host": os.environ.get("CLICKHOUSE_HOST", "localhost"),
        "ch_port": int(os.environ.get("CLICKHOUSE_PORT", "8123")),
        "ch_user": os.environ.get("CLICKHOUSE_USER", "default"),
        "ch_password": os.environ.get("CLICKHOUSE_PASSWORD", ""),
        "ch_database": os.environ.get("CLICKHOUSE_DATABASE", "default"),
        "ch_secure": os.environ.get("CLICKHOUSE_SECURE", "").lower() in {"1", "true", "yes"},
        "connected": False,
        "conn_params": None,
        "db_list": None,
        "last_conn_error": None,
        "refresh": 0,
        "selected_node": None,
        "types": list(TYPE_LABELS),
        "show_system": False,
        "show_external": True,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


@st.cache_resource(show_spinner="Подключение к ClickHouse…")
def connect(host: str, port: int, user: str, password: str, secure: bool) -> chi.SchemaIntrospector:
    return chi.SchemaIntrospector(
        host=host, port=port, username=user, password=password, secure=secure
    )


@st.cache_data(show_spinner="Чтение схемы…")
def schema_snapshot(
    host: str, port: int, user: str, password: str, secure: bool, refresh: int, database: str
) -> chi.SchemaSnapshot:
    return connect(host, port, user, password, secure).build_snapshot(database)


@st.cache_data(show_spinner=False)
def columns_for(
    host: str, port: int, user: str, password: str, secure: bool, refresh: int,
    obj_db: str, obj_table: str,
) -> list[list[str]]:
    intro = connect(host, port, user, password, secure)
    return [[n, t] for n, t in intro.columns(obj_db, obj_table)]


def _apply_connection(host: str, port: int, user: str, password: str, secure: bool) -> None:
    """Validate credentials on submit and populate the database list."""
    try:
        dbs = list(connect(host, port, user, password, secure).databases())
    except Exception as exc:
        st.session_state.update(
            connected=False, db_list=None, conn_params=None,
            last_conn_error=str(exc) or type(exc).__name__,
        )
        return
    st.session_state.update(
        last_conn_error=None, db_list=dbs, connected=True, selected_node=None,
        conn_params={
            "host": host, "port": int(port), "user": user, "password": password, "secure": secure,
        },
    )
    if st.session_state["ch_database"] not in dbs:
        st.session_state["ch_database"] = dbs[0]


def _connection_form(submit_label: str) -> None:
    """Connection form on the *current* container (caller opens sidebar/expander context).

    Inside the form always use module-level st.* calls: st.sidebar.columns would
    bypass the form context and break st.form_submit_button.
    """
    with st.form("connection_form", clear_on_submit=False):
        row1 = st.columns([2, 1])
        row1[0].text_input("Хост", key="ch_host")
        row1[1].number_input("Порт", min_value=1, max_value=65535, step=1, key="ch_port")
        row2 = st.columns(2)
        row2[0].text_input("Пользователь", key="ch_user")
        row2[1].text_input("Пароль", type="password", key="ch_password")
        st.checkbox("HTTPS / TLS", key="ch_secure")
        submitted = st.form_submit_button(submit_label, use_container_width=True)
    if submitted:
        _apply_connection(
            host=st.session_state["ch_host"],
            port=int(st.session_state["ch_port"]),
            user=st.session_state["ch_user"],
            password=st.session_state["ch_password"],
            secure=st.session_state["ch_secure"],
        )


def _render_login() -> None:
    st.subheader("Подключение к ClickHouse")
    _connection_form("🔌 Подключиться")
    if st.session_state.get("last_conn_error"):
        st.error(f"Не удалось подключиться: {st.session_state['last_conn_error']}")
    st.caption(
        "Значения по умолчанию задаются переменными окружения "
        "CLICKHOUSE_HOST / PORT / USER / PASSWORD / DATABASE / SECURE."
    )


def _render_connected_sidebar(conn: tuple[str, int, str, str, bool]) -> None:
    with st.sidebar:
        st.success(f"🟢 Подключено: {conn[0]}:{conn[1]} · {conn[2]}")
        with st.expander("⚙️ Параметры подключения", expanded=False):
            _connection_form("🔌 Переподключиться")
        if not st.session_state["connected"]:
            st.stop()  # reconnect failed; the next run renders the login screen

        database = st.selectbox("База данных", st.session_state["db_list"], key="ch_database")
        st.divider()
        st.multiselect("Типы объектов", list(TYPE_LABELS), key="types", format_func=TYPE_LABELS.get)
        st.checkbox("Показывать объекты базы system", key="show_system")
        st.checkbox("Показывать объекты из других баз", key="show_external")
        if st.button("🔄 Обновить схему", use_container_width=True):
            st.session_state["refresh"] += 1


def _visible_objects(snap: chi.SchemaSnapshot) -> list[chi.DBObject]:
    selected_types = set(st.session_state["types"])
    out = []
    for obj in snap.objects:
        if _is_hidden(obj.id):
            continue
        if obj.kind not in selected_types:
            continue
        if obj.database == "system" and not st.session_state["show_system"]:
            continue
        if obj.database not in (snap.database, "system") and not st.session_state["show_external"]:
            continue
        out.append(obj)
    return out


def _build_graph(snap: chi.SchemaSnapshot):  # -> (list[Node], list[Edge])
    visible = _visible_objects(snap)
    by_id = {obj.id: obj for obj in visible}

    # Referenced objects outside the visible set (e.g. hidden by the type filter,
    # or living in other databases) are added as pseudo-nodes so that view edges
    # always show the tables a view uses.  Edges touching technical `x_*`
    # shard-local tables are dropped entirely — they never appear on the graph.
    included = [
        e for e in snap.edges
        if (e.src in by_id or e.dst in by_id)
        and not _is_hidden(e.src) and not _is_hidden(e.dst)
    ]
    endpoint_ids = set(by_id)
    for edge in included:
        endpoint_ids.add(edge.src)
        endpoint_ids.add(edge.dst)
    for node_id in endpoint_ids - set(by_id):
        db, _, name = node_id.partition(".")
        by_id[node_id] = chi.DBObject(database=db, name=name, engine="", kind="pseudo")

    edges = [
        Edge(source=e.src, target=e.dst, label=EDGE_LABELS.get(e.kind, e.kind))
        for e in included
    ]
    # Degree is computed from our own DbEdge objects: the streamlit-agraph Edge
    # has .source/.to but no .target attr (version-dependent), so never read it.
    degree: dict[str, int] = defaultdict(int)
    for e in included:
        degree[e.src] += 1
        degree[e.dst] += 1

    nodes = []
    for node_id, obj in sorted(by_id.items()):
        if obj.database == "system":
            color = "#95a5a6"
        elif obj.kind == "pseudo":
            color = "#d9b25f"
        else:
            color = NODE_COLORS.get(obj.kind, "#d9b25f")
        label = obj.name if obj.database == snap.database else node_id
        nodes.append(
            Node(id=node_id, label=label, size=16 + min(30, degree.get(node_id, 0) * 2), color=color)
        )
    return nodes, edges


def _graph_config() -> Config:
    return Config(
        width=960,
        height=680,
        directed=True,
        physics=True,
        nodeHighlightBehavior=True,
        highlightColor="#F7A7A6",
        collapsible=False,
        node={"labelProperty": "label"},
        link={"labelProperty": "label", "renderLabel": True},
    )


def _node_id_from_selection(selected) -> str | None:
    """Normalize the agraph click result.

    Depending on the installed streamlit-agraph version the returned value is
    either a plain node-id string or a Node object with an .id attribute.
    """
    if selected is None:
        return None
    if isinstance(selected, str):
        return selected or None
    node_id = getattr(selected, "id", None)
    return node_id if isinstance(node_id, str) and node_id else None


def _render_details(
    conn: tuple[str, int, str, str, bool], snap: chi.SchemaSnapshot, node_id: str
) -> None:
    obj = next((o for o in snap.objects if o.id == node_id), None)
    obj_db, _, obj_name = node_id.partition(".")
    st.subheader(f"🔍 {node_id}")
    if obj is not None:
        kind_ru = KIND_LABELS_RU.get(obj.kind, obj.kind)
        rows = f"{obj.rows} строк" if obj.rows is not None else "строки неизвестны"
        st.caption(f"{kind_ru} · engine: {obj.engine or '—'} · {rows}")
    else:
        st.caption("Объект из другой базы данных (в снимок схемы не включён).")

    try:
        cols = columns_for(*conn, st.session_state["refresh"], obj_db, obj_name)
    except Exception as exc:
        cols = []
        st.warning(f"Не удалось получить колонки: {exc}")
    if cols:
        st.markdown("**Колонки**")
        st.dataframe(
            [{"name": n, "type": t} for n, t in cols], hide_index=True, use_container_width=True
        )

    uses = sorted((e.dst, e.kind) for e in snap.edges if e.src == node_id and not _is_hidden(e.dst))
    used_by = sorted(
        (e.src, e.kind) for e in snap.edges if e.dst == node_id and not _is_hidden(e.src)
    )
    st.markdown("**Использует**")
    st.markdown(
        "\n".join(f"- ↗ {dst} ({EDGE_LABELS.get(kind, kind)})" for dst, kind in uses) or "— (нет)"
    )
    st.markdown("**Используется в**")
    st.markdown("\n".join(f"- ↙ {src}" for src, kind in used_by) or "— (нет)")

    if obj is not None and obj.create_query:
        with st.expander("CREATE-запрос", expanded=False):
            st.code(obj.create_query, language="sql")


def main() -> None:
    _init_state()
    st.title("🗄 ClickHouse — схема базы данных")

    if not st.session_state["connected"]:
        _render_login()
        return

    params = st.session_state["conn_params"]
    conn = (params["host"], params["port"], params["user"], params["password"], params["secure"])
    _render_connected_sidebar(conn)

    database = st.session_state["ch_database"]
    try:
        snap = schema_snapshot(*conn, st.session_state["refresh"], database)
    except Exception as exc:
        st.error(f"Не удалось прочитать схему базы `{database}`: {exc}")
        st.info("Попробуйте «🔄 Обновить схему» или переподключиться в боковой панели.")
        st.stop()

    nodes, edges = _build_graph(snap)
    st.caption(
        f"База `{snap.database}` · объектов на графе: {len(nodes)} · ссылок: {len(edges)} "
        "(технические таблицы `x_*` скрыты)"
    )
    left, right = st.columns([0.68, 0.32], gap="medium")
    with left:
        if not nodes:
            st.info("Пусто: нет объектов для отображения (проверьте фильтры на боковой панели).")
        else:
            selected = agraph(nodes=nodes, edges=edges, config=_graph_config())
            node_clicked = _node_id_from_selection(selected)
            if node_clicked:
                st.session_state["selected_node"] = node_clicked
    with right:
        node_id = st.session_state.get("selected_node")
        if node_id:
            _render_details(conn, snap, node_id)
        else:
            st.info("Кликните по узлу графа, чтобы увидеть колонки, CREATE-запрос и связи.")


if __name__ == "__main__":
    main()
