"""Streamlit entry point: ``esglocal ui`` (binds to 127.0.0.1 only)."""

from __future__ import annotations

import streamlit as st

from esglocal import __version__
from esglocal.privacy import network_guard
from esglocal.ui import state, views

st.set_page_config(page_title="ESG Local AI", page_icon="🌿", layout="wide")

s = state.settings()

# ---------------------------------------------------------------- vault unlock
if state.vault() is None:
    err = state.open_vault()
    if err:
        st.title("🔒 Unlock your ESG vault")
        st.caption("Your data is encrypted on this device. Enter the vault passphrase.")
        with st.form("unlock"):
            pw = st.text_input("Passphrase", type="password")
            if st.form_submit_button("Unlock", type="primary"):
                err2 = state.open_vault(pw)
                if err2:
                    st.error(err2)
                else:
                    st.rerun()
        st.stop()

# -------------------------------------------------------------------- sidebar
with st.sidebar:
    st.markdown(
        f"### 🌿 ESG Local AI  \n<small>v{__version__} · 100% on-device</small>",
        unsafe_allow_html=True,
    )
    ok, msg = state.llm().health()
    st.markdown(f"{'🟢' if ok else '🔴'} **Model runtime** — {msg}")
    if ok:
        st.caption(f"Chat model: `{state.llm().model or 'none'}`")
    st.markdown(
        f"{'🛡️' if network_guard.is_installed() else '⚠️'} **Offline guard** "
        f"{'on' if network_guard.is_installed() else 'off'}"
    )
    st.markdown(
        f"🔐 **Vault** {'passphrase' if state.vault().passphrase_protected else 'key-file'} encrypted"
    )
    ws = state.workspace()
    if ws.industry:
        st.caption(f"Workspace: **{ws.company or 'Unnamed company'}** · {ws.industry}")

pages = [
    st.Page(views.home, title="Home", icon="🏠", default=True),
    st.Page(views.data, title="Data & KPIs", icon="📥", url_path="data"),
    st.Page(views.dashboard, title="Dashboard", icon="📊", url_path="dashboard"),
    st.Page(views.what_if, title="What-if simulator", icon="🧪", url_path="what-if"),
    st.Page(views.agents, title="AI agents report", icon="🤖", url_path="agents"),
    st.Page(views.advisor, title="Advisor chat", icon="💬", url_path="advisor"),
    st.Page(views.documents, title="Documents", icon="📚", url_path="documents"),
    st.Page(views.settings_page, title="Models & privacy", icon="🔒", url_path="settings"),
]
st.navigation(pages).run()
