# AGENTS.md

## Commands
- Install: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- Run app: `.venv/bin/streamlit run app.py` → http://localhost:8501. No CLI args. Connection is configured in the UI: a `st.form` on the login screen and a sidebar expander ("⚙️ Параметры подключения") after connecting. Env vars `CLICKHOUSE_HOST/PORT/USER/PASSWORD/DATABASE/SECURE` only provide form defaults (`st.session_state["ch_*"]`).
- Tests: `.venv/bin/pytest tests -q` — pure Python, no ClickHouse server required (they only exercise the regex parser).
  Single test: `.venv/bin/pytest tests/test_dependencies.py -k mv_target`
- No lint/format/typecheck configured. Quick sanity check: `python3 -m py_compile app.py clickhouse_introspection.py`

## Layout
- `app.py` — the entire Streamlit UI (Russian labels): sidebar connection + filters, agraph render, click-details panel.
- `clickhouse_introspection.py` — all ClickHouse work: `SchemaIntrospector` (system.tables/columns/databases), object classification, `parse_dependencies` regexes. The driver (`clickhouse_connect`) is imported lazily, so parser changes and tests need no ClickHouse.
- `demo/demo_schema.sql` — small `shop` schema (tables + view + MV + dictionary) for manual verification.
- `tests/` tests only `parse_dependencies`.

## Introspection model (the readable part)
- Object metadata comes from one query to `system.tables`; view/MV/dictionary edges are regex-extracted from `create_table_query`. ClickHouse has no server-side view→table map (`system.tables.dependencies_*` is reverse-only), so that regex parse is the only source of edges.
- Parser contract: strips comments; skips subquery sources `FROM (SELECT…`, function sources `FROM numbers(10)`, SQL-keyword tokens (`TO DISK` in TTL), CTE aliases (`WITH t AS (…)`), self-edges; unqualified refs resolve to the object's own database. Sources *inside* subqueries are kept (they are real deps).
- Edge kinds: `view_source`, `mv_target`, `dict_source` — renaming them breaks `EDGE_LABELS` in `app.py` and the tests. The table regex uses an atomic group `(?>\w+)` + lookahead to distinguish `FROM numbers(10)` from `FROM number`; don't replace it with plain `\w+`.

## Streamlit specifics
- Never call `st.sidebar.*` (e.g. `st.sidebar.columns`) **inside** a `st.form` context — the sidebar proxy drops the form context and `st.form_submit_button` then raises `StreamlitInvalidLayoutContextError`. Open the sidebar/expander context via `with st.sidebar:` in the caller and use only module-level `st.*` calls inside `_connection_form`.
- Connection lifecycle: form submit → `_apply_connection` validates creds (`connect(...).databases()`), then stores `st.session_state["connected" / "conn_params" / "db_list" / "last_conn_error"]`. A failed attempt flips `connected=False` and the next run falls back to the login screen with the error shown. Connection happens only on form submit — never reconnect on widget keystrokes.
- `connect` is `st.cache_resource`, snapshot/column tables are `st.cache_data`; both are keyed on the connection params plus `st.session_state["refresh"]`. The "🔄 Обновить схему" button increments `refresh` to force re-introspection — do not add ad-hoc cache clearing.
- A graph click makes `agraph` return the selected node — a plain id string or a Node object depending on the streamlit-agraph version; normalize through `_node_id_from_selection` before storing in `st.session_state["selected_node"]`. The details panel renders from it on the same/next run.
- The agraph `Edge` object exposes `.source`/.`from`/`.to` but **no** `.target` attribute here (constructor swallows it) — compute anything graph-derived (node sizes/degrees) from our `chi.DbEdge` (`.src`/`.dst`) only; regression tests live in `tests/test_build_graph.py`.
- Node ids are fully-qualified `database.name`. Objects referenced from other databases (or hidden by the type filter) appear as extra pseudo-nodes (`kind="pseudo"`, gold/system-grey colors); they bypass the type multi-select by design so a view always shows the tables it uses.
- Objects whose *name* starts with `x_` (`HIDDEN_PREFIXES` in `app.py`) are technical shard-local tables feeding Distributed tables: they are excluded from rendering — not in `_visible_objects`, their edges/pseudo-nodes are dropped in `_build_graph`, and they are filtered out of the details' "uses/used-by" lists. The prefix check matches only the table-name part, so a database named `x_db` is unaffected. The caption counts refer to the filtered graph.
