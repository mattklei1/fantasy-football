"""Thin wrapper around GroupMe's Bot API for scheduled league posts (slate
updates, waiver recaps - see scripts/post_*.py). A Bot's bot_id can ONLY
post messages to the one group it was created for - it has no read
access, no account access, nothing else it could leak even if it were
ever exposed, unlike the personal access token used earlier in this
project for one-off read-only GroupMe research.

GroupMe has NO real text-formatting support - confirmed against their
own API docs (2026-09-14): a message's "attachments" only support
image/location/split/emoji types, nothing for bold/italic/etc, and
Markdown isn't rendered at all (unstripped **word** shows up as literal
asterisks). The closest real, working substitute is Unicode's
Mathematical Bold letters/digits (U+1D400 range) - these are ORDINARY,
DISTINCT characters, not a formatting instruction, so they render as
genuinely bold-looking text in plain messages on any platform with
normal Unicode font support (same category of trick already used
successfully elsewhere in this project, e.g. team name "Christian²").
"""
from __future__ import annotations

import re

import requests

POST_URL = "https://api.groupme.com/v3/bots/post"
MAX_MESSAGE_LENGTH = 1000  # GroupMe's documented per-message limit
TIMEOUT_SECONDS = 15

_MARKDOWN_BOLD = re.compile(r"\*\*(.+?)\*\*")

#: Unicode Mathematical Bold block offsets - real distinct codepoints
#: for A-Z/a-z/0-9, not a formatting instruction (see module docstring).
_BOLD_UPPER_BASE = 0x1D400  # Mathematical Bold Capital A
_BOLD_LOWER_BASE = 0x1D41A  # Mathematical Bold Small a
_BOLD_DIGIT_BASE = 0x1D7CE  # Mathematical Bold Digit 0


def to_bold_unicode(text: str) -> str:
    """Converts plain ASCII letters/digits in `text` to Unicode
    Mathematical Bold equivalents - everything else (spaces,
    punctuation, emoji, already-non-ASCII text) passes through
    unchanged, since the Mathematical Bold block only defines A-Z/a-z/
    0-9."""
    out = []
    for ch in text:
        if "A" <= ch <= "Z":
            out.append(chr(ord(ch) - ord("A") + _BOLD_UPPER_BASE))
        elif "a" <= ch <= "z":
            out.append(chr(ord(ch) - ord("a") + _BOLD_LOWER_BASE))
        elif "0" <= ch <= "9":
            out.append(chr(ord(ch) - ord("0") + _BOLD_DIGIT_BASE))
        else:
            out.append(ch)
    return "".join(out)


def to_groupme_text(markdown_text: str) -> str:
    """Converts Markdown bold (**word**) into REAL bold-looking text via
    Unicode Mathematical Bold characters (see to_bold_unicode) instead
    of just stripping the asterisks - GroupMe doesn't render Markdown
    itself, but these are ordinary characters so they display as bold
    regardless. Used for content shared with the Streamlit page (which
    DOES want real Markdown, e.g. the weekly recap) before it's posted
    here - see scripts/post_weekly_recap.py - and by every other
    scripts/post_*.py message builder that marks up its own key phrases
    with **word** for this same conversion."""
    return _MARKDOWN_BOLD.sub(lambda m: to_bold_unicode(m.group(1)), markdown_text)


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
    cutting mid-sentence. Each chunk posts as its own message, in order.

    Converts **bold** markup to real Unicode bold (see to_groupme_text)
    BEFORE splitting, so every scripts/post_*.py caller gets real
    formatting for free just by marking up its own message text - no
    need for each script to remember to call to_groupme_text itself -
    and so the length check below reflects the actual text that gets
    sent, not the pre-conversion markdown."""
    text = to_groupme_text(text)
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
