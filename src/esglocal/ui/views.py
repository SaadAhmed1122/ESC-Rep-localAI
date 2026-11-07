"""Streamlit pages."""

from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from esglocal.agents.advisor import Advisor
from esglocal.agents.orchestrator import STAGES, ESGPipeline
from esglocal.calculator import detect_period_column
from esglocal.catalog import CATEGORIES
from esglocal.forecasting import forecast, simulate
from esglocal.llm.base import LLMError
from esglocal.llm.factory import RECOMMENDED_MODELS
from esglocal.llm.ollama import is_embedding_model
from esglocal.mapping import suggest_mapping
from esglocal.privacy import network_guard
from esglocal.privacy.redact import redact
from esglocal.privacy.safe_markdown import safe_markdown
from esglocal.rag.ingest import IngestError, read_document
from esglocal.reporting import to_html
from esglocal.samples import SAMPLE_COMPANY, SAMPLE_INDUSTRY, sample_dataframe, sample_narratives
from esglocal.scoring import rating_for
from esglocal.ui import state

CAT_COLORS = {"Environmental": "#2f9e44", "Social": "#1c7ed6", "Governance": "#7048e8"}


def _need_industry() -> bool:
    if not state.workspace().industry:
        st.info("Choose an industry and add data on **📥 Data & KPIs** (sidebar) first.")
        return True
    return False


def _fmt(v, nd=2):
    if v is None:
        return "—"
    return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:,.{nd}f}"


def load_sample() -> None:
    ws = state.workspace()
    df = sample_dataframe()
    ws.company, ws.industry = SAMPLE_COMPANY, SAMPLE_INDUSTRY
    ws.set_data(df)
    ws.period_col = detect_period_column(df)
    ws.mapping = suggest_mapping(df.columns, state.catalog().required_inputs(ws.industry))
    ws.narratives = sample_narratives()
    state.save()
    state.audit().append("data.sample_loaded", rows=len(df))


# ------------------------------------------------------------------------ HOME
def home() -> None:
    st.title("🌿 ESG Local AI")
    st.markdown(
        "Private ESG reporting and AI advisory that runs **entirely on this computer**. "
        "Your data never leaves the machine: models run in your local runtime (Ollama or any "
        "OpenAI-compatible local server), storage is encrypted, and outbound network access "
        "from the app is blocked."
    )
    c1, c2, c3 = st.columns(3)
    ok, msg = state.llm().health()
    c1.metric("Model runtime", "online" if ok else "offline", help=msg)
    c2.metric("Network guard", "on" if network_guard.is_installed() else "off")
    c3.metric("Storage", "encrypted")
    if not ok:
        st.warning(
            "No local model runtime found. Install [Ollama](https://ollama.com), run "
            "`ollama serve`, then `ollama pull qwen2.5:7b`. Or set `ESGLOCAL_PROVIDER=openai_compat` "
            "for LM Studio / llama.cpp."
        )
    st.subheader("Get started")
    st.markdown(
        "1. **Data & KPIs** — pick your industry, upload a CSV/Excel file (columns are auto-mapped) "
        "or type values, and describe your policies.\n"
        "2. **Dashboard** — scores vs benchmarks, trends and forecasts.\n"
        "3. **What-if** — see how operational changes move your score.\n"
        "4. **AI agents report** — E/S/G analyst agents, a strategist, a critic and a writer "
        "produce a board-ready report.\n"
        "5. **Advisor chat** — ask questions; the advisor uses tools on your data and documents."
    )
    if st.button("Load demo company", type="primary", icon="🏭"):
        load_sample()
        st.success(f"Loaded {SAMPLE_COMPANY}. Open the Dashboard.")


