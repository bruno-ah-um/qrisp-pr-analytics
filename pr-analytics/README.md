# Qrisp PR Review Analytics

A static GitHub Pages dashboard for `eclipse-qrisp/Qrisp`. It collects public PR/review metadata four times per day, keeps up to 365 days of summary snapshots, and publishes the dashboard with GitHub Pages.

## Install

> **Eclipse projects:** `eclipse-qrisp` is an Eclipse Foundation organization. Repository settings such as GitHub Pages are usually managed centrally by the Eclipse Foundation (through its Otterdog configuration), not through the Settings UI. Agree with the Qrisp maintainers first, then ask them or the Eclipse webmaster to enable Pages with **GitHub Actions** as the source. Also check that `eclipse-qrisp.github.io/Qrisp/` is not already used for another site; deploying would replace it.

Copy `pr-analytics/` and `.github/workflows/pr-analytics-pages.yml` into the target repository, keeping their paths. Do not copy the top-level `README.md`, `LICENSE` or `.gitignore`; the target repository has its own. If its `.gitignore` does not already cover it, add `pr-analytics/site/data/`. Then:

1. Make sure **Settings → Pages → Build and deployment → Source** is set to **GitHub Actions** (or have it set through Otterdog, see above).
2. Open **Actions → PR analytics dashboard → Run workflow** to perform the first collection and deployment.
3. After it succeeds, open **Settings → Pages** to find the published URL.

The workflow does not need write access to repository contents. The default read-only workflow permissions are enough.

**Testing from another repository:** to run the dashboard from a personal repository while analysing Qrisp, set the Actions variable `PR_ANALYTICS_REPOSITORY` to `eclipse-qrisp/Qrisp` (**Settings → Secrets and variables → Actions → Variables**). Without it, the workflow analyses the repository it runs in.

The dashboard is expected at `https://eclipse-qrisp.github.io/Qrisp/`.

## How history is stored

Nothing is committed back to the repository. On each run, the workflow downloads `data/history.json` from the currently deployed Pages site, appends the new snapshot and redeploys. This means:

- The first run (HTTP 404) starts a new, empty history.
- Any other fetch failure, or an invalid `history.json`, fails the run instead of deploying with lost history.
- If the Pages site is deleted or replaced, the accumulated history goes with it. To keep a backup, download `data/history.json` from the live site.

## Schedule

The workflow runs at `00:00, 06:00, 12:00, 18:00 UTC`, plus manual runs and relevant pushes to `main`. GitHub Actions cron schedules are UTC and do not support an IANA timezone such as `Europe/Berlin`; the UI formats timestamps in `Europe/Berlin`. The UTC schedule therefore shifts by one hour relative to Berlin when daylight-saving time changes.

If you prefer different fixed UTC times, edit the cron expression in `.github/workflows/pr-analytics-pages.yml`.

## What is collected

- All currently open PRs.
- Current requested individual reviewers and requested teams.
- Submitted reviews and distinct reviewers.
- Recently closed/merged PRs (last 30 days by default), for the first-review latency metric only.
- PR age and time from PR creation to first submitted review.
- Outstanding review requests (GitHub keeps a request only until the reviewer submits a review).
- Four-times-daily summary history, retained for 365 days on the deployed site.

The collector uses the repository-scoped `GITHUB_TOKEN` supplied by Actions. No personal access token or secret is needed for this public repository.

## Metric caveats

- A reviewer's backlog means open PRs with an outstanding request to that individual. It does not infer informal assignments or work someone has not been explicitly asked to review.
- Team requests are shown on PR rows but are not expanded into individual team members. GitHub does not identify which team member is expected to take a team-level request.
- First-review latency is measured from PR creation to the first submitted review by a human other than the PR author. It is not a pure review-request response-time metric.
- Bot accounts (GitHub user type `Bot` or a login ending in `[bot]`, e.g. CodeRabbit) are ignored everywhere: as reviewers, as first reviews, and as requested reviewers.
- The median first-review time covers every PR (open, merged or closed) whose first human review happened in the last `LATENCY_WINDOW_DAYS` days (default 30). Including closed PRs avoids a bias toward slow PRs, since quickly reviewed PRs get merged and disappear from the open list. PRs that have not had a review yet are not in the sample; the "awaiting first review" KPI tracks those.
- The initial dashboard has no historical backlog before the first collection. Trend charts populate as snapshots accumulate.
- A PR that is open but not reviewable yet (for example, a draft) remains in the open count, but drafts are excluded from the awaiting-first-review KPI and from the reviewer backlog.

## Public data and security

GitHub Pages is public. The JSON files in `pr-analytics/site/data/` are also deployed publicly. This starter publishes public PR metadata only, but verify your organization's policies before enabling it.

The workflow requires:
- `contents: read` to check out the repository.
- `pages: write` and `id-token: write` to deploy GitHub Pages.

It never pushes to any branch.

## Local test

From the repository root:

```bash
PR_ANALYTICS_REPOSITORY=eclipse-qrisp/Qrisp GITHUB_TOKEN=YOUR_TOKEN python pr-analytics/collect.py
```

The token is optional for public data but recommended for higher API rate limits. Do not put a token in source files or the Pages site.

Output is written to `pr-analytics/site/data/`, which is git-ignored. To preview the dashboard, serve the site directory, for example with `python -m http.server -d pr-analytics/site`. To continue from the live history, first download `https://eclipse-qrisp.github.io/Qrisp/data/history.json` into that directory.

## Troubleshooting

- **"Could not fetch …/data/history.json":** the deployed site was unreachable. Re-run the workflow; nothing was deployed, so no history was lost.
- **"Deployed history.json is not a JSON list":** the live history is corrupt. Inspect it and repair it manually, or start over with **Run workflow → Reset history**.
- **Pages deployment fails:** ensure Settings → Pages → Source is set to GitHub Actions.
- **No data yet:** run the workflow manually from Actions and inspect its logs.
- **API rate limit:** keep the Actions-provided `GITHUB_TOKEN`; the collector uses pagination and makes one additional request per open PR and per PR closed within the latency window.
