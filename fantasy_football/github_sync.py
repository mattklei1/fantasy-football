"""Minimal wrapper around GitHub's Contents/Git-refs APIs - used by:

- war_room_data.save_rank_override()/clear_rank_override() and
  save_protected_players() to commit real admin-uploaded data to this
  repo's default branch, so it durably survives a Streamlit Cloud disk
  wipe and is visible to the Wednesday/Sunday GitHub Actions scripts (a
  completely separate environment that only ever sees what's actually
  in the repo - see rank_overrides.py's module docstring for the full
  reasoning).
- matchup_snapshots.py, which commits to a SEPARATE branch (not `main`)
  it creates on first use via ensure_branch_exists() - see that
  module's docstring for why (short version: Streamlit Cloud redeploys
  on every push to the branch it's watching, and this needs to commit
  every ~10 minutes during live games without restarting the live app
  each time).

Deliberately narrow: create/read/update a single file, create a branch
if missing - not a general git client.
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


def branch_exists(repo: str, token: str, branch: str) -> bool:
    resp = requests.get(
        f"{API_BASE}/repos/{repo}/git/ref/heads/{branch}", headers=_headers(token), timeout=TIMEOUT_SECONDS
    )
    return resp.status_code == 200


def ensure_branch_exists(repo: str, token: str, branch: str, base_branch: str = "main") -> dict:
    """Creates `branch` pointing at `base_branch`'s current HEAD if it
    doesn't already exist - a no-op success if it does. Used by the
    matchup-snapshot collector to create a dedicated data branch
    (matchup-snapshots) that Streamlit Cloud never watches for deploys,
    so committing a snapshot every 10 minutes during live games doesn't
    also restart the live site every 10 minutes (see matchup_snapshots.py
    module docstring)."""
    if branch_exists(repo, token, branch):
        return {"success": True, "message": "Branch already exists"}

    base_resp = requests.get(
        f"{API_BASE}/repos/{repo}/git/ref/heads/{base_branch}", headers=_headers(token), timeout=TIMEOUT_SECONDS
    )
    if base_resp.status_code != 200:
        return {"success": False, "message": f"Couldn't resolve base branch {base_branch}: HTTP {base_resp.status_code}"}
    base_sha = base_resp.json()["object"]["sha"]

    create_resp = requests.post(
        f"{API_BASE}/repos/{repo}/git/refs",
        headers=_headers(token),
        json={"ref": f"refs/heads/{branch}", "sha": base_sha},
        timeout=TIMEOUT_SECONDS,
    )
    if create_resp.status_code == 201:
        return {"success": True, "message": "Branch created"}
    try:
        detail = create_resp.json().get("message", create_resp.text[:300])
    except ValueError:
        detail = create_resp.text[:300]
    return {"success": False, "message": detail}


def get_file_content(repo: str, token: str, path: str, branch: str) -> tuple[bytes, str] | None:
    """(content_bytes, sha) for one file on `branch`, or None if it
    doesn't exist there. Used to read back a growing snapshot manifest
    before appending to it (see matchup_snapshots.append_snapshot()) and
    by the live app to render the win-probability chart from it."""
    resp = requests.get(
        f"{API_BASE}/repos/{repo}/contents/{path}",
        headers=_headers(token),
        params={"ref": branch},
        timeout=TIMEOUT_SECONDS,
    )
    if resp.status_code != 200:
        return None
    body = resp.json()
    return base64.b64decode(body["content"]), body["sha"]


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
