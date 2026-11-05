"""Load one bookmark with OAuth 2.0 PKCE user authorization.

Host, scopes, and paths follow a public mirror of an OpenAPI document titled
"X API v2" version 2.157. docs.x.com, developer.x.com, and
https://api.x.com/2/openapi.json returned 403 when checked, so these
requirements are unverified against X's own docs. See README.md.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

API_ORIGIN = "https://api.x.com"
AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
SCOPES = "bookmark.read tweet.read users.read"
_MAX_BODY_BYTES = 1_000_000
_PENDING_LIMIT = 50
_pending_verifiers: dict[str, str] = {}
_pending_lock = threading.Lock()


class XApiError(Exception):
    """A sign-in or bookmarks request failed. The message includes no token."""


@dataclass(frozen=True)
class Bookmark:
    tweet_id: str | None
    text: str | None
    url: str | None

    @property
    def label(self) -> str:
        if self.tweet_id and (not self.text or text_is_only_links(self.text)):
            return f"Post {self.tweet_id}"
        if self.text:
            snippet = " ".join(self.text.split())
            if len(snippet) > 80:
                return snippet[:77] + "..."
            return snippet
        return "Bookmark"


def new_pkce() -> tuple[str, str, str]:
    """Return a code verifier, its S256 challenge, and an opaque state value."""
    verifier = secrets.token_urlsafe(64)[:128]
    return verifier, code_challenge(verifier), secrets.token_urlsafe(32)


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def remember_verifier(state: str, verifier: str) -> None:
    """Keep the PKCE verifier in process memory until the callback arrives."""
    with _pending_lock:
        if len(_pending_verifiers) >= _PENDING_LIMIT:
            _pending_verifiers.pop(next(iter(_pending_verifiers)))
        _pending_verifiers[state] = verifier


def pop_verifier(state: str) -> str | None:
    with _pending_lock:
        return _pending_verifiers.pop(state, None)


def build_authorize_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
) -> str:
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": SCOPES,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        },
        quote_via=urllib.parse.quote,
    )
    return f"{AUTHORIZE_URL}?{query}"


def exchange_code(
    *,
    client_id: str,
    redirect_uri: str,
    code: str,
    code_verifier: str,
) -> str:
    """Exchange an authorization code for a user access token. No client secret."""
    form = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code": code,
            "code_verifier": code_verifier,
        }
    ).encode("utf-8")
    status, body = _request(
        "POST",
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=form,
    )
    payload = _json_object(status, body)
    token = payload.get("access_token")
    if status != 200 or not isinstance(token, str) or not token:
        raise XApiError(_error_message(status, payload))
    return token


def fetch_me(access_token: str) -> str:
    status, body = _request(
        "GET",
        f"{API_ORIGIN}/2/users/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    payload = _json_object(status, body)
    if status != 200:
        raise XApiError(_error_message(status, payload))
    data = payload.get("data")
    user_id = data.get("id") if isinstance(data, dict) else None
    if not isinstance(user_id, str) or not user_id.isdigit():
        raise XApiError("X did not return a numeric user id.")
    return user_id


# Small page. Official hosts returned 403; field names are from the public mirror.
BOOKMARK_PAGE_SIZE = 10
BOOKMARK_TWEET_FIELDS = "text,note_tweet,entities,article"
BOOKMARK_EXPANSIONS = "article.cover_media,article.media_entities"
_HTTP_URL = re.compile(r"https?://\S+")


def bookmarks_request_url(user_id: str) -> str:
    return (
        f"{API_ORIGIN}/2/users/{user_id}/bookmarks"
        f"?max_results={BOOKMARK_PAGE_SIZE}"
        f"&tweet.fields={BOOKMARK_TWEET_FIELDS}"
        f"&expansions={BOOKMARK_EXPANSIONS}"
    )


def fetch_bookmarks(access_token: str, user_id: str) -> list[Bookmark]:
    if not user_id.isdigit():
        raise XApiError("Refusing to request bookmarks without a numeric user id.")
    status, body = _request(
        "GET",
        bookmarks_request_url(user_id),
        headers={"Authorization": f"Bearer {access_token}"},
    )
    payload = _json_object(status, body)
    if status != 200:
        raise XApiError(_error_message(status, payload))
    data = payload.get("data")
    if data is None:
        return []
    if not isinstance(data, list):
        raise XApiError("X returned an unexpected bookmarks payload.")
    bookmarks: list[Bookmark] = []
    for post in data:
        if isinstance(post, dict):
            bookmarks.append(bookmark_from_post(post))
    return bookmarks


def bookmark_from_post(post: dict) -> Bookmark:
    raw_id = post.get("id")
    tweet_id = raw_id.strip() if isinstance(raw_id, str) else None
    if not tweet_id:
        tweet_id = None
    text = _fullest_text(post)
    url = (
        f"https://x.com/i/status/{tweet_id}"
        if tweet_id and tweet_id.isdigit()
        else None
    )
    return Bookmark(tweet_id=tweet_id, text=text, url=url)


def text_is_only_links(text: str | None) -> bool:
    if not isinstance(text, str) or not text.strip():
        return False
    remainder = _HTTP_URL.sub(" ", text).strip()
    return remainder == ""


def _fullest_text(post: dict) -> str | None:
    entities = _url_entities(post)
    candidates: list[str] = []
    note = post.get("note_tweet")
    if isinstance(note, dict):
        _append_text(candidates, note.get("text"))
    article = post.get("article")
    if isinstance(article, dict):
        # The data dictionary says this object carries the article text.
        # The OpenAPI mirror does not name that property; `text` is unverified.
        _append_text(candidates, article.get("text"))
    _append_text(candidates, post.get("text"))
    expanded = [_expand_urls(item, entities) for item in candidates]
    prose = [item for item in expanded if not text_is_only_links(item)]
    if prose:
        return max(prose, key=len)
    if expanded:
        return expanded[0]
    return None


def _append_text(candidates: list[str], value: object) -> None:
    if isinstance(value, str) and value.strip():
        candidates.append(value.strip())


def _url_entities(post: dict) -> list[dict]:
    sources: list[object] = [post.get("entities")]
    note = post.get("note_tweet")
    if isinstance(note, dict):
        sources.append(note.get("entities"))
    article = post.get("article")
    if isinstance(article, dict):
        sources.append(article.get("entities"))
    found: list[dict] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        urls = source.get("urls")
        if not isinstance(urls, list):
            continue
        for item in urls:
            if isinstance(item, dict):
                found.append(item)
    return found


def _expand_urls(text: str, entities: list[dict]) -> str:
    updated = text
    for entity in entities:
        short = entity.get("url")
        expanded = entity.get("expanded_url")
        if not isinstance(expanded, str) or not expanded.startswith(("http://", "https://")):
            unwound = entity.get("unwound_url")
            expanded = (
                unwound
                if isinstance(unwound, str) and unwound.startswith(("http://", "https://"))
                else None
            )
        if (
            isinstance(short, str)
            and short
            and isinstance(expanded, str)
            and short != expanded
        ):
            updated = updated.replace(short, expanded)
    return updated


def _request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=20) as response:
            return response.status, _read_limited(response)
    except urllib.error.HTTPError as exc:
        return exc.code, _read_limited(exc)


def _read_limited(response: object) -> bytes:
    read = getattr(response, "read")
    body = read(_MAX_BODY_BYTES + 1)
    if len(body) > _MAX_BODY_BYTES:
        raise XApiError("X API response was larger than this viewer will read.")
    return body


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        return None


def _json_object(status: int, body: bytes) -> dict:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise XApiError(f"X API request failed with status {status}.") from exc
    if not isinstance(payload, dict):
        raise XApiError(f"X API request failed with status {status}.")
    return payload


def _error_message(status: int, payload: dict) -> str:
    for key in ("error_description", "detail", "title", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return f"X API request failed with status {status}: {value.strip()[:300]}"
    errors = payload.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        message = errors[0].get("message") or errors[0].get("detail")
        if isinstance(message, str) and message.strip():
            return f"X API request failed with status {status}: {message.strip()[:300]}"
    return f"X API request failed with status {status}."