# ------------------------------------------------------------------------ DATA
def data() -> None:
    st.title("📥 Data & KPIs")
    cat = state.catalog()
    ws = state.workspace()

    c1, c2 = st.columns([1, 1])
    company = c1.text_input("Company name", ws.company)
    if company != ws.company:
        ws.company = company
        state.save()
    names = [i.name for i in cat.industries]
    idx = names.index(ws.industry) if ws.industry in names else None
    industry = c2.selectbox(
        "Industry",
        names,
        index=idx,
        placeholder="Search industry…",
        format_func=lambda n: f"{n} · {cat.industry(n).sector}",
    )
    if industry != ws.industry:
        ws.industry = industry
        if ws.records:
            ws.mapping = suggest_mapping(ws.dataframe().columns, cat.required_inputs(industry))
            st.session_state._map_ver = st.session_state.get("_map_ver", 0) + 1
        state.save()
    if not ws.industry:
        st.button("…or load the demo company", on_click=load_sample)
        return

    ind = cat.industry(ws.industry)
    w = ind.weights
    st.caption(
        f"Materiality weights — Environmental {w['Environmental']:.0%} · Social {w['Social']:.0%} "
        f"· Governance {w['Governance']:.0%}. {len(cat.kpis_for(ws.industry))} KPIs apply."
    )

    tab_up, tab_map, tab_manual, tab_text, tab_bench = st.tabs(
        ["Upload", "Column mapping", "Manual entry", "Narratives", "Benchmarks"]
    )
    inputs = cat.required_inputs(ws.industry)

    with tab_up:
        st.markdown(
            "Upload one row per period (e.g. a `Year` column), or several rows per period "
            "(sites/business units) — they are aggregated correctly. Processed locally."
        )
        up = st.file_uploader("CSV or Excel", type=["csv", "xlsx", "xls"])
        digest = hashlib.sha256(up.getvalue()).hexdigest() if up is not None else None
        if up is not None and st.session_state.get("_last_upload") != digest:
            try:
                df = pd.read_csv(up) if up.name.lower().endswith(".csv") else pd.read_excel(up)
            except Exception as exc:  # noqa: BLE001
                st.error(f"Could not read file: {exc}")
            else:
                ws.set_data(df)
                ws.period_col = detect_period_column(df)
                ws.mapping = suggest_mapping(df.columns, inputs)
                st.session_state._last_upload = digest
                st.session_state._map_ver = st.session_state.get("_map_ver", 0) + 1
                state.save()
                state.audit().append("data.uploaded", rows=len(df), columns=len(df.columns))
                st.success(
                    f"Loaded {len(df)} rows; auto-mapped {len(ws.mapping)}/{len(inputs)} inputs."
                )
        if ws.records:
            st.dataframe(ws.dataframe(), use_container_width=True, height=220)
            tmpl = pd.DataFrame(columns=["Year", *inputs.keys()])
            st.download_button(
                "Download blank template", tmpl.to_csv(index=False), "esg_template.csv", "text/csv"
            )

    with tab_map:
        if not ws.records:
            st.info("Upload data first.")
        else:
            cols = ["—", *map(str, ws.dataframe().columns)]
            pc = st.selectbox(
                "Period column",
                cols,
                index=cols.index(ws.period_col) if ws.period_col in cols else 0,
            )
            new_map: dict[str, str] = {}
            st.caption(f"{len(ws.mapping)}/{len(inputs)} inputs mapped. Adjust if needed.")
            grid = st.columns(2)
            for i, (name, inp) in enumerate(sorted(inputs.items())):
                cur = ws.mapping.get(name, "—")
                choice = grid[i % 2].selectbox(
                    f"{name} ({inp.unit})",
                    cols,
                    index=cols.index(cur) if cur in cols else 0,
                    help=inp.description,
                    key=f"map_{st.session_state.get('_map_ver', 0)}_{name}",
                )
                if choice != "—":
                    new_map[name] = choice
            if st.button("Save mapping", type="primary"):
                ws.mapping = new_map
                ws.period_col = None if pc == "—" else pc
                state.save()
                st.success("Mapping saved.")

    with tab_manual:
        st.caption("Values entered here override the latest period of uploaded data.")
        with st.form("manual"):
            vals = {}
            grid = st.columns(3)
            for i, (name, inp) in enumerate(sorted(inputs.items())):
                cur = ws.manual_values.get(name)
                vals[name] = grid[i % 3].number_input(
                    f"{name} ({inp.unit})", value=cur, help=inp.description, format="%.4f"
                )
            if st.form_submit_button("Save values", type="primary"):
                ws.manual_values = {k: v for k, v in vals.items() if v is not None}
                state.save()
                st.success("Saved.")

    with tab_text:
        st.caption("Qualitative KPIs are scored locally by similarity to best-practice exemplars.")
        with st.form("narratives"):
            texts = {}
            for k in [k for k in cat.kpis_for(ws.industry) if k.is_qualitative]:
                texts[k.id] = st.text_area(
                    f"{k.name} ({k.category}) — {', '.join(k.frameworks)}",
                    ws.narratives.get(k.id, ""),
                    help=k.description,
                    height=140,
                )
            if st.form_submit_button("Save narratives", type="primary"):
                ws.narratives = {k: v for k, v in texts.items() if v.strip()}
                state.save()
                st.success("Saved.")

    with tab_bench:
        st.caption(
            "Defaults are illustrative. Override with your own peer or target values "
            "(best = 100 points, worst = 0)."
        )
        rows = []
        for k in cat.kpis_for(ws.industry):
            b = cat.benchmark_for(k.id, ws.industry)
            if not b:
                continue
            o = ws.benchmark_overrides.get(k.id, {})
            rows.append(
                {
                    "kpi_id": k.id,
                    "kpi": k.name,
                    "unit": k.unit,
                    "best": o.get("best", b.best),
                    "worst": o.get("worst", b.worst),
                }
            )
        edited = st.data_editor(
            pd.DataFrame(rows),
            disabled=["kpi_id", "kpi", "unit"],
            hide_index=True,
            use_container_width=True,
        )
        if st.button("Save benchmarks"):
            ov = {}
            for r in edited.to_dict(orient="records"):
                b = cat.benchmark_for(r["kpi_id"], ws.industry)
                if pd.isna(r["best"]) or pd.isna(r["worst"]) or r["best"] == r["worst"]:
                    continue
                if (r["best"], r["worst"]) != (b.best, b.worst):
                    ov[r["kpi_id"]] = {"best": float(r["best"]), "worst": float(r["worst"])}
            ws.benchmark_overrides = ov
            state.save()
            st.success(f"{len(ov)} override(s) saved.")


