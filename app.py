"""BusSafar Support: Streamlit front end for Sahil.

Run locally:  streamlit run app.py
"""
from __future__ import annotations

import hmac
import html
import time

import pandas as pd
import streamlit as st

import config
from agent.sahil import GeminiLLM, LLMError, Sahil
from booking.booking_lookup import format_booking, get_booking_status
from support.ticket_manager import CATEGORIES, TicketError, TicketManager

st.set_page_config(page_title="BusSafar Support", page_icon="🚌", layout="centered")

st.markdown(
    """
    <style>
      :root { --bs-red: #D7263D; --bs-ink: #1B1B2F; }
      .bs-header { display:flex; align-items:center; gap:14px; padding:14px 18px;
                   border-radius:14px; background:linear-gradient(90deg,#D7263D,#F46036); color:#fff;
                   margin-bottom:12px; }
      .bs-logo { width:52px; height:52px; border-radius:12px; background:#fff; color:#D7263D;
                 display:flex; align-items:center; justify-content:center; font-weight:800;
                 font-size:20px; flex-shrink:0; }
      .bs-title { font-size:24px; font-weight:800; line-height:1.1; margin:0; }
      .bs-sub { font-size:14px; opacity:.92; margin:2px 0 0; }
      div[data-testid="stChatMessage"] { border-radius:12px; }
      .bs-small { font-size:12px; color:#6b6b80; }
      @media (max-width: 640px) { .bs-title { font-size:20px; } .bs-logo { width:44px; height:44px; } }
    </style>
    """,
    unsafe_allow_html=True,
)


def header() -> None:
    st.markdown(
        """
        <div class="bs-header">
          <div class="bs-logo" aria-label="BusSafar logo placeholder">BS</div>
          <div><div class="bs-title">BusSafar Support</div>
          <div class="bs-sub">{tagline}</div></div>
        </div>
        """.replace("{tagline}", html.escape(config.APP_TAGLINE)),
        unsafe_allow_html=True,
    )


# --- Passcode gate --------------------------------------------------------
def passcode_gate() -> bool:
    if st.session_state.get("authenticated"):
        return True
    header()
    expected = config.app_passcode()
    if not expected:
        st.error("The app passcode is not configured. Set APP_PASSCODE in .env or Streamlit secrets.")
        return False
    locked_until = st.session_state.get("locked_until", 0.0)
    if time.time() < locked_until:
        st.error(f"Too many wrong attempts. Try again in {int(locked_until - time.time()) + 1} seconds.")
        return False
    with st.form("passcode_form"):
        code = st.text_input("Enter the passcode to open the chat", type="password")
        submitted = st.form_submit_button("Unlock")
    if submitted:
        if hmac.compare_digest(code.encode(), expected.encode()):
            st.session_state.authenticated = True
            st.session_state.pop("passcode_attempts", None)
            st.rerun()
        attempts = st.session_state.get("passcode_attempts", 0) + 1
        st.session_state.passcode_attempts = attempts
        if attempts >= config.MAX_PASSCODE_ATTEMPTS:
            st.session_state.locked_until = time.time() + config.PASSCODE_LOCKOUT_SECONDS
            st.session_state.passcode_attempts = 0
        st.error("Wrong passcode. The chat stays locked.")
    return False


# --- Shared resources -----------------------------------------------------
@st.cache_resource(show_spinner="Preparing Sahil's knowledge base (first start can take a minute)...")
def load_retriever():
    from rag.embeddings import GeminiEmbedder
    from rag.retrieval import Retriever
    from rag.vector_store import ensure_index

    embedder = GeminiEmbedder()
    collection = ensure_index(embedder)
    return Retriever(collection, embedder)


@st.cache_resource
def ticket_manager() -> TicketManager:
    return TicketManager(config.TICKETS_CSV)


def get_agent() -> tuple[Sahil, str | None]:
    if "agent" in st.session_state:
        return st.session_state.agent, st.session_state.get("agent_error")
    error = None
    retriever = llm = None
    if not config.google_api_key():
        error = "GOOGLE_API_KEY is not set, so chat is unavailable. Booking lookup and tickets still work."
    else:
        try:
            retriever = load_retriever()
            llm = GeminiLLM()
        except Exception as exc:  # noqa: BLE001 - shown to the operator, not the model
            error = f"Could not start the AI service: {type(exc).__name__}. Check the API key and model names."
            retriever = llm = None
    agent = Sahil(retriever, llm, ticket_manager(), config.BOOKINGS_CSV)
    st.session_state.agent = agent
    st.session_state.agent_error = error
    return agent, error


