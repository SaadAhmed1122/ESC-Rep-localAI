"""Session services for the Streamlit UI (settings, vault, workspace, LLM)."""

from __future__ import annotations

import streamlit as st

from esglocal.agents.tools import AgentContext
from esglocal.catalog import Catalog, load_catalog
from esglocal.config import Settings
from esglocal.embeddings import get_embedder
from esglocal.llm.factory import create_client
from esglocal.narrative import NarrativeScorer
from esglocal.privacy import network_guard
from esglocal.rag.store import DocumentStore
from esglocal.security.audit import AuditLog
from esglocal.security.vault import Vault, VaultError
from esglocal.workspace import Workspace, load_workspace, save_workspace


@st.cache_resource
def catalog() -> Catalog:
    return load_catalog()


def settings() -> Settings:
    if "settings" not in st.session_state:
        s = Settings.from_env()
        if s.offline:
            network_guard.install(s.allowed_hosts)
        s.ensure_home()
        st.session_state.settings = s
    return st.session_state.settings


def llm():
    s = settings()
    key = (s.provider, s.base_url, s.chat_model, s.temperature, s.think)
    if st.session_state.get("_llm_key") != key:
        st.session_state.llm = create_client(s)
        st.session_state._llm_key = key
    return st.session_state.llm


def audit() -> AuditLog:
    if "audit" not in st.session_state:
        st.session_state.audit = AuditLog(settings().audit_path)
    return st.session_state.audit


def vault() -> Vault | None:
    return st.session_state.get("vault")


def open_vault(passphrase: str | None = None) -> str | None:
    """Open the vault; returns an error message or None."""
    try:
        st.session_state.vault = Vault(settings().vault_path, passphrase or settings().passphrase)
    except VaultError as exc:
        return str(exc)
    ws_name = st.session_state.get("ws_name", "default")
    st.session_state.ws = load_workspace(st.session_state.vault, ws_name)
    invalidate()
    audit().append("vault.opened", passphrase=bool(passphrase or settings().passphrase))
    return None


def workspace() -> Workspace:
    if "ws" not in st.session_state:
        st.session_state.ws = Workspace()
    return st.session_state.ws


def save() -> None:
    v = vault()
    if v is not None:
        save_workspace(v, workspace())
    invalidate()  # always drop derived caches after an edit


def scorer() -> NarrativeScorer:
    s = settings()
    key = (s.provider, s.base_url, s.embed_model)
    if st.session_state.get("_scorer_key") != key:
        try:
            emb = get_embedder(llm(), s.embed_model)
        except Exception:  # noqa: BLE001
            emb = get_embedder()
        st.session_state.scorer = NarrativeScorer(emb)
        st.session_state._scorer_key = key
    return st.session_state.scorer


def documents() -> DocumentStore | None:
    v = vault()
    if v is None:
        return None
    if (
        "docstore" not in st.session_state
        or st.session_state.docstore.embedder is not scorer().embedder
    ):
        st.session_state.docstore = DocumentStore(
            settings().rag_path, scorer().embedder, v.encrypt_bytes, v.decrypt_bytes
        )
    return st.session_state.docstore


def assessment():
    ws = workspace()
    if not ws.industry:
        return None
    sc = scorer()
    if (
        "_assessment" not in st.session_state
        or st.session_state.get("_assessment_scorer") is not sc
    ):
        st.session_state._assessment = ws.assessment(catalog(), sc)
        st.session_state._assessment_scorer = sc
    return st.session_state._assessment


def invalidate() -> None:
    for key in ("_assessment", "_ctx"):
        st.session_state.pop(key, None)


def agent_context() -> AgentContext | None:
    ws = workspace()
    a = assessment()
    if a is None:
        return None
    docs = documents()
    return AgentContext(
        catalog=catalog(),
        industry=ws.industry,
        assessment=a,
        table=ws.table(catalog()),
        narratives=ws.narratives,
        scorer=scorer(),
        documents=docs if docs and docs.documents() else None,
        company=ws.company,
    )