# ------------------------------------------------------------------- DASHBOARD
def _gauge(value, title):
    return go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=value or 0,
            title={"text": title},
            gauge={
                "axis": {"range": [0, 100]},
                "bar": {"color": "#0b7a53"},
                "steps": [
                    {"range": [0, 40], "color": "rgba(224,49,49,.15)"},
                    {"range": [40, 60], "color": "rgba(245,159,0,.15)"},
                    {"range": [60, 100], "color": "rgba(47,158,68,.15)"},
                ],
            },
        )
    ).update_layout(height=230, margin=dict(l=20, r=20, t=50, b=10))


def dashboard() -> None:
    st.title("📊 ESG dashboard")
    if _need_industry():
        return
    a = state.assessment()
    cat = state.catalog()
    ws = state.workspace()
    if not any(r.ok for r in a.results):
        st.warning("No KPIs could be computed yet — add data on the Data page.")
        return

    c = st.columns(4)
    c[0].plotly_chart(_gauge(a.overall, f"Overall · {a.rating}"), use_container_width=True)
    for i, catg in enumerate(CATEGORIES, 1):
        c[i].plotly_chart(
            _gauge(a.category_scores.get(catg), f"{catg} ({a.weights.get(catg, 0):.0%})"),
            use_container_width=True,
        )
    st.caption(f"KPI coverage {a.coverage:.0%} · industry {ws.industry}")

    tabs = st.tabs([*CATEGORIES, "Trends & forecast", "All KPIs"])
    for tab, catg in zip(tabs[:3], CATEGORIES, strict=True):
        with tab:
            rs = [r for r in a.results if r.category == catg and r.score is not None]
            if not rs:
                st.info("No scored KPIs in this category yet.")
                continue
            left, right = st.columns([3, 2])
            fig = go.Figure(
                go.Bar(
                    x=[r.score for r in rs],
                    y=[r.name for r in rs],
                    orientation="h",
                    marker_color=CAT_COLORS[catg],
                    text=[f"{r.score:.0f}" for r in rs],
                    textposition="outside",
                )
            ).update_layout(
                xaxis=dict(range=[0, 110], title="score (0-100)"),
                height=60 + 38 * len(rs),
                margin=dict(l=10, r=10, t=10, b=30),
            )
            left.plotly_chart(fig, use_container_width=True)
            if len(rs) >= 3:
                radar = go.Figure(
                    go.Scatterpolar(
                        r=[r.score for r in rs] + [rs[0].score],
                        theta=[r.kpi_id for r in rs] + [rs[0].kpi_id],
                        fill="toself",
                        line_color=CAT_COLORS[catg],
                    )
                ).update_layout(
                    polar=dict(radialaxis=dict(range=[0, 100])),
                    height=340,
                    margin=dict(l=30, r=30, t=20, b=20),
                    showlegend=False,
                )
                right.plotly_chart(radar, use_container_width=True)
            for r in rs:
                if r.qualitative:
                    with st.expander(f"📝 {r.name} — {r.score:.0f}/100"):
                        st.markdown(safe_markdown(r.text))

    with tabs[3]:
        trend = [r for r in a.results if len(r.history) >= 2]
        if not trend:
            st.info("Upload two or more periods to see trends and forecasts.")
        else:
            pick = st.selectbox(
                "KPI", trend, format_func=lambda r: f"{r.kpi_id} · {r.name} ({r.unit})"
            )
            fc = forecast(pick.history, horizon=3)
            fig = go.Figure()
            fig.add_trace(
                go.Scatter(
                    x=list(pick.history),
                    y=list(pick.history.values()),
                    mode="lines+markers",
                    name="actual",
                )
            )
            if fc:
                fig.add_trace(
                    go.Scatter(
                        x=[list(pick.history)[-1], *fc.periods],
                        y=[list(pick.history.values())[-1], *fc.values],
                        mode="lines+markers",
                        line=dict(dash="dash"),
                        name="linear forecast",
                    )
                )
            b = ws.overrides().get(pick.kpi_id) or cat.benchmark_for(pick.kpi_id, ws.industry)
            if b:
                fig.add_hline(
                    y=b.best,
                    line_dash="dot",
                    line_color="#2f9e44",
                    annotation_text="best-practice benchmark",
                )
            fig.update_layout(
                height=380, margin=dict(l=10, r=10, t=30, b=10), xaxis_type="category"
            )
            st.plotly_chart(fig, use_container_width=True)
            if fc:
                st.caption(
                    f"Trend {fc.direction}, R² = {fc.r2:.2f}. Linear projection — indicative only."
                )

    with tabs[4]:
        df = pd.DataFrame(
            [
                {
                    "ID": r.kpi_id,
                    "KPI": r.name,
                    "Category": r.category,
                    "Value": None if r.value is None else round(r.value, 3),
                    "Unit": r.unit,
                    "Score": None if r.score is None else round(r.score, 1),
                    "Rating": rating_for(r.score) if r.score is not None else "",
                    "Period": r.period,
                    "Status": "ok" if r.ok else r.error,
                    "Frameworks": ", ".join(cat.kpi(r.kpi_id).frameworks),
                }
                for r in a.results
            ]
        )
        st.dataframe(
            df,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f")
            },
        )
        st.download_button(
            "Export KPI table (CSV)", df.to_csv(index=False), "esg_kpis.csv", "text/csv"
        )


