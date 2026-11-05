"""Streamlit viewer for one bookmark from the signed-in X account."""

from __future__ import annotations

import os
import sys
import urllib.parse
from pathlib import Path

import streamlit as st

_APP_DIR = str(Path(__file__).resolve().parent)
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from linkedin_draft import DraftError, generate_linkedin_draft, resolve_model
from x_bookmarks import (
    XApiError,
    build_authorize_url,
    exchange_code,
    fetch_bookmarks,
    fetch_me,
    new_pkce,
    pop_verifier,
    remember_verifier,
    text_is_only_links,
)

CLIENT_ID_ENV = "X_CLIENT_ID"
REDIRECT_ENV = "X_REDIRECT_URI"

st.set_page_config(page_title="X bookmark viewer", layout="centered")
st.title("X bookmark viewer")
st.write(
    "Sign in with X, then load a page of bookmarks from the account you authorize. "
    "The access token stays in this browser session and is not written to disk. "
    "This page does not publish anything."
)

def _setting(name: str) -> str:
    """Use an existing environment value. Otherwise read .streamlit/secrets.toml."""
    from_env = os.environ.get(name, "").strip()
    if from_env:
        return from_env
    try:
        raw = st.secrets[name]
    except Exception:
        return ""
    if raw is None:
        return ""
    return str(raw).strip()


client_id = _setting(CLIENT_ID_ENV)
redirect_uri = _setting(REDIRECT_ENV)
if not client_id or not redirect_uri:
    st.error(
        f"Set {CLIENT_ID_ENV} and {REDIRECT_ENV} in .streamlit/secrets.toml "
        "or the environment before signing in. A client secret is not required."
    )
    st.stop()

parsed_redirect = urllib.parse.urlparse(redirect_uri)
if parsed_redirect.scheme not in {"http", "https"} or not parsed_redirect.netloc:
    st.error(f"{REDIRECT_ENV} must be an http or https URL.")
    st.stop()


def _param(name: str) -> str:
    value = st.query_params.get(name, "")
    if isinstance(value, list):
        value = value[0] if value else ""
    return value.strip() if isinstance(value, str) else ""


def _sign_out() -> None:
    for key in list(st.session_state.keys()):
        if (
            key.startswith("x_")
            or key.startswith("pasted_source_")
            or key.startswith("source_text_")
            or key.startswith("linkedin_")
            or key == "bookmark_choice"
        ):
            st.session_state.pop(key, None)


code = _param("code")
state = _param("state")
oauth_error = _param("error")

if code and "x_access_token" not in st.session_state:
    verifier = pop_verifier(state) if state else None
    if verifier is None:
        st.session_state["x_sign_in_error"] = "This sign-in attempt expired. Start sign-in again."
    else:
        try:
            st.session_state["x_access_token"] = exchange_code(
                client_id=client_id,
                redirect_uri=redirect_uri,
                code=code,
                code_verifier=verifier,
            )
            st.session_state.pop("x_sign_in_error", None)
            st.session_state.pop("x_bookmarks", None)
            st.session_state.pop("x_bookmarks_loaded", None)
            st.session_state.pop("x_bookmarks_error", None)
        except XApiError as exc:
            st.session_state["x_sign_in_error"] = str(exc)
    st.query_params.clear()
    st.rerun()
elif oauth_error and "x_access_token" not in st.session_state:
    if oauth_error.isascii() and oauth_error.replace("_", "").isalnum() and len(oauth_error) <= 64:
        st.session_state["x_sign_in_error"] = f"X did not complete sign-in ({oauth_error})."
    else:
        st.session_state["x_sign_in_error"] = "X did not complete sign-in."
    st.query_params.clear()
    st.rerun()

sign_in_error = st.session_state.get("x_sign_in_error")
if sign_in_error:
    st.error(sign_in_error)

access_token = st.session_state.get("x_access_token")
if not access_token:
    if st.button("Sign in with X"):
        verifier, challenge, login_state = new_pkce()
        remember_verifier(login_state, verifier)
        st.session_state["x_auth_url"] = build_authorize_url(
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=login_state,
            code_challenge=challenge,
        )
        st.session_state.pop("x_sign_in_error", None)
        st.rerun()
    auth_url = st.session_state.get("x_auth_url")
    if auth_url:
        st.link_button("Continue to X", auth_url)
    else:
        st.info("Sign in, then load bookmarks.")
    st.stop()

