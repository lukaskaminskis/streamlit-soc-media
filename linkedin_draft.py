"""Turn editable source text into a LinkedIn draft with the Gemini API.

The selected bookmark is the only source of facts. Posts the user pasted are
style samples only. Opinion notes are optional and are never invented.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_CONFIG_PATH = Path(__file__).with_name("gemini_config.toml")

SYSTEM_INSTRUCTION = """
You write a fresh LinkedIn post in the user's style.
Do not closely paraphrase the bookmark. Do not rewrite it sentence by sentence.
Use new sentences and a new arrangement. Keep the facts.

The only source of facts is the selected bookmark in the user message.
That text is the bookmark's own text, or source content the user pasted because the bookmark was only links.
Do not browse, fetch, watch, or invent article or video content from any URL, including the source URL.
Do not invent facts, numbers, names, quotes, or claims that are not in that bookmark.
If the bookmark does not support a claim, leave it out.

My writing examples are for tone, paragraph length, and structure only.
They are not factual sources. Do not copy their facts, names, numbers, stories, or claims into the draft.
If no writing examples were provided, do not imitate an outside post and do not invent a personal voice.

Opinion notes are optional.
When the user message says opinion notes were not provided, do not invent the user's beliefs, experiences, or endorsements.
Do not add a personal take, agreement, memory, or endorsement to fill that gap.
When opinion notes are provided, you may express those notes and only those notes.
Write the post in plain text. Do not describe the page behind the source URL.
""".strip()


class DraftError(Exception):
    """A draft could not be generated. The message includes no secrets."""


def default_model() -> str:
    data = tomllib.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    model = data.get("model", "")
    if not isinstance(model, str) or not model.strip():
        raise DraftError("The Gemini config file is missing a model name.")
    return model.strip()


def resolve_model(override: str) -> str:
    if override.strip():
        return override.strip()
    return default_model()


def generate_linkedin_draft(
    *,
    api_key: str,
    model: str,
    source_text: str,
    source_url: str | None,
    writing_examples: str = "",
    opinion_notes: str | None = None,
    client: object | None = None,
) -> str:
    """Call Gemini once. Pass client only from tests; it is not the API key."""
    if not api_key.strip():
        raise DraftError(
            "Set GEMINI_API_KEY in the environment or .streamlit/secrets.toml."
        )
    cleaned = source_text.strip()
    if not cleaned or _is_only_links(cleaned):
        raise DraftError(
            "Paste source content before generating a draft. A link alone is not used."
        )
    if not model.strip():
        raise DraftError("Set GEMINI_MODEL or the model name in gemini_config.toml.")
    contents = _user_contents(
        cleaned,
        source_url,
        writing_examples=writing_examples,
        opinion_notes=opinion_notes,
    )
    try:
        if client is None:
            from google import genai

            client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model.strip(),
            contents=contents,
            config={"system_instruction": SYSTEM_INSTRUCTION},
        )
    except DraftError:
        raise
    except Exception:
        raise DraftError(
            "Gemini could not generate a draft. Check GEMINI_API_KEY and GEMINI_MODEL, then try again."
        ) from None
    text = getattr(response, "text", None)
    if not isinstance(text, str) or not text.strip():
        raise DraftError("Gemini returned an empty draft.")
    return text.strip()


def _user_contents(
    source_text: str,
    source_url: str | None,
    *,
    writing_examples: str,
    opinion_notes: str | None,
) -> str:
    url_line = source_url.strip() if isinstance(source_url, str) and source_url.strip() else "none"
    examples = writing_examples.strip()
    examples_block = examples if examples else "none provided"
    notes = opinion_notes.strip() if isinstance(opinion_notes, str) else ""
    if notes:
        notes_block = (
            f"{notes}\n"
            "These notes are the user's own views. "
            "Do not invent beliefs, experiences, or endorsements beyond them."
        )
    else:
        notes_block = (
            "not provided\n"
            "Do not invent the user's beliefs, experiences, or endorsements."
        )
    return (
        "My writing examples (style only: tone, paragraph length, and structure; not factual sources):\n"
        f"{examples_block}\n\n"
        "Selected bookmark (the only source of facts):\n"
        f"{source_text}\n\n"
        "Opinion notes (optional):\n"
        f"{notes_block}\n\n"
        "Source URL (citation only, do not fetch it and do not invent what it says):\n"
        f"{url_line}"
    )


def _is_only_links(text: str) -> bool:
    from x_bookmarks import text_is_only_links

    return text_is_only_links(text)