# --------------------------------------------------------------------- WHAT-IF
def what_if() -> None:
    st.title("🧪 What-if simulator")
    if _need_industry():
        return
    ws = state.workspace()
    cat = state.catalog()
    table = ws.table(cat)
    if table.empty:
        st.info("Add quantitative data first.")
        return
    base = state.assessment()
    st.caption(
        f"Adjust the latest period ({table.index[-1]}) inputs and see the score impact instantly."
    )
    inputs = cat.required_inputs(ws.industry)
    available = [c for c in table.columns if c in inputs]
    levers = st.multiselect(
        "Levers",
        available,
        default=[
            x
            for x in ("scope1_tco2e", "renewable_energy_mwh", "women_in_management")
            if x in available
        ],
        format_func=lambda n: f"{n} — {inputs[n].description}",
    )
    changes = {}
    grid = st.columns(3)
    for i, name in enumerate(levers):
        changes[name] = grid[i % 3].slider(f"{name} (%)", -100, 200, 0, 5)
    sim = simulate(cat, ws.industry, table, changes, ws.narratives, state.scorer(), ws.overrides())
    c = st.columns(4)
    c[0].metric("Overall", _fmt(sim.overall, 1), _fmt((sim.overall or 0) - (base.overall or 0), 1))
    for i, catg in enumerate(CATEGORIES, 1):
        after, before = sim.category_scores.get(catg), base.category_scores.get(catg)
        delta = None if after is None or before is None else f"{after - before:+.1f}"
        c[i].metric(catg, _fmt(after, 1), delta)
    rows = []
    for r in sim.results:
        b = base.result(r.kpi_id)
        if b and b.score is not None and r.score is not None and abs(r.score - b.score) > 0.05:
            rows.append(
                {
                    "KPI": f"{r.kpi_id} · {r.name}",
                    "Value before": b.value,
                    "Value after": r.value,
                    "Score before": round(b.score, 1),
                    "Score after": round(r.score, 1),
                }
            )
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


