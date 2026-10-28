from __future__ import annotations

import base64
import json
import os
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

OWNER = "Neiro-Max"
REPO = "Neiro-Max_Control"
BASE_BRANCH = "control-v0.8-live"
OUTPUT_BRANCH = "generated-projects"
API = "https://api.github.com"


def _request(path: str, method: str = "GET", payload: dict | None = None):
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is not configured")
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = Request(
        API + path,
        data=body,
        method=method,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "NEIRO-MAX-CONTROL",
        },
    )
    try:
        with urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        if exc.code == 404:
            return None
        raise RuntimeError(f"GitHub API {exc.code}: {raw[:300]}") from exc


def ensure_output_branch() -> str:
    existing = _request(f"/repos/{OWNER}/{REPO}/git/ref/heads/{OUTPUT_BRANCH}")
    if existing:
        return existing["object"]["sha"]
    base = _request(f"/repos/{OWNER}/{REPO}/git/ref/heads/{BASE_BRANCH}")
    if not base:
        raise RuntimeError("Base branch not found")
    created = _request(
        f"/repos/{OWNER}/{REPO}/git/refs",
        method="POST",
        payload={"ref": f"refs/heads/{OUTPUT_BRANCH}", "sha": base["object"]["sha"]},
    )
    return created["object"]["sha"]


def publish_project(project_id: int, artifacts: dict[str, str]) -> dict:
    ensure_output_branch()
    prefix = f"generated/project-{project_id}"
    published = []
    last_commit = None
    for filename, text in artifacts.items():
        path = f"{prefix}/{filename}"
        encoded_path = quote(path, safe="/")
        existing = _request(f"/repos/{OWNER}/{REPO}/contents/{encoded_path}?ref={OUTPUT_BRANCH}")
        payload = {
            "message": f"Publish generated project {project_id}: {filename}",
            "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            "branch": OUTPUT_BRANCH,
        }
        if existing and existing.get("sha"):
            payload["sha"] = existing["sha"]
        result = _request(
            f"/repos/{OWNER}/{REPO}/contents/{encoded_path}",
            method="PUT",
            payload=payload,
        )
        last_commit = (result or {}).get("commit", {}).get("sha")
        published.append(path)
    return {
        "repository": f"{OWNER}/{REPO}",
        "branch": OUTPUT_BRANCH,
        "prefix": prefix,
        "files": published,
        "commit_sha": last_commit,
    }
