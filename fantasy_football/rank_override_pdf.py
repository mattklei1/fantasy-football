"""Parses an uploaded weekly-rankings PDF into a (rank, player_name) list
for war_room_data.save_rank_override(). The source varies week to week
(FantasyPros export, Yahoo, an individual analyst's own cheat sheet -
each with a different layout), so this is a deliberately generic,
best-effort parser rather than one tuned to a specific vendor's format -
paired with a mandatory manual review/edit step in the UI
(pages/9_War_Room.py) before anything is applied. A wrong parse here is
just a bad rank suggestion surfaced for the admin to fix or discard, not
a silent data-integrity problem, so it's fine for this to be approximate.

Split into a pure text->rows parser (parse_ranking_lines, unit-testable
with plain strings - no PDF fixtures needed) and a thin pdfplumber-backed
byte-extraction wrapper (extract_text_from_pdf) that just feeds it.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass

# A rank line looks like "12. Justin Jefferson  WR - MIN" or "12) Name" or
# "12 Name  CIN  WR  Bye: 7" - a leading integer (the rank), then a run of
# up to 4 Title-Case-ish word tokens (the name, hyphens/periods/apostrophes
# allowed so "Ja'Marr Chase"/"Amon-Ra St. Brown" match as single tokens).
# Deliberately permissive - see module docstring on why approximate is OK
# here.
_RANK_LINE = re.compile(
    r"^\s*(\d{1,3})[\.\)]?\s+([A-Z][A-Za-z.'\-]*(?:\s+[A-Z][A-Za-z.'\-]*){0,3})"
)

# Trailing tokens this project already treats as team/position codes
# elsewhere (player_matching.py) plus the rest of the 32 NFL teams and
# the standard position codes - trimmed off the end of a parsed name when
# a cheat sheet's "Name  TEAM  POS  Bye: N" layout leaks them into the
# name capture above.
_TEAM_CODES = {
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN",
    "DET", "GB", "HOU", "IND", "JAX", "JAC", "KC", "LAC", "LAR", "LV",
    "MIA", "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SEA", "SF",
    "TB", "TEN", "WSH", "WAS", "FA",
}
_POSITION_CODES = {"QB", "RB", "WR", "TE", "K", "DST", "DEF", "D/ST", "OP", "FLEX"}
_STOP_TOKENS = _TEAM_CODES | _POSITION_CODES | {"BYE"}

MIN_NAME_LENGTH = 3  # shorter than this is a stray initial/junk match, not a real name


@dataclass
class ParsedRankLine:
    rank: int
    player_name: str
    raw_line: str


def _trim_trailing_stop_tokens(name: str) -> str:
    tokens = name.split()
    while len(tokens) > 1 and tokens[-1].upper().rstrip(".,:") in _STOP_TOKENS:
        tokens.pop()
    return " ".join(tokens)


def parse_ranking_lines(text: str) -> list[ParsedRankLine]:
    """Pure text -> parsed (rank, name) list, one candidate row per line
    that matches the "leading number + name-shaped text" pattern. Lines
    that don't match (headers, footers, page numbers, dates) are silently
    skipped - the review UI shows exactly what WAS parsed, so a missed
    player is easy to spot and add by hand."""
    results = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _RANK_LINE.match(line)
        if not match:
            continue
        rank = int(match.group(1))
        name = _trim_trailing_stop_tokens(match.group(2).strip())
        if len(name) < MIN_NAME_LENGTH:
            continue
        results.append(ParsedRankLine(rank=rank, player_name=name, raw_line=line))
    return results


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Real PDF text extraction (pdfplumber), page by page in document
    order. Kept separate from parse_ranking_lines() so the parsing regex
    is unit-testable without needing real PDF fixtures."""
    import pdfplumber

    pages_text = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            pages_text.append(page.extract_text() or "")
    return "\n".join(pages_text)


def parse_pdf_rankings(pdf_bytes: bytes) -> list[ParsedRankLine]:
    """Upload-to-rows entry point the UI calls directly."""
    return parse_ranking_lines(extract_text_from_pdf(pdf_bytes))