# ---------------------------------------------------------------------- AGENTS
def agents() -> None:
    st.title("🤖 Multi-agent ESG report")
    if _need_industry():
        return
    ws = state.workspace()
    llm = state.llm()
    ok, msg = llm.health()
    st.markdown(
        "**Pipeline:** data-quality checks → Environmental, Social & Governance analyst agents → "
        "strategist (runs what-if simulations) → critic (reviews & triggers a revision) → "
        "report writer. All numbers are computed in code; agents interpret them."
    )
    if not ok:
        st.error(msg)
        return
    s = state.settings()
    c1, c2 = st.columns(2)
    revise = c1.toggle("Allow critic-driven revision", value=True)
    c2.caption(
        f"Model `{llm.model}` · concurrency {s.agent_concurrency} · max {s.agent_max_steps} tool steps"
    )

    if st.button("Run agents", type="primary", icon="▶️"):
        ctx = state.agent_context()
        progress = st.progress(0.0)
        with st.status("Agents working…", expanded=True) as status:
            done: set[str] = set()

            def on_event(ev):
                if ev["stage"] == "tool":
                    st.write(f"🔧 {ev['message']}")
                elif ev["status"] == "start":
                    st.write(f"▶️ {ev['message']}")
                elif ev["status"] == "done":
                    done.add(ev["stage"])
                    progress.progress(len(done) / len(STAGES))
                    st.write(f"✅ {ev['message']}")
                elif ev["status"] == "error":
                    st.write(f"⚠️ {ev['message']}")

            try:
                result = ESGPipeline(
                    llm,
                    ctx,
                    on_event=on_event,
                    concurrency=s.agent_concurrency,
                    max_steps=s.agent_max_steps,
                    redact_pii=s.redact_pii,
                    revise=revise,
                ).run()
            except LLMError as exc:
                status.update(label="Failed", state="error")
                st.error(str(exc))
                return
            status.update(label=f"Done in {result.duration_s}s", state="complete")
        ws.reports.insert(
            0,
            {
                "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "model": result.model,
                "markdown": result.report_markdown,
                "analyses": result.analyses,
                "strategy": result.strategy,
                "review": result.review,
            },
        )
        ws.reports = ws.reports[:10]
        state.save()
        state.audit().append(
            "agents.run",
            model=result.model,
            seconds=result.duration_s,
            verdict=result.review.get("verdict"),
        )

    if not ws.reports:
        return
    labels = [f"{r['created']} · {r['model']}" for r in ws.reports]
    i = st.selectbox("Report", range(len(labels)), format_func=lambda k: labels[k])
    rep = ws.reports[i]
    t1, t2, t3 = st.tabs(["Report", "Agent outputs", "Review"])
    with t1:
        d1, d2 = st.columns(2)
        d1.download_button("Download Markdown", rep["markdown"], "esg_report.md", "text/markdown")
        d2.download_button(
            "Download HTML", to_html(rep["markdown"]), "esg_report.html", "text/html"
        )
        st.markdown(safe_markdown(rep["markdown"]))
    with t2:
        for catg, an in rep.get("analyses", {}).items():
            with st.expander(f"{catg} analyst", expanded=False):
                st.json(an)
        st.subheader("Strategy")
        st.json(rep.get("strategy", {}))
    with t3:
        rv = rep.get("review", {})
        st.markdown(
            f"**Verdict:** {rv.get('verdict', 'n/a')}" + (" (revised)" if rv.get("revised") else "")
        )
        for issue in rv.get("issues", []):
            st.markdown("- " + safe_markdown(str(issue)))


