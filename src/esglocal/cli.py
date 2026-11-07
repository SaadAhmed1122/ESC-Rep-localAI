"""``esglocal`` command-line interface."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

from esglocal import __version__
from esglocal.config import ConfigError, Settings


def _settings(args) -> Settings:
    s = Settings.from_env()
    if getattr(args, "model", None):
        s.chat_model = args.model
    if getattr(args, "provider", None):
        s.provider = args.provider
    if getattr(args, "base_url", None):
        s.base_url = args.base_url
    s.validate()
    if s.offline:
        from esglocal.privacy import network_guard

        network_guard.install(s.allowed_hosts)
    return s


def _read_table(path: str) -> pd.DataFrame:
    p = Path(path)
    if p.suffix.lower() in {".xlsx", ".xls"}:
        return pd.read_excel(p)
    return pd.read_csv(p)


def _prepare(args, settings: Settings, llm=None):
    from esglocal.agents.tools import AgentContext
    from esglocal.calculator import build_period_table
    from esglocal.catalog import load_catalog
    from esglocal.embeddings import get_embedder
    from esglocal.mapping import suggest_mapping
    from esglocal.narrative import NarrativeScorer
    from esglocal.samples import SAMPLE_INDUSTRY, sample_dataframe, sample_narratives
    from esglocal.scoring import assess

    catalog = load_catalog(args.catalog) if getattr(args, "catalog", None) else load_catalog()
    if args.data == "sample":
        df, industry = sample_dataframe(), args.industry or SAMPLE_INDUSTRY
        narratives = sample_narratives()
    else:
        df, industry = _read_table(args.data), args.industry
        narratives = {}
    if not industry:
        sys.exit("--industry is required (see `esglocal industries`)")
    if getattr(args, "narratives", None):
        narratives.update(json.loads(Path(args.narratives).read_text("utf-8")))
    inputs = catalog.required_inputs(industry)
    mapping = suggest_mapping(df.columns, inputs)
    table = build_period_table(df, mapping, inputs)
    embedder = get_embedder(llm, settings.embed_model) if llm else get_embedder()
    scorer = NarrativeScorer(embedder)
    assessment = assess(catalog, industry, table, narratives, scorer)
    ctx = AgentContext(
        catalog=catalog,
        industry=industry,
        assessment=assessment,
        table=table,
        narratives=narratives,
        scorer=scorer,
        company=getattr(args, "company", "") or "",
    )
    return ctx, mapping, inputs


# --------------------------------------------------------------------- commands
def cmd_doctor(args) -> int:
    from esglocal.llm.factory import create_client
    from esglocal.llm.ollama import is_embedding_model
    from esglocal.privacy import network_guard

    try:
        s = _settings(args)
    except ConfigError as exc:
        print(f"✗ config: {exc}")
        return 1
    print(f"esglocal {__version__}")
    print(f"✓ provider={s.provider} endpoint={s.base_url} (local-only check passed)")
    print(
        f"{'✓' if network_guard.is_installed() else '!'} offline network guard "
        f"{'active' if network_guard.is_installed() else 'disabled (ESGLOCAL_OFFLINE=0)'}"
    )
    client = create_client(s)
    ok, msg = client.health()
    print(f"{'✓' if ok else '✗'} {msg}")
    if ok:
        models = client.list_models()
        chat = [m for m in models if not is_embedding_model(m)]
        emb = [m for m in models if is_embedding_model(m)]
        print(
            f"{'✓' if chat else '✗'} chat models: {', '.join(chat) or 'none — run: ollama pull qwen2.5:7b'}"
        )
        print(
            f"{'✓' if emb else '!'} embedding models: {', '.join(emb) or 'none (offline hashing fallback in use)'}"
        )
        print(f"  selected chat model: {client.model or '—'}")
    print(f"  data directory: {s.home} (encrypted vault: {s.vault_path.name})")
    return 0 if ok else 1


def cmd_models(args) -> int:
    from esglocal.llm.factory import RECOMMENDED_MODELS, create_client

    s = _settings(args)
    if args.action == "recommended":
        for m in RECOMMENDED_MODELS:
            print(f"{m['name']:22} {m['size']:>9}  {m['use']}")
        return 0
    client = create_client(s)
    if args.action == "list":
        if hasattr(client, "model_details"):
            for m in client.model_details():
                print(f"{m.name:32} {m.size_gb:>6} GB  {m.parameter_size:>8} {m.quantization}")
        else:
            print("\n".join(client.list_models()))
        return 0
    if s.provider != "ollama":
        print("pull / rm / import are only supported for the Ollama provider")
        return 1
    if args.action == "pull":
        print(f"Pulling {args.name} via the local Ollama daemon (needs internet once)…")
        last = ""
        for ev in client.pull(args.name):
            status = ev.get("status", "")
            if ev.get("total"):
                pct = 100 * ev.get("completed", 0) / ev["total"]
                print(f"\r{status} {pct:5.1f}%", end="", flush=True)
            elif status != last:
                print(f"\n{status}", end="", flush=True)
            last = status
        print("\ndone")
    elif args.action == "rm":
        client.delete(args.name)
        print(f"removed {args.name}")
    elif args.action == "import":
        client.import_gguf(args.name, args.as_name or Path(args.name).stem)
        print(f"imported {args.name} as {args.as_name or Path(args.name).stem}")
    return 0


def cmd_industries(args) -> int:
    from esglocal.catalog import load_catalog

    for ind in load_catalog().industries:
        w = ind.weights
        print(
            f"{ind.sector:20} {ind.name:32} E{w['Environmental']:.0%} S{w['Social']:.0%} "
            f"G{w['Governance']:.0%}"
        )
    return 0


def cmd_score(args) -> int:
    from esglocal.reporting import kpi_table, scores_table

    s = Settings.from_env()
    if s.offline:
        from esglocal.privacy import network_guard

        network_guard.install(s.allowed_hosts)
    ctx, mapping, inputs = _prepare(args, s)
    if args.json:
        print(json.dumps({**ctx.assessment.to_dict(), "mapping": mapping}, indent=2))
        return 0
    print(f"Mapped {len(mapping)}/{len(inputs)} inputs")
    print(scores_table(ctx.assessment))
    print()
    print(kpi_table(ctx.assessment, ctx.catalog))
    return 0


def cmd_analyze(args) -> int:
    from esglocal.agents.orchestrator import ESGPipeline
    from esglocal.llm.factory import create_client
    from esglocal.reporting import to_html
    from esglocal.security.audit import AuditLog

    s = _settings(args)
    llm = create_client(s)
    ok, msg = llm.health()
    if not ok:
        print(f"✗ {msg}\nStart Ollama (`ollama serve`) or pick another provider.")
        return 1
    ctx, mapping, _ = _prepare(args, s, llm)

    def on_event(ev):
        if ev["status"] in {"start", "done", "error"} or ev["stage"] == "tool":
            print(f"[{ev['stage']:>8}] {ev['status']:5} {ev['message']}", file=sys.stderr)

    print(f"Running agents with local model {llm.model!r}…", file=sys.stderr)
    result = ESGPipeline(
        llm,
        ctx,
        on_event=on_event,
        concurrency=s.agent_concurrency,
        max_steps=s.agent_max_steps,
        redact_pii=s.redact_pii,
    ).run()
    out = Path(args.out)
    out.write_text(result.report_markdown, encoding="utf-8")
    print(f"Report written to {out} ({result.duration_s}s)", file=sys.stderr)
    if args.html:
        Path(args.html).write_text(to_html(result.report_markdown), encoding="utf-8")
        print(f"HTML written to {args.html}", file=sys.stderr)
    s.ensure_home()
    AuditLog(s.audit_path).append(
        "cli.analyze", industry=ctx.industry, model=result.model, kpis=len(ctx.assessment.results)
    )
    return 0


def cmd_ask(args) -> int:
    from esglocal.agents.advisor import Advisor
    from esglocal.llm.factory import create_client

    s = _settings(args)
    llm = create_client(s)
    ctx, _, _ = _prepare(args, s, llm)
    result = Advisor(llm, ctx, max_steps=s.agent_max_steps, redact_pii=s.redact_pii).ask(
        args.question
    )
    for st in result.steps:
        print(f"  ↳ tool {st['tool']}({json.dumps(st['arguments'])})", file=sys.stderr)
    print(result.content)
    return 0


def cmd_ui(args) -> int:
    app = Path(__file__).with_name("ui") / "app.py"
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app),
        "--server.address",
        "127.0.0.1",
        "--server.port",
        str(args.port),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
        "--server.enableXsrfProtection",
        "true",
        "--server.maxUploadSize",
        "50",
        "--theme.base",
        "light",
    ]
    print(f"ESG Local AI → http://127.0.0.1:{args.port}  (local only)")
    env = {**os.environ, "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false"}
    return subprocess.call(cmd, env=env)  # noqa: S603 - fixed argv, no shell


def cmd_audit(args) -> int:
    from esglocal.security.audit import AuditLog

    s = Settings.from_env()
    if not s.audit_path.exists():
        print("No audit log yet.")
        return 0
    log = AuditLog(s.audit_path)
    if args.action == "verify":
        ok, n, broken = log.verify()
        print(f"{'✓ intact' if ok else f'✗ tampered at entry {broken}'} ({n} entries)")
        return 0 if ok else 1
    for e in log.entries(limit=args.limit):
        print(f"{e['ts']}  {e['event']:24} {json.dumps(e['details'])}")
    return 0


def cmd_sample(args) -> int:
    from esglocal.samples import sample_csv_text

    Path(args.out).write_text(sample_csv_text(), encoding="utf-8")
    print(f"Sample data written to {args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="esglocal", description="Private, local-AI ESG reporting")
    p.add_argument("--version", action="version", version=f"esglocal {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def llm_opts(sp):
        sp.add_argument("--model", help="chat model name (default: first installed)")
        sp.add_argument("--provider", choices=["ollama", "openai_compat", "fake"])
        sp.add_argument("--base-url", help="local model endpoint URL")

    def data_opts(sp):
        sp.add_argument("data", help="CSV/XLSX file, or 'sample' for the bundled demo")
        sp.add_argument("--industry", help="industry name (see `esglocal industries`)")
        sp.add_argument("--narratives", help="JSON {kpi_id: text} for qualitative KPIs")
        sp.add_argument("--company", default="")
        sp.add_argument("--catalog", help="custom KPI catalog YAML")

    sp = sub.add_parser("doctor", help="check local model runtime and privacy settings")
    llm_opts(sp)
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("models", help="manage local models")
    sp.add_argument("action", choices=["list", "pull", "rm", "import", "recommended"])
    sp.add_argument("name", nargs="?", help="model name, or .gguf path for import")
    sp.add_argument("--as", dest="as_name", help="model name to register a GGUF as")
    llm_opts(sp)
    sp.set_defaults(func=cmd_models)

    sp = sub.add_parser("industries", help="list supported industries")
    sp.set_defaults(func=cmd_industries)

    sp = sub.add_parser("score", help="compute KPIs and scores (no LLM)")
    data_opts(sp)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_score)

    sp = sub.add_parser("analyze", help="run the multi-agent analysis and write a report")
    data_opts(sp)
    llm_opts(sp)
    sp.add_argument("--out", default="esg_report.md")
    sp.add_argument("--html", help="also write an HTML report")
    sp.set_defaults(func=cmd_analyze)

    sp = sub.add_parser("ask", help="ask the ESG advisor a question about your data")
    sp.add_argument("question")
    data_opts(sp)
    llm_opts(sp)
    sp.set_defaults(func=cmd_ask)

    sp = sub.add_parser("ui", help="launch the web UI on 127.0.0.1")
    sp.add_argument("--port", type=int, default=8501)
    sp.set_defaults(func=cmd_ui)

    sp = sub.add_parser("audit", help="show or verify the tamper-evident audit log")
    sp.add_argument("action", choices=["show", "verify"])
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(func=cmd_audit)

    sp = sub.add_parser("sample", help="write the demo dataset to a CSV")
    sp.add_argument("--out", default="sample_company.csv")
    sp.set_defaults(func=cmd_sample)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in {"models"} and args.action in {"pull", "rm", "import"} and not args.name:
        print("a model name (or .gguf path) is required")
        return 2
    try:
        return int(args.func(args) or 0)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