if st.button("Sign out"):
    _sign_out()
    st.rerun()

if st.button("Load bookmarks"):
    try:
        user_id = fetch_me(access_token)
        st.session_state["x_bookmarks"] = fetch_bookmarks(access_token, user_id)
        st.session_state["x_bookmarks_error"] = None
    except XApiError as exc:
        st.session_state["x_bookmarks"] = []
        st.session_state["x_bookmarks_error"] = str(exc)
    st.session_state["x_bookmarks_loaded"] = True
    st.session_state.pop("bookmark_choice", None)

if not st.session_state.get("x_bookmarks_loaded"):
    st.info("Choose Load bookmarks to fetch one page from X.")
    st.stop()

bookmark_error = st.session_state.get("x_bookmarks_error")
if bookmark_error:
    st.error(bookmark_error)
    st.stop()

bookmarks = st.session_state.get("x_bookmarks") or []
if not bookmarks:
    st.info("X returned no bookmarks for this account.")
    st.stop()

st.caption(f"{len(bookmarks)} bookmark{'s' if len(bookmarks) != 1 else ''}")
selected_index = st.selectbox(
    "Bookmark",
    range(len(bookmarks)),
    format_func=lambda index: bookmarks[index].label,
    key="bookmark_choice",
)
selected = bookmarks[selected_index]
source_id = selected.tweet_id or str(selected_index)
if st.session_state.get("linkedin_draft_for") != source_id:
    st.session_state.pop("linkedin_draft", None)
    st.session_state.pop("linkedin_draft_error", None)
    st.session_state["linkedin_draft_for"] = source_id

source_key = f"source_text_{source_id}"
needs_paste = not (selected.text and not text_is_only_links(selected.text))
st.subheader("Text")
if needs_paste:
    if selected.text:
        st.text(selected.text)
    st.info(
        "This bookmark has no article text. Paste the source content below. "
        "It will not be filled from the URL."
    )
    source_text = st.text_area(
        "Source text",
        key=source_key,
        placeholder="Paste the article text here",
    )
else:
    if source_key not in st.session_state:
        st.session_state[source_key] = selected.text
    if selected.text:
        st.text(selected.text)
    source_text = st.text_area("Source text", key=source_key)

source_ready = bool(source_text.strip()) and not text_is_only_links(source_text)
if not source_ready:
    st.info(
        "Paste source content before generating a draft. "
        "A link alone is not used, and nothing is filled from the URL."
    )
writing_examples = st.text_area(
    "My writing examples",
    key="writing_examples",
    height=180,
    placeholder="Paste several posts you wrote. Used for tone, paragraph length, and structure only.",
)
st.caption(
    "Examples stay in this browser session across reruns. They are not used as facts."
)
opinion_notes = st.text_area(
    "Opinion notes (optional)",
    key="opinion_notes",
    placeholder="Optional. Leave blank and the draft will not invent your beliefs, experiences, or endorsements.",
)
gemini_key = _setting("GEMINI_API_KEY")
if not gemini_key:
    st.caption(
        "Set GEMINI_API_KEY in the environment or .streamlit/secrets.toml to generate a draft."
    )
if st.button("Generate LinkedIn draft", disabled=not source_ready):
    with st.spinner("Generating LinkedIn draft..."):
        try:
            st.session_state["linkedin_draft"] = generate_linkedin_draft(
                api_key=gemini_key,
                model=resolve_model(_setting("GEMINI_MODEL")),
                source_text=source_text,
                source_url=selected.url,
                writing_examples=writing_examples,
                opinion_notes=opinion_notes,
            )
            st.session_state["linkedin_draft_error"] = None
            st.session_state["linkedin_draft_for"] = source_id
        except DraftError as exc:
            st.session_state["linkedin_draft"] = None
            st.session_state["linkedin_draft_error"] = str(exc)
draft_error = st.session_state.get("linkedin_draft_error")
if draft_error:
    st.error(draft_error)
draft = st.session_state.get("linkedin_draft")
if isinstance(draft, str) and draft.strip():
    st.subheader("LinkedIn draft")
    st.text(draft)
st.subheader("Source URL")
if selected.url:
    st.markdown(f"[{selected.url}]({selected.url})")
else:
    st.info("No source URL is available for this bookmark.")
if selected.tweet_id:
    st.caption(f"Post id {selected.tweet_id}")
