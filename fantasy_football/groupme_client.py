"""Thin wrapper around GroupMe's Bot API for scheduled league posts (slate
updates, waiver recaps - see scripts/post_*.py). A Bot's bot_id can ONLY
post messages to the one group it was created for - it has no read
access, no account access, nothing else it could leak even if it were
ever exposed, unlike the personal access token used earlier in this
project for one-off read-only GroupMe research.
"""
from __future__ import annotations

import re

import requests

POST_URL = "https://api.groupme.com/v3/bots/post"
MAX_MESSAGE_LENGTH = 1000  # GroupMe's documented per-message limit
TIMEOUT_SECONDS = 15

_MARKDOWN_BOLD = re.compile(r"\*\*(.+?)\*\*")


def to_groupme_text(markdown_text: str) -> str:
    """Strips Markdown bold (**word**) down to plain text - GroupMe
    doesn't render Markdown, so unstripped asterisks show up literally.
    Used for content shared with the Streamlit page (which DOES want
    Markdown, e.g. the weekly recap) before it's posted here - see
    scripts/post_weekly_recap.py."""
    return _MARKDOWN_BOLD.sub(r"\1", markdown_text)


def send_message(bot_id: str, text: str) -> None:
    """Posts one message (<=1000 chars). Raises requests.HTTPError on
    failure - callers (scripts/post_*.py) should let this surface loudly
    in the GitHub Actions log rather than silently swallow a failed post."""
    resp = requests.post(
        POST_URL, json={"bot_id": bot_id, "text": text}, timeout=TIMEOUT_SECONDS
    )
    resp.raise_for_status()


def send_long_message(bot_id: str, text: str, max_length: int = MAX_MESSAGE_LENGTH) -> None:
    """Splits on blank-line boundaries (paragraph breaks) to stay under
    GroupMe's per-message length limit, rather than truncating content or
    cutting mid-sentence. Each chunk posts as its own message, in order."""
    if len(text) <= max_length:
        send_message(bot_id, text)
        return

    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) > max_length and current:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        chunks.append(current)

    for chunk in chunks:
        # a single paragraph longer than max_length on its own - hard-cut
        # as a last resort rather than fail the whole post
        for i in range(0, len(chunk), max_length):
            send_message(bot_id, chunk[i : i + max_length])
