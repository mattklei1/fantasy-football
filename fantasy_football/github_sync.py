"""Minimal wrapper around GitHub's Contents API - used ONLY by
war_room_data.save_rank_override()/clear_rank_override() to commit an
uploaded weekly rank override directly to this repo, so it durably
survives a Streamlit Cloud disk wipe and is visible to the Wednesday/
Sunday GitHub Actions scripts (a completely separate environment that
only ever sees what's actually in the repo - see rank_overrides.py's
module docstring for the full reasoning). Deliberately narrow: create-
or-update a single file, nothing else - not a general git client.
"""
from __future__ import annotations

import base64

import requests

API_BASE = "https://api.github.com"
TIMEOUT_SECONDS = 20


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _existing_sha(repo: str, token: str, path: str, branch: str) -> str | None:
    resp = requests.get(
        f"{API_BASE}/repos/{repo}/contents/{path}",
        headers=_headers(token),
        params={"ref": branch},
        timeout=TIMEOUT_SECONDS,
    )
    if resp.status_code == 200:
        return resp.json().get("sha")
    return None  # 404 (new file) or any other non-200 - fall through to a plain create attempt


def commit_file(repo: str, token: str, path: str, content_bytes: bytes, message: str, branch: str = "main") -> dict:
    """Creates or updates one file at `path` in `repo` on `branch` via a
    single Contents API PUT. Returns {"success", "status_code", "message",
    "commit_sha"} - never raises on an HTTP-level rejection (bad token,
    branch protection, etc.), only on a genuine network failure, same
    "return a result, don't crash the page" convention as war_room_data's
    submit_waiver_claim()/submit_lineup_changes()."""
    sha = _existing_sha(repo, token, path, branch)
    payload = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("ascii"),
        "branch": branch,
    }
    if sha is not None:
        payload["sha"] = sha

    try:
        resp = requests.put(
            f"{API_BASE}/repos/{repo}/contents/{path}",
            headers=_headers(token),
            json=payload,
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        return {"success": False, "status_code": None, "message": str(exc), "commit_sha": None}

    if resp.status_code in (200, 201):
        body = resp.json()
        return {
            "success": True,
            "status_code": resp.status_code,
            "message": "Committed",
            "commit_sha": (body.get("commit") or {}).get("sha"),
        }
    try:
        detail = resp.json().get("message", resp.text[:300])
    except ValueError:
        detail = resp.text[:300]
    return {"success": False, "status_code": resp.status_code, "message": detail, "commit_sha": None}


def delete_file(repo: str, token: str, path: str, message: str, branch: str = "main") -> dict:
    """Deletes one file - needs its current sha first (the Contents API's
    DELETE requires it). A no-op success if the file doesn't exist on
    this branch (nothing to delete is not a failure here)."""
    sha = _existing_sha(repo, token, path, branch)
    if sha is None:
        return {"success": True, "status_code": None, "message": "Nothing to delete", "commit_sha": None}

    try:
        resp = requests.delete(
            f"{API_BASE}/repos/{repo}/contents/{path}",
            headers=_headers(token),
            json={"message": message, "sha": sha, "branch": branch},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        return {"success": False, "status_code": None, "message": str(exc), "commit_sha": None}

    if resp.status_code == 200:
        body = resp.json()
        return {
            "success": True,
            "status_code": resp.status_code,
            "message": "Deleted",
            "commit_sha": (body.get("commit") or {}).get("sha"),
        }
    try:
        detail = resp.json().get("message", resp.text[:300])
    except ValueError:
        detail = resp.text[:300]
    return {"success": False, "status_code": resp.status_code, "message": detail, "commit_sha": None}
