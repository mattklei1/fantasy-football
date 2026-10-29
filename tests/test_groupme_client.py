"""Unit tests for fantasy_football.groupme_client - no real network calls
(requests.post is monkeypatched)."""
import pytest

from fantasy_football import groupme_client


class _FakeResponse:
    def __init__(self, json_body=None):
        self._json_body = json_body

    def raise_for_status(self):
        pass

    def json(self):
        return self._json_body


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


def test_upload_image_posts_to_the_image_service_with_the_access_token(monkeypatch):
    calls = []

    def fake_post(url, headers, data, timeout):
        calls.append((url, headers, data, timeout))
        return _FakeResponse({"payload": {"url": "https://i.groupme.com/abc123.png"}})

    monkeypatch.setattr(groupme_client.requests, "post", fake_post)
    result = groupme_client.upload_image("my-access-token", b"fake-png-bytes")

    assert result == "https://i.groupme.com/abc123.png"
    assert len(calls) == 1
    url, headers, data, _ = calls[0]
    assert url == groupme_client.IMAGE_UPLOAD_URL
    assert headers["X-Access-Token"] == "my-access-token"
    assert headers["Content-Type"] == "image/png"
    assert data == b"fake-png-bytes"


def test_upload_image_respects_a_custom_content_type(monkeypatch):
    calls = []
    monkeypatch.setattr(
        groupme_client.requests, "post",
        lambda url, headers, data, timeout: calls.append(headers) or _FakeResponse({"payload": {"url": "x"}}),
    )
    groupme_client.upload_image("tok", b"...", content_type="image/jpeg")
    assert calls[0]["Content-Type"] == "image/jpeg"


def test_send_message_with_image_posts_bot_id_text_and_attachment(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return _FakeResponse()

    monkeypatch.setattr(groupme_client.requests, "post", fake_post)
    groupme_client.send_message_with_image("bot123", "check this out", "https://i.groupme.com/xyz.png")

    assert len(calls) == 1
    url, payload, _ = calls[0]
    assert url == groupme_client.POST_URL
    assert payload == {
        "bot_id": "bot123",
        "text": "check this out",
        "attachments": [{"type": "image", "url": "https://i.groupme.com/xyz.png"}],
    }