# --- Sidebar ----------------------------------------------------------------
def sidebar(agent: Sahil) -> None:
    with st.sidebar:
        st.markdown("### Check booking status")
        with st.form("booking_form", clear_on_submit=False):
            pnr = st.text_input("PNR", placeholder="BS123456", max_chars=12)
            last4 = st.text_input("Last 4 digits of phone", type="password", max_chars=4)
            go = st.form_submit_button("Check status")
        if go:
            if agent.failed_lookups >= config.MAX_FAILED_LOOKUPS:
                st.error("Too many unsuccessful attempts. Please raise a support ticket.")
            else:
                result = get_booking_status(pnr, last4, config.BOOKINGS_CSV)
                if result["found"]:
                    st.success(format_booking(result["booking"]))
                else:
                    agent.failed_lookups += result["error"] == "not_found"
                    st.error(result["message"])

        st.markdown("### Contact a person")
        with st.form("ticket_form", clear_on_submit=True):
            labels = {v: k for k, v in CATEGORIES.items()}
            label = st.selectbox("Issue type", list(labels))
            t_pnr = st.text_input("PNR (optional)", max_chars=12)
            desc = st.text_area("Describe the issue", max_chars=1000)
            send = st.form_submit_button("Create ticket")
        if send:
            if not desc.strip():
                st.error("Please describe the issue before creating a ticket.")
            else:
                try:
                    tid = agent.create_manual_ticket(desc, labels[label], t_pnr)
                    st.success(f"Ticket {tid} created. {config.TICKET_REPLY_PROMISE}")
                except TicketError as exc:
                    st.error(f"Ticket could not be created: {exc}")

        st.markdown("### Open tickets")
        rows = ticket_manager().list_tickets(status="open")
        if rows:
            df = pd.DataFrame(rows)[["ticket_id", "category", "priority", "created_at"]]
            st.dataframe(df.iloc[::-1], hide_index=True, use_container_width=True)
        else:
            st.caption("No open tickets yet.")
        st.caption("Tickets reset when the hosted demo restarts.")

        st.divider()
        c1, c2 = st.columns(2)
        if c1.button("New chat", use_container_width=True):
            for k in ("agent", "agent_error", "messages"):
                st.session_state.pop(k, None)
            st.rerun()
        if c2.button("Lock", use_container_width=True):
            st.session_state.clear()
            st.rerun()


# --- Chat -------------------------------------------------------------------
def chat(agent: Sahil, error: str | None) -> None:
    header()
    if error:
        st.warning(error)
    if "messages" not in st.session_state:
        st.session_state.messages = [{"role": "assistant", "content": config.GREETING}]

    for m in st.session_state.messages:
        with st.chat_message(m["role"], avatar="🧑" if m["role"] == "user" else "🚌"):
            st.markdown(m["content"])
            if m.get("note"):
                st.markdown(f"<span class='bs-small'>{m['note']}</span>", unsafe_allow_html=True)

    pending = None
    if len(st.session_state.messages) <= 1:
        st.markdown("**Popular questions**")
        cols = st.columns(2)
        for i, q in enumerate(config.SUGGESTED_QUESTIONS):
            if cols[i % 2].button(q, key=f"sq{i}", use_container_width=True):
                pending = q
    else:
        with st.expander("Popular questions"):
            for i, q in enumerate(config.SUGGESTED_QUESTIONS):
                if st.button(q, key=f"sqx{i}"):
                    pending = q

    typed = st.chat_input("Type your question for Sahil", max_chars=config.MAX_INPUT_CHARS)
    prompt = typed or pending
    if not prompt:
        return
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.spinner("Sahil is typing..."):
        try:
            res = agent.respond(prompt)
            notes = []
            if res.sources:
                notes.append("Source: BusSafar Help docs, " + "; ".join(res.sources))
            if res.ticket_ids:
                notes.append(f"Ticket created: {', '.join(res.ticket_ids)}")
            note = html.escape(" · ".join(notes)) if notes else None
            st.session_state.messages.append({"role": "assistant", "content": res.text, "note": note})
        except LLMError:
            st.session_state.messages.append(
                {"role": "assistant", "content": "Sorry, I'm having trouble right now. Please try again shortly."}
            )
    st.rerun()


def main() -> None:
    if not passcode_gate():
        return
    agent, error = get_agent()
    sidebar(agent)
    chat(agent, error)


main()
