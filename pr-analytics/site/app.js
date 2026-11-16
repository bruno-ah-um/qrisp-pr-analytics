/*
 * Copyright (c) 2026 the Qrisp authors
 *
 * This program and the accompanying materials are made available under the
 * terms of the Eclipse Public License 2.0 which is available at
 * http://www.eclipse.org/legal/epl-2.0.
 *
 * SPDX-License-Identifier: EPL-2.0 OR GPL-2.0 WITH Classpath-exception-2.0
 */
const DATA_BASE = "data/";
// Overridden by the "timezone" field of current.json once it has loaded.
let DISPLAY_TZ = "Europe/Berlin";
let currentData = null;

const byId = (id) => document.getElementById(id);
const fmtDate = (value) => value ? new Intl.DateTimeFormat("en-GB", {
  dateStyle: "medium", timeStyle: "short", timeZone: DISPLAY_TZ
}).format(new Date(value)) : "—";
const duration = (hours) => {
  if (hours === null || hours === undefined || Number.isNaN(Number(hours))) return "—";
  const h = Number(hours);
  if (h < 24) return `${Math.round(h)}h`;
  const days = h / 24;
  return `${days < 10 ? days.toFixed(1) : Math.round(days)}d`;
};
const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

async function getJson(file) {
  const response = await fetch(`${DATA_BASE}${file}?v=${Date.now()}`, {cache:"no-store"});
  if (!response.ok) throw new Error(`Could not load ${file} (${response.status})`);
  return response.json();
}

function renderMeta(data) {
  if (data.timezone) DISPLAY_TZ = data.timezone;
  byId("display-tz").textContent = DISPLAY_TZ;
  if (data.repository) {
    const [owner, repo] = data.repository.split("/");
    byId("repo-link").href = `https://github.com/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}`;
    byId("repo-link").textContent = `${owner} / ${repo}`;
    document.title = `${repo} · PR Review Analytics`;
  }
  // Points at the repository running the workflow, which may differ from the analysed one.
  if (data.workflow_url) {
    byId("workflow-link").href = data.workflow_url;
    byId("workflow-link").classList.remove("hidden");
  }
}

function renderKpis(data) {
  const s = data.summary || {};
  byId("open-prs").textContent = s.open_prs ?? "—";
  byId("awaiting").textContent = s.awaiting_first_review ?? "—";
  byId("pending-requests").textContent = s.pending_review_requests ?? "—";
  byId("median-review").textContent = s.median_hours_to_first_review == null ? "—" : duration(s.median_hours_to_first_review);
  if (s.first_review_window_days != null) {
    byId("median-review-note").textContent = `First reviewed in last ${s.first_review_window_days}d (n=${s.first_review_sample_size ?? 0})`;
  }
  byId("last-updated").textContent = `Updated ${fmtDate(data.generated_at)} (${DISPLAY_TZ})`;
}

function renderReviewerChart(data) {
  const rows = data.summary?.reviewer_backlog || [];
  if (!rows.length) { byId("reviewer-chart").classList.add("hidden"); byId("reviewer-empty").classList.remove("hidden"); return; }
  byId("reviewer-empty").classList.add("hidden");
  if (window.reviewerChart) window.reviewerChart.destroy();
  window.reviewerChart = new Chart(byId("reviewer-chart"), {
    type:"bar",
    data:{labels:rows.map(r=>r.login),datasets:[{label:"Pending PRs",data:rows.map(r=>r.pending_prs),backgroundColor:"#4779c7",borderRadius:5}]},
    options:{indexAxis:"y",responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{beginAtZero:true,ticks:{precision:0}},y:{grid:{display:false}}}}
  });
}

function renderTrend(history) {
  const points = (history || []).slice(-60);
  if (points.length < 2) { byId("trend-chart").classList.add("hidden"); byId("trend-empty").classList.remove("hidden"); return; }
  byId("trend-empty").classList.add("hidden");
  if (window.trendChart) window.trendChart.destroy();
  window.trendChart = new Chart(byId("trend-chart"), {
    type:"line",
    data:{labels:points.map(p=>new Intl.DateTimeFormat("en-GB",{day:"2-digit",month:"short",hour:"2-digit",minute:"2-digit",timeZone:DISPLAY_TZ}).format(new Date(p.generated_at))),datasets:[
      {label:"Open PRs",data:points.map(p=>p.open_prs),borderColor:"#4779c7",backgroundColor:"#4779c722",tension:.25},
      {label:"Awaiting first review",data:points.map(p=>p.awaiting_first_review),borderColor:"#e29a3b",backgroundColor:"#e29a3b22",tension:.25}
    ]},
    options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:"bottom"}},scales:{y:{beginAtZero:true,ticks:{precision:0}}}}
  });
}

function renderTable(prs) {
  const query = byId("search").value.trim().toLowerCase();
  const filtered = prs.filter(p => {
    const text = [p.number,p.title,p.author,...(p.requested_reviewers||[]).map(x=>x.login),...(p.actual_reviewers||[])].join(" ").toLowerCase();
    return text.includes(query);
  });
  if (!filtered.length) {
    byId("pr-rows").innerHTML = `<tr><td colspan="7" class="empty">No matching open pull requests.</td></tr>`;
    return;
  }
  byId("pr-rows").innerHTML = filtered.map(p => {
    // Every requested reviewer is still pending; GitHub drops them once they review.
    // Drafts are not counted as backlog, so their requests get a neutral style.
    const requested = (p.requested_reviewers||[]).map(r =>
      `<span class="pill ${p.draft?"":"warn"}">${escapeHtml(r.kind==="team"?"@"+r.login:r.login)}</span>`
    ).join("") || '<span class="muted">None</span>';
    const actual = (p.actual_reviewers||[]).map(x=>`<span class="pill">${escapeHtml(x)}</span>`).join("") || "—";
    return `<tr>
      <td><a href="${escapeHtml(p.url)}" target="_blank" rel="noopener">#${p.number} · ${escapeHtml(p.title)}</a>${p.draft?' <span class="pill">Draft</span>':''}</td>
      <td>${escapeHtml(p.author)}</td><td>${requested}</td><td>${actual}</td>
      <td>${duration(p.age_hours)}</td><td>${duration(p.hours_to_first_review)}</td><td>${p.review_count ?? 0}</td>
    </tr>`;
  }).join("");
}

async function init() {
  try {
    const [current, history] = await Promise.all([getJson("current.json"), getJson("history.json")]);
    currentData = current;
    renderMeta(current);
    renderKpis(current);
    if (window.Chart) {
      renderReviewerChart(current);
      renderTrend(history);
    } else {
      byId("reviewer-empty").textContent = "Chart library did not load. PR data is still available below.";
      byId("reviewer-empty").classList.remove("hidden");
    }
    renderTable(current.open_prs || []);
    byId("search").addEventListener("input", () => renderTable(currentData.open_prs || []));
  } catch (error) {
    byId("last-updated").textContent = "Dashboard data not available yet";
    byId("pr-rows").innerHTML = `<tr><td colspan="7" class="empty">${escapeHtml(error.message)}. Run the PR analytics workflow from the Actions tab.</td></tr>`;
  }
}
document.addEventListener("DOMContentLoaded", init);
