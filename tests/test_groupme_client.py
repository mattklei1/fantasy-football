"""Unit tests for fantasy_football.groupme_client - no real network calls
(requests.post is monkeypatched)."""
import pytest

from fantasy_football import groupme_client


class _FakeResponse:
    def raise_for_status(self):
        pass


def test_send_message_posts_expected_payload(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return _FakeResponse()

    monkeypatch.setattr(groupme_client.requests, "post", fake_post)
    groupme_client.send_message("bot123", "hello league")

    assert len(calls) == 1
    url, payload, _ = calls[0]
    assert url == groupme_client.POST_URL
    assert payload == {"bot_id": "bot123", "text": "hello league"}


def test_send_long_message_under_limit_sends_once(monkeypatch):
    calls = []
    monkeypatch.setattr(groupme_client.requests, "post", lambda url, json, timeout: calls.append(json) or _FakeResponse())

    groupme_client.send_long_message("bot123", "short message")
    assert len(calls) == 1


def test_send_long_message_splits_on_paragraph_boundaries(monkeypatch):
    calls = []
    monkeypatch.setattr(groupme_client.requests, "post", lambda url, json, timeout: calls.append(json["text"]) or _FakeResponse())

    paragraphs = [f"Paragraph {i}: " + ("x" * 200) for i in range(10)]
    text = "\n\n".join(paragraphs)

    groupme_client.send_long_message("bot123", text, max_length=500)

    assert len(calls) > 1
    for chunk in calls:
        assert len(chunk) <= 500
    # every paragraph's content survives the split, none silently dropped
    rejoined = "\n\n".join(calls)
    for p in paragraphs:
        assert p in rejoined


def test_to_groupme_text_converts_markdown_bold_to_unicode_bold():
    text = "**HEADLINE**\n\nWeek 6 is in the books."
    result = groupme_client.to_groupme_text(text)
    assert "**" not in result  # no literal asterisks leak through
    assert result == "𝐇𝐄𝐀𝐃𝐋𝐈𝐍𝐄\n\nWeek 6 is in the books."


def test_to_groupme_text_leaves_plain_text_unchanged():
    text = "No markdown here at all."
    assert groupme_client.to_groupme_text(text) == text


def test_to_bold_unicode_converts_letters_and_digits():
    assert groupme_client.to_bold_unicode("Week 1") == "𝐖𝐞𝐞𝐤 𝟏"


def test_to_bold_unicode_leaves_punctuation_spaces_and_emoji_unchanged():
    assert groupme_client.to_bold_unicode("hi! 🏈 $5") == "𝐡𝐢! 🏈 $𝟓"


def test_to_bold_unicode_round_trips_back_via_a_second_call_is_a_noop():
    # already-bold text has no plain ASCII left to convert - calling
    # again should be idempotent, not double-convert or error
    once = groupme_client.to_bold_unicode("Week 1")
    assert groupme_client.to_bold_unicode(once) == once


def test_send_long_message_hard_cuts_a_single_oversized_paragraph(monkeypatch):
    calls = []
    monkeypatch.setattr(groupme_client.requests, "post", lambda url, json, timeout: calls.append(json["text"]) or _FakeResponse())

    text = "x" * 1500  # one giant paragraph, no natural break points
    groupme_client.send_long_message("bot123", text, max_length=500)

    assert len(calls) == 3
    assert "".join(calls) == text