# --------------------------------------------------------------------- ADVISOR
def advisor() -> None:
    st.title("💬 ESG advisor")
    if _need_industry():
        return
    ws = state.workspace()
    llm = state.llm()
    ok, msg = llm.health()
    if not ok:
        st.error(msg)
        return
    c1, c2 = st.columns([4, 1])
    c1.caption("Grounded in your data via tools: KPIs, scores, forecasts, what-if, your documents.")
    if c2.button("Clear chat"):
        ws.chat = []
        state.save()
    for m in ws.chat:
        with st.chat_message(m["role"]):
            st.markdown(safe_markdown(m["content"]))
            if m.get("steps"):
                with st.expander(f"🔧 {len(m['steps'])} tool call(s)"):
                    for stp in m["steps"]:
                        st.code(f"{stp['tool']}({json.dumps(stp['arguments'])})", language="python")
    if not ws.chat:
        st.markdown(
            "Try: *What are our three weakest KPIs and how do we fix them?* · "
            "*What happens to our score if we cut scope 1 emissions by 25%?*"
        )
    q = st.chat_input("Ask about your ESG performance…")
    if not q:
        return
    with st.chat_message("user"):
        st.markdown(safe_markdown(q))
    with st.chat_message("assistant"), st.spinner("Thinking locally…"):
        try:
            res = Advisor(
                llm,
                state.agent_context(),
                state.settings().agent_max_steps,
                state.settings().redact_pii,
            ).ask(q, ws.chat)
        except LLMError as exc:
            st.error(str(exc))
            return
        st.markdown(safe_markdown(res.content))
    ws.chat += [
        {"role": "user", "content": q},
        {"role": "assistant", "content": res.content, "steps": res.steps},
    ]
    ws.chat = ws.chat[-40:]
    state.save()
    state.audit().append("advisor.ask", tools=len(res.steps))
    st.rerun()


# ------------------------------------------------------------------- DOCUMENTS
def documents() -> None:
    st.title("📚 Documents")
    st.caption(
        "Index your own policies and prior reports for the advisor and agents. Text is encrypted "
        "at rest; embeddings are computed locally."
    )
    store = state.documents()
    emb = state.scorer().embedder
    st.markdown(
        f"Embedding: `{emb.name}`"
        + (
            "  — tip: `ollama pull nomic-embed-text` for better retrieval"
            if emb.name.startswith("hashing")
            else ""
        )
    )
    redact_on = st.toggle(
        "Redact personal data (emails, phones, IBANs…) before indexing", value=True
    )
    files = st.file_uploader(
        "Add documents", type=["txt", "md", "pdf", "csv"], accept_multiple_files=True
    )
    if files and st.button("Index", type="primary"):
        for f in files:
            try:
                text = read_document(f.name, f.getvalue())
            except IngestError as exc:
                st.error(f"{f.name}: {exc}")
                continue
            removed = 0
            if redact_on:
                rr = redact(text)
                text, removed = rr.text, rr.total
            n = store.add(f.name, text)
            st.success(
                f"{f.name}: {n} chunks indexed"
                + (f", {removed} PII items redacted" if removed else "")
            )
            state.audit().append("docs.indexed", chunks=n, redacted=removed)
    docs = store.documents()
    if docs:
        st.dataframe(pd.DataFrame(docs), hide_index=True, use_container_width=True)
        c1, c2 = st.columns(2)
        rm = c1.selectbox(
            "Remove document", [None, *docs], format_func=lambda d: "—" if d is None else d["name"]
        )
        if rm and c1.button("Remove"):
            store.remove(rm["id"])
            st.rerun()
        if c2.button("Remove all documents"):
            store.clear()
            st.rerun()
        q = st.text_input("Test search")
        if q:
            for h in store.search(q):
                with st.expander(f"{h.doc} #{h.chunk} · {h.score:.2f}"):
                    st.markdown(safe_markdown(h.text))


