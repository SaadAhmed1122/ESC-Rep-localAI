import json

import httpx
import pytest

from esglocal.embeddings import HashingEmbedder, get_embedder
from esglocal.llm.base import LLMError
from esglocal.llm.fake import FakeLLM
from esglocal.llm.ollama import OllamaClient, is_embedding_model
from esglocal.llm.openai_compat import OpenAICompatClient
from esglocal.quality import check_quality
from esglocal.rag.ingest import IngestError, chunk_text, read_document
from esglocal.rag.store import DocumentStore
from esglocal.reporting import build_report, markdown_to_html, to_html
from esglocal.security.vault import Vault

# ----------------------------------------------------------------------- RAG


def test_chunking_respects_size():
    text = "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(20))
    chunks = chunk_text(text, max_chars=500, overlap=50)
    assert len(chunks) > 3
    assert all(len(c) <= 500 + 60 for c in chunks)
    assert chunk_text("") == []


def test_read_document_types():
    assert read_document("a.md", b"# Title") == "# Title"
    with pytest.raises(IngestError):
        read_document("a.exe", b"MZ")


def test_store_search_dedupe_and_encryption(tmp_path):
    vault = Vault(tmp_path / "v.db")
    store = DocumentStore(
        tmp_path / "docs.db", HashingEmbedder(), vault.encrypt_bytes, vault.decrypt_bytes
    )
    policy = (
        "Whistleblowing channel is anonymous and run by a third party.\n\nSECRETMARKER bribery."
    )
    assert store.add("ethics.md", policy) > 0
    assert store.add("ethics.md", policy) == 0  # same content not indexed twice
    store.add("hr.md", "Training hours per employee rose to 27 hours.")
    hits = store.search("anonymous whistleblowing")
    assert hits and hits[0].doc == "ethics.md"
    assert b"SECRETMARKER" not in (tmp_path / "docs.db").read_bytes()
    store.remove(store.documents()[0]["id"])
    assert len(store.documents()) == 1


def test_get_embedder_falls_back_offline():
    assert get_embedder().name.startswith("hashing")
    assert get_embedder(FakeLLM(), "nomic-embed-text").name.startswith("hashing")


# ------------------------------------------------------------------ reports


def test_report_and_html_are_safe(catalog, assessment):
    md = build_report(
        "Evil <script>alert(1)</script>", assessment, catalog, "Body **bold**", model="m"
    )
    assert "## ESG scorecard" in md and "| **Overall** |" in md
    html = to_html(md)
    assert "<script" not in html.lower()  # stripped by safe_markdown, escaped by to_html
    assert "<table>" in html and "<strong>bold</strong>" in html


def test_markdown_lists_and_tables():
    out = markdown_to_html("# T\n- a\n- b\n\n1. x\n   - y\n\n| h |\n|---|\n| v \\| w |")
    assert "<ul>" in out and "<ol>" in out and "<td>v | w</td>" in out


def test_quality_checks(catalog, assessment, table):
    issues = check_quality(catalog, assessment, table)
    msgs = " ".join(i.message for i in issues)
    assert "Fatalities" not in msgs, "1 -> 0 fatalities must not be flagged as a data error"
    assert all(i.severity != "error" for i in issues)


# ---------------------------------------------------------------- clients


def _ollama(handler) -> OllamaClient:
    c = OllamaClient(model="m")
    c._http = httpx.Client(
        base_url="http://127.0.0.1:11434", transport=httpx.MockTransport(handler)
    )
    return c


def test_ollama_chat_with_tool_calls():
    seen = {}

    def handler(req):
        body = json.loads(req.content)
        seen.update(body)
        return httpx.Response(
            200,
            json={
                "model": "m",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "get_kpi", "arguments": {"kpi_id": "E-GHG-INT"}}}
                    ],
                },
            },
        )

    c = _ollama(handler)
    msgs = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "1", "name": "x", "arguments": {}}],
        },
        {"role": "tool", "tool_call_id": "1", "name": "x", "content": "r"},
    ]
    resp = c.chat(msgs, tools=[{"name": "get_kpi", "description": "", "parameters": {}}])
    assert resp.tool_calls[0].name == "get_kpi" and resp.tool_calls[0].arguments == {
        "kpi_id": "E-GHG-INT"
    }
    assert seen["stream"] is False and seen["tools"][0]["type"] == "function"
    assert seen["messages"][2] == {"role": "tool", "content": "r", "tool_name": "x"}
    assert seen["messages"][1]["tool_calls"][0]["function"]["name"] == "x"


def test_ollama_errors_and_models():
    c = _ollama(lambda req: httpx.Response(500, text="boom"))
    with pytest.raises(LLMError, match="500"):
        c.chat([{"role": "user", "content": "x"}])

    def tags(req):
        return httpx.Response(
            200,
            json={
                "models": [
                    {"name": "nomic-embed-text:latest", "size": 1},
                    {"name": "qwen2.5:7b", "size": 2},
                ]
            },
        )

    c = _ollama(tags)
    assert c.default_chat_model() == "qwen2.5:7b"
    assert is_embedding_model("nomic-embed-text") and not is_embedding_model("llama3.1")


def test_ollama_requires_model():
    c = OllamaClient(model="")
    with pytest.raises(LLMError, match="No chat model"):
        c.chat([{"role": "user", "content": "x"}])


def test_openai_compat_parsing():
    def handler(req):
        body = json.loads(req.content)
        assert body["messages"][1]["tool_calls"][0]["function"]["arguments"] == "{}"
        return httpx.Response(
            200,
            json={
                "model": "local",
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {"id": "c9", "function": {"name": "get_scores", "arguments": "{}"}}
                            ],
                        }
                    }
                ],
            },
        )

    c = OpenAICompatClient(model="local")
    c._http = httpx.Client(
        base_url="http://127.0.0.1:1234/v1", transport=httpx.MockTransport(handler)
    )
    resp = c.chat(
        [
            {"role": "user", "content": "x"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "a", "name": "n", "arguments": {}}],
            },
        ]
    )
    assert resp.tool_calls[0].id == "c9" and resp.content == ""
