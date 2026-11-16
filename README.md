# Qrisp PR Review Analytics

**Live dashboard: <https://bruno-ah-um.github.io/qrisp-pr-analytics/>**

A static GitHub Pages dashboard that tracks the pull-request review workload of [eclipse-qrisp/Qrisp](https://github.com/eclipse-qrisp/Qrisp): open PRs, outstanding review requests per reviewer, time to first review and backlog trends. A GitHub Actions workflow refreshes it four times a day.

This repository is a test deployment. It runs the dashboard against Qrisp through the `PR_ANALYTICS_REPOSITORY` Actions variable, so it can be evaluated before being proposed upstream.

## Contents

| Path | Purpose |
| --- | --- |
| [`pr-analytics/`](pr-analytics/) | Collector (`collect.py`, standard-library Python only) and the static site. |
| [`pr-analytics/README.md`](pr-analytics/README.md) | Full documentation: installation, metric definitions, data storage, troubleshooting. |
| [`.github/workflows/pr-analytics-pages.yml`](.github/workflows/pr-analytics-pages.yml) | Scheduled workflow that collects the data and deploys GitHub Pages. |

## License

[Eclipse Public License 2.0](LICENSE), the same license as Qrisp.