# -------------------------------------------------------------------- SETTINGS
def settings_page() -> None:
    st.title("🔒 Models & privacy")
    s = state.settings()
    llm = state.llm()
    ok, msg = llm.health()

    st.subheader("Local model")
    st.caption(msg)
    if ok:
        models = llm.list_models()
        chat_models = [m for m in models if not is_embedding_model(m)] or models
        if chat_models:
            cur = chat_models.index(llm.model) if llm.model in chat_models else 0
            pick = st.selectbox("Chat model", chat_models, index=cur)
            if pick != s.chat_model:
                s.chat_model = pick
        s.temperature = st.slider("Temperature", 0.0, 1.0, float(s.temperature), 0.05)
        s.agent_concurrency = st.number_input(
            "Parallel agents",
            1,
            4,
            s.agent_concurrency,
            help="Raise only if Ollama runs with OLLAMA_NUM_PARALLEL > 1 and you have the RAM.",
        )

    if s.provider == "ollama" and ok:
        with st.expander("Download or import models"):
            st.markdown(
                "Downloading needs internet **once** (done by the Ollama daemon); "
                "afterwards everything runs offline."
            )
            st.dataframe(
                pd.DataFrame(RECOMMENDED_MODELS), hide_index=True, use_container_width=True
            )
            name = st.text_input("Model to pull", placeholder="e.g. qwen2.5:7b or nomic-embed-text")
            if st.button("Pull model") and name:
                bar = st.progress(0.0, text=f"Pulling {name}…")
                try:
                    for ev in llm.pull(name.strip()):
                        if ev.get("total"):
                            bar.progress(
                                min(ev.get("completed", 0) / ev["total"], 1.0),
                                text=ev.get("status", ""),
                            )
                    st.success(f"{name} ready.")
                    state.audit().append("model.pulled", model=name)
                except LLMError as exc:
                    st.error(str(exc))
            st.markdown(
                "**Air-gapped install:** copy a `.gguf` file to this machine and register it."
            )
            gguf = st.text_input("Path to .gguf file")
            gname = st.text_input("Register as", placeholder="my-model")
            if st.button("Import GGUF") and gguf and gname:
                try:
                    llm.import_gguf(gguf, gname)
                    st.success(f"Imported as {gname}.")
                    state.audit().append("model.imported", model=gname)
                except LLMError as exc:
                    st.error(str(exc))

    st.subheader("Privacy status")
    st.markdown(
        f"- Model endpoint: `{s.base_url}` (loopback-only enforced)\n"
        f"- Offline network guard: **{'on' if network_guard.is_installed() else 'off'}**\n"
        f"- PII redaction in prompts: **{'on' if s.redact_pii else 'off'}**\n"
        f"- Data directory: `{s.home}`\n"
        "- Telemetry: **none** (Streamlit usage stats disabled)"
    )
    blocked = network_guard.blocked_attempts()
    if blocked:
        st.warning(f"Blocked outbound attempts this session: {', '.join(sorted(set(blocked)))}")

    st.subheader("Encryption")
    v = state.vault()
    st.markdown(
        f"Vault protected by **{'passphrase' if v.passphrase_protected else 'local key file'}**."
    )
    with st.form("pass"):
        p1 = st.text_input("New passphrase", type="password")
        p2 = st.text_input("Repeat", type="password")
        if st.form_submit_button("Set passphrase"):
            if len(p1) < 10:
                st.error("Use at least 10 characters.")
            elif p1 != p2:
                st.error("Passphrases differ.")
            else:
                docs = state.documents()
                v.change_passphrase(p1, rekey=[docs.reencrypt] if docs else [])
                state.audit().append("vault.passphrase_changed")
                st.success("Vault re-encrypted. You'll need this passphrase next time.")

    st.subheader("Audit log")
    log = state.audit()
    okv, n, broken = log.verify()
    st.markdown(
        f"{'✅ intact' if okv else f'❌ tampered at entry {broken}'} · {n} entries (metadata only)"
    )
    st.dataframe(
        pd.DataFrame(log.entries(limit=50))[["ts", "event", "details"]] if n else pd.DataFrame(),
        hide_index=True,
        use_container_width=True,
    )

    st.subheader("Your data")
    ws = state.workspace()
    buf = io.StringIO()
    json.dump(ws.to_dict(), buf, indent=2, default=str)
    st.download_button("Export workspace (JSON, unencrypted)", buf.getvalue(), "esg_workspace.json")
    if st.checkbox(
        "I understand this permanently deletes all workspaces, reports, chats and documents"
    ):
        if st.button("Delete all my data", type="primary"):
            removed = v.purge()
            if state.documents():
                state.documents().clear()
            st.session_state.ws = type(ws)()
            state.invalidate()
            state.audit().append("data.purged", records=removed)
            st.success(f"Deleted {removed} records.")
