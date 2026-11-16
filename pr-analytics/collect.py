#!/usr/bin/env python3
# ********************************************************************************
# * Copyright (c) 2026 the Qrisp authors
# *
# * This program and the accompanying materials are made available under the
# * terms of the Eclipse Public License 2.0 which is available at
# * http://www.eclipse.org/legal/epl-2.0.
# *
# * This Source Code may also be made available under the following Secondary
# * Licenses when the conditions for such availability set forth in the Eclipse
# * Public License, v. 2.0 are satisfied: GNU General Public License, version 2
# * with the GNU Classpath Exception which is
# * available at https://www.gnu.org/software/classpath/license.html.
# *
# * SPDX-License-Identifier: EPL-2.0 OR GPL-2.0 WITH Classpath-exception-2.0
# ********************************************************************************
"""Collect public GitHub PR/review data and write static dashboard JSON.

Uses only Python's standard library. No third-party dependencies are needed.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "site" / "data"
CURRENT_FILE = DATA_DIR / "current.json"
HISTORY_FILE = DATA_DIR / "history.json"
API = "https://api.github.com"
API_VERSION = "2022-11-28"
DISPLAY_TZ = os.getenv("DASHBOARD_TIMEZONE", "Europe/Berlin")
HISTORY_DAYS = 365
# First-review latency is measured over PRs whose first review happened within
# this window, whether the PR is still open or already closed/merged.
LATENCY_WINDOW_DAYS = int(os.getenv("LATENCY_WINDOW_DAYS", "30"))
# Retries for transient API failures: 2s, 4s, 8s between attempts.
MAX_ATTEMPTS = 4
BACKOFF_SECONDS = 2
MAX_RATE_LIMIT_WAIT_SECONDS = 300


class GitHubAPIError(RuntimeError):
    pass


def github_get(path: str, params: dict | None = None):
    url = f"{API}{path}"
    if params:
        url += "?" + urlencode(params)
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "qrisp-pr-analytics-dashboard",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, headers=headers)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            error = GitHubAPIError(f"GitHub API returned HTTP {exc.code} for {path}: {body[:500]}")
            delay = _retry_delay(exc, attempt)
            if delay is None or attempt == MAX_ATTEMPTS:
                raise error from exc
        except (URLError, OSError, HTTPException) as exc:
            # Connection resets, timeouts, truncated responses: transient.
            error = GitHubAPIError(f"Could not reach GitHub API for {path}: {exc}")
            if attempt == MAX_ATTEMPTS:
                raise error from exc
            delay = BACKOFF_SECONDS * 2 ** (attempt - 1)
        print(f"WARNING: {error} (attempt {attempt}/{MAX_ATTEMPTS}, retrying in {delay:.0f}s)", file=sys.stderr)
        time.sleep(delay)
    raise AssertionError("unreachable")


def _retry_delay(exc: HTTPError, attempt: int) -> float | None:
    """Seconds to wait before retrying an HTTP error, or None if it is not retryable."""
    backoff = BACKOFF_SECONDS * 2 ** (attempt - 1)
    if exc.code >= 500:
        return backoff
    if exc.code not in (403, 429):
        return None  # 404, 401, 422, ...: retrying will not help.
    # Rate limits (primary: remaining=0; secondary: Retry-After or 403/429).
    retry_after = exc.headers.get("Retry-After")
    if retry_after and retry_after.isdigit():
        delay = float(retry_after)
    elif exc.headers.get("X-RateLimit-Remaining") == "0":
        reset = float(exc.headers.get("X-RateLimit-Reset") or 0)
        delay = max(1.0, reset - time.time())
    elif exc.code == 429:
        delay = max(60.0, backoff)
    else:
        return None  # A plain 403 is a permission problem, not a rate limit.
    # Do not let a run sit idle for a long time; the next scheduled run will catch up.
    return delay if delay <= MAX_RATE_LIMIT_WAIT_SECONDS else None


def iter_pages(path: str, params: dict | None = None):
    """Yield items page by page until a short page signals the end."""
    page = 1
    while True:
        query = dict(params or {})
        query.update({"per_page": 100, "page": page})
        batch = github_get(path, query)
        if not isinstance(batch, list):
            raise GitHubAPIError(f"Expected a list from {path}")
        yield from batch
        if len(batch) < 100:
            return
        page += 1
        # Be polite to the API; authenticated Actions requests have ample quota.
        time.sleep(0.05)


def paginated(path: str, params: dict | None = None):
    """Fetch all pages."""
    return list(iter_pages(path, params))


def parse_dt(value: str | None):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def is_bot(user: dict | None) -> bool:
    """True for GitHub Apps and bot accounts (e.g. coderabbitai[bot], Copilot)."""
    user = user or {}
    return user.get("type") == "Bot" or user.get("login", "").endswith("[bot]")


def human_reviews(pr) -> list[tuple[datetime, str, str]]:
    """Submitted reviews by humans other than the PR author, oldest first."""
    author = (pr.get("user") or {}).get("login", "")
    submitted = []
    for review in paginated(f"/repos/{OWNER}/{REPO}/pulls/{pr['number']}/reviews"):
        user = review.get("user") or {}
        login = user.get("login")
        submitted_at = parse_dt(review.get("submitted_at"))
        if not login or not submitted_at or login == author or is_bot(user):
            continue
        submitted.append((submitted_at, login, review.get("state", "COMMENTED")))
    return sorted(submitted)


def latency_sample(pr, submitted, window_start: datetime):
    """Hours from creation to first human review, if that review is inside the window."""
    if not submitted:
        return None
    first_review = submitted[0][0]
    if first_review < window_start:
        return None
    created = parse_dt(pr["created_at"])
    return {
        "number": pr["number"],
        "state": "open" if pr.get("state") == "open" else ("merged" if pr.get("merged_at") else "closed"),
        "first_review_at": iso(first_review),
        "hours_to_first_review": round(max(0, (first_review - created).total_seconds() / 3600), 1),
    }


def recently_closed_samples(window_start: datetime):
    """Latency samples for closed/merged PRs first reviewed inside the window.

    Without these, the metric would only see PRs that are still open, which
    skews it towards slow PRs because quickly reviewed ones get merged and drop out.
    """
    samples = []
    for pr in iter_pages(
        f"/repos/{OWNER}/{REPO}/pulls",
        {"state": "closed", "sort": "updated", "direction": "desc"},
    ):
        # A PR first reviewed inside the window was updated inside it too, and
        # results are sorted by updated_at, so everything after this is older.
        if parse_dt(pr["updated_at"]) < window_start:
            break
        try:
            sample = latency_sample(pr, human_reviews(pr), window_start)
        except GitHubAPIError as exc:
            print(f"WARNING: closed PR #{pr['number']} skipped: {exc}", file=sys.stderr)
            continue
        if sample:
            samples.append(sample)
    return samples


def requested_people(pr):
    """Outstanding review requests, taken from the PR list payload.

    GitHub removes a reviewer from requested_reviewers as soon as they submit
    a review (and adds them back if re-requested), so every entry here is a
    request that is still waiting for a review.
    """
    users = [{"login": u["login"], "kind": "user"} for u in pr.get("requested_reviewers") or [] if not is_bot(u)]
    teams = [{"login": t["slug"], "name": t["name"], "kind": "team"} for t in pr.get("requested_teams") or []]
    return users + teams


def collect_one(pr):
    number = pr["number"]
    submitted = human_reviews(pr)
    author = (pr.get("user") or {}).get("login", "")
    first_review = submitted[0][0] if submitted else None
    reviewer_logins = sorted({item[1] for item in submitted})

    created = parse_dt(pr["created_at"])
    now = datetime.now(timezone.utc)
    age_hours = max(0, (now - created).total_seconds() / 3600)
    first_review_hours = max(0, (first_review - created).total_seconds() / 3600) if first_review else None

    return {
        "number": number,
        "title": pr.get("title", ""),
        "url": pr.get("html_url", ""),
        "state": pr.get("state", ""),
        "draft": bool(pr.get("draft", False)),
        "author": author,
        "created_at": pr["created_at"],
        "updated_at": pr["updated_at"],
        "age_hours": round(age_hours, 1),
        "first_review_at": iso(first_review),
        "hours_to_first_review": round(first_review_hours, 1) if first_review_hours is not None else None,
        "requested_reviewers": requested_people(pr),
        "actual_reviewers": reviewer_logins,
        "review_count": len(submitted),
        "comments": pr.get("comments", 0),
        "labels": [label.get("name") for label in pr.get("labels", [])],
    }


def build_summary(prs, latency_samples, generated_at):
    open_count = len(prs)
    eligible = [p for p in prs if not p["draft"]]
    awaiting_first = [p for p in eligible if p["first_review_at"] is None]
    all_first_times = [s["hours_to_first_review"] for s in latency_samples]
    pending_by_reviewer = {}
    actual_by_reviewer = {}
    for pr in prs:
        # Drafts are not ready for review, so their requests are not backlog yet.
        for reviewer in [] if pr["draft"] else pr["requested_reviewers"]:
            if reviewer["kind"] == "user":
                pending_by_reviewer[reviewer["login"]] = pending_by_reviewer.get(reviewer["login"], 0) + 1
        for login in pr["actual_reviewers"]:
            actual_by_reviewer[login] = actual_by_reviewer.get(login, 0) + 1

    reviewer_rows = []
    for login, count in pending_by_reviewer.items():
        reviewer_rows.append({
            "login": login,
            "pending_prs": count,
            "reviews_on_open_prs": actual_by_reviewer.get(login, 0),
        })
    reviewer_rows.sort(key=lambda row: (-row["pending_prs"], row["login"].lower()))

    return {
        "generated_at": generated_at,
        "open_prs": open_count,
        "awaiting_first_review": len(awaiting_first),
        "draft_prs": sum(1 for p in prs if p["draft"]),
        "requested_reviewer_count": len(pending_by_reviewer),
        "pending_review_requests": sum(p["pending_prs"] for p in reviewer_rows),
        "average_hours_to_first_review": round(sum(all_first_times) / len(all_first_times), 1) if all_first_times else None,
        "median_hours_to_first_review": round(statistics.median(all_first_times), 1) if all_first_times else None,
        "first_review_window_days": LATENCY_WINDOW_DAYS,
        "first_review_sample_size": len(all_first_times),
        "reviewer_backlog": reviewer_rows,
    }


def workflow_url():
    """Actions page of the workflow that produced this snapshot, if run in Actions.

    This is the repository *running* the dashboard, which can differ from the
    analysed repository (PR_ANALYTICS_REPOSITORY).
    """
    server = os.getenv("GITHUB_SERVER_URL", "https://github.com")
    runner_repo = os.getenv("GITHUB_REPOSITORY")
    # e.g. "owner/repo/.github/workflows/pr-analytics-pages.yml@refs/heads/main"
    workflow_ref = os.getenv("GITHUB_WORKFLOW_REF", "")
    if not os.getenv("GITHUB_ACTIONS") or not runner_repo or "/.github/workflows/" not in workflow_ref:
        return None
    workflow_file = workflow_ref.split("@", 1)[0].rsplit("/", 1)[-1]
    return f"{server}/{runner_repo}/actions/workflows/{workflow_file}"


def main():
    global OWNER, REPO
    # PR_ANALYTICS_REPOSITORY selects the repository to analyse. GITHUB_REPOSITORY
    # cannot be used for that in Actions: the runner always sets it to the
    # repository running the workflow and ignores overrides.
    full_name = os.getenv("PR_ANALYTICS_REPOSITORY") or os.getenv("GITHUB_REPOSITORY") or "eclipse-qrisp/Qrisp"
    if "/" not in full_name:
        raise SystemExit("PR_ANALYTICS_REPOSITORY / GITHUB_REPOSITORY must be in owner/repository format.")
    OWNER, REPO = full_name.split("/", 1)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Collecting open pull requests for {OWNER}/{REPO}...")
    pull_requests = paginated(
        f"/repos/{OWNER}/{REPO}/pulls",
        {"state": "open", "sort": "updated", "direction": "desc"},
    )
    # Avoid a single broken PR stopping collection for the rest of the repository.
    details = []
    errors = []
    for pr in pull_requests:
        try:
            details.append(collect_one(pr))
        except Exception as exc:
            errors.append({"number": pr.get("number"), "error": str(exc)[:300]})
            print(f"WARNING: PR #{pr.get('number')} could not be collected: {exc}", file=sys.stderr)

    window_start = datetime.now(timezone.utc) - timedelta(days=LATENCY_WINDOW_DAYS)
    print(f"Collecting first-review latency for the last {LATENCY_WINDOW_DAYS} days...")
    latency_samples = [
        {
            "number": p["number"],
            "state": "open",
            "first_review_at": p["first_review_at"],
            "hours_to_first_review": p["hours_to_first_review"],
        }
        for p in details
        if p["first_review_at"] and parse_dt(p["first_review_at"]) >= window_start
    ]
    latency_samples += recently_closed_samples(window_start)

    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    snapshot = {
        "repository": f"{OWNER}/{REPO}",
        "generated_at": generated_at,
        "timezone": DISPLAY_TZ,
        "workflow_url": workflow_url(),
        "open_prs": details,
        "first_review_samples": latency_samples,
        "collection_errors": errors,
        "summary": build_summary(details, latency_samples, generated_at),
    }
    CURRENT_FILE.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")

    history = []
    if HISTORY_FILE.exists():
        # Fail loudly instead of resetting: silently starting over would wipe
        # the accumulated history on the next deployment.
        try:
            history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise SystemExit(f"Could not read {HISTORY_FILE}: {exc}") from exc
        if not isinstance(history, list):
            raise SystemExit(f"{HISTORY_FILE} must contain a JSON list.")
    history.append(snapshot["summary"])
    cutoff = datetime.now(timezone.utc).timestamp() - HISTORY_DAYS * 86400
    history = [
        item for item in history
        if (parse_dt(item.get("generated_at")) or datetime.now(timezone.utc)).timestamp() >= cutoff
    ]
    HISTORY_FILE.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")

    print(f"Saved {len(details)} open PRs; {len(errors)} collection errors.")
    print(f"Updated {CURRENT_FILE} and {HISTORY_FILE}.")


if __name__ == "__main__":
    main()
