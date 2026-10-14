/* ========================================================================
 * Entravision Proposal Builder — Admin Console controller
 * ======================================================================== */

const adminState = {
  proposals: [],
  // Server-side pagination/filter state for the Proposals tab — the full
  // list no longer lives in the browser (see loadProposals()), so every
  // one of these triggers a real re-fetch, not an in-memory re-filter.
  proposalsPage: 1,
  proposalsPageSize: 25,
  proposalsMineOnly: true,   // per explicit request: land on "my work" first, not the whole company's history
  proposalsSearch: "",
  proposalsTotalCount: 0,
  proposalsTotalPages: 1,
  proposalsLoading: false,
  analytics: null,
  analyticsLoaded: false,
  analyticsWindow: "all",  // "30d" | "90d" | "12m" | "all"
  rates: [],          // the product list: [{id, name, family, ...}]
  productFamilies: [],
  ratesLoaded: false,
  markets: [],
  marketsLoaded: false,
  users: [],
  usersLoaded: false,
  disclaimers: [],
  disclaimerColors: {},
  disclaimerSwatches: {},
  disclaimersLoaded: false,
  disclaimerEditingId: null,
  restrictions: null,
  forecast: null,     // Admin -> Forecast: {products, coverage, platforms, families, summary} from /api/admin/forecast-map
  forecastLoaded: false,
  faq: [],            // the Process FAQs rows: [{id, section, services, question, link_title, link_url}]
  faqLoaded: false,
  faqEditingId: null,
  faqFormToken: null,
  currentUserEmail: null,  // set by loadUsers()'s /api/me call — lets the Users tab hide "disable/delete self" actions
};

document.addEventListener("DOMContentLoaded", () => {
  wireTabs();
  wireSearch();
  wireProposalsControls();
  wireAnalyticsControls();
  wireProductForm();
  wireBulkUpload();
  wireMarketConfig();
  wireAddUser();
  wireDisclaimers();
  wireRestrictions();
  wireForecast();
  wireFaq();
  wireCsvExports();
  loadProposals();
});

// --------------------------------------------------------------------------
// Tabs
// --------------------------------------------------------------------------

function wireTabs() {
  document.querySelectorAll(".admin-tab").forEach(btn => {
    btn.addEventListener("click", () => {
      const tab = btn.dataset.tab;
      document.querySelectorAll(".admin-tab").forEach(b => b.classList.toggle("active", b === btn));
      document.getElementById("tab-proposals").classList.toggle("hidden", tab !== "proposals");
      document.getElementById("tab-analytics").classList.toggle("hidden", tab !== "analytics");
      document.getElementById("tab-rates").classList.toggle("hidden", tab !== "rates");
      document.getElementById("tab-forecast").classList.toggle("hidden", tab !== "forecast");
      document.getElementById("tab-markets").classList.toggle("hidden", tab !== "markets");
      document.getElementById("tab-users").classList.toggle("hidden", tab !== "users");
      document.getElementById("tab-disclaimers").classList.toggle("hidden", tab !== "disclaimers");
      document.getElementById("tab-restrictions").classList.toggle("hidden", tab !== "restrictions");
      document.getElementById("tab-faq").classList.toggle("hidden", tab !== "faq");
      if (tab === "analytics" && !adminState.analyticsLoaded) loadAnalytics();
      if (tab === "rates" && !adminState.ratesLoaded) loadRates();
      if (tab === "forecast" && !adminState.forecastLoaded) loadForecast();
      if (tab === "markets" && !adminState.marketsLoaded) loadMarketConfig();
      if (tab === "users" && !adminState.usersLoaded) loadUsers();
      if (tab === "disclaimers" && !adminState.disclaimersLoaded) loadDisclaimers();
      if (tab === "restrictions" && !adminState.restrictions) loadRestrictions();
      if (tab === "faq" && !adminState.faqLoaded) loadFaq();
    });
  });
}

// --------------------------------------------------------------------------
// Analytics dashboard
// --------------------------------------------------------------------------

function wireAnalyticsControls() {
  document.querySelectorAll("#analytics-window-tabs .tier-tab").forEach(btn => {
    btn.addEventListener("click", () => {
      if (adminState.analyticsWindow === btn.dataset.window) return;
      adminState.analyticsWindow = btn.dataset.window;
      document.querySelectorAll("#analytics-window-tabs .tier-tab").forEach(b => b.classList.toggle("active", b === btn));
      loadAnalytics();
    });
  });
}

async function loadAnalytics() {
  const loadingEl = document.getElementById("analytics-loading");
  const contentEl = document.getElementById("analytics-content");
  loadingEl.classList.remove("hidden");
  contentEl.classList.add("analytics-loading-dim");
  try {
    const res = await fetch(`/api/admin/analytics?window=${encodeURIComponent(adminState.analyticsWindow)}`);
    if (!res.ok) throw new Error(res.statusText);
    adminState.analytics = await res.json();
    adminState.analyticsLoaded = true;
    renderAnalytics(adminState.analytics);
  } catch (e) {
    contentEl.innerHTML = `<p class="admin-empty">Failed to load analytics: ${escapeHtml(e.message)}</p>`;
  } finally {
    loadingEl.classList.add("hidden");
    contentEl.classList.remove("analytics-loading-dim");
  }
}

function _analyticsStatCard(label, value, hint) {
  return `
    <div class="analytics-stat-card">
      <div class="analytics-stat-value">${escapeHtml(String(value))}</div>
      <div class="analytics-stat-label">${escapeHtml(label)}</div>
      ${hint ? `<div class="analytics-stat-hint">${escapeHtml(hint)}</div>` : ""}
    </div>`;
}

// Simple dependency-free horizontal bar list — a <div> per row, width set
// to that row's % of the largest value in the set. No charting library;
// this app has stayed zero-frontend-dependency throughout, no reason to
// add one just for a handful of admin-only bar charts.
function _analyticsBarList(rows, labelKey, countKey, formatLabel) {
  if (!rows.length) return `<p class="admin-empty">No data yet for this window.</p>`;
  const max = Math.max(...rows.map(r => r[countKey]), 1);
  return rows.map(r => {
    const pct = Math.round((r[countKey] / max) * 100);
    const label = formatLabel ? formatLabel(r[labelKey]) : r[labelKey];
    return `
      <div class="analytics-bar-row">
        <div class="analytics-bar-label">${escapeHtml(label)}</div>
        <div class="analytics-bar-track"><div class="analytics-bar-fill" style="width:${pct}%"></div></div>
        <div class="analytics-bar-count">${r[countKey]}</div>
      </div>`;
  }).join("");
}

// The billing-period values the wizard stores in reopen_state.time_unit. "full_flight" is two words — a plain
// capitalize would print "Full_flight" — so it is spelled out; every other value keeps the label it always had
// (capitalized: Week / Month / Quarter), with underscores read as spaces for anything unforeseen.
const _TIME_UNIT_LABELS = { full_flight: "Full Flight" };
function _formatTimeUnitLabel(u) {
  const key = String(u == null ? "" : u);
  if (_TIME_UNIT_LABELS[key]) return _TIME_UNIT_LABELS[key];
  const words = key.replace(/_/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : "Unknown";
}

const _ANALYTICS_MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function _formatMonthBucket(ym) {
  const [y, m] = ym.split("-");
  return `${_ANALYTICS_MONTH_NAMES[parseInt(m, 10) - 1]} ${y}`;
}

function renderAnalytics(data) {
  const statCards = [
    _analyticsStatCard("Total Proposals", data.total_proposals),
    _analyticsStatCard("Unique Requests", data.unique_requests, "distinct Notion IDs (+ proposals with none)"),
    _analyticsStatCard("Reworked Proposals", data.duplicate_count, "extra generations of an existing Notion ID"),
    _analyticsStatCard("Avg. Proposal Value", money(data.avg_total_net)),
    _analyticsStatCard("Total Pipeline Value", money(data.total_net_sum)),
    _analyticsStatCard("Avg. Flight Length", data.avg_months != null ? `${data.avg_months} mo` : "—"),
    _analyticsStatCard("Multi-Option Proposals", `${data.multi_tier_pct}%`, "offered more than one budget option"),
  ];
  document.getElementById("analytics-stat-grid").innerHTML = statCards.join("");

  const plannerBody = document.querySelector("#analytics-planner-table tbody");
  plannerBody.innerHTML = data.by_planner.length
    ? data.by_planner.map(p => `
        <tr>
          <td class="mono">${escapeHtml(p.email)}</td>
          <td>${p.count}</td>
          <td>${money(p.total_net)}</td>
          <td>${money(p.avg_net)}</td>
        </tr>`).join("")
    : `<tr><td colspan="4" class="admin-empty">No proposals yet for this window.</td></tr>`;

  const regenBody = document.querySelector("#analytics-regen-table tbody");
  regenBody.innerHTML = data.most_regenerated.length
    ? data.most_regenerated.map(r => `
        <tr>
          <td class="mono">${escapeHtml(r.notion_id)}</td>
          <td>${escapeHtml(r.client_name || "—")}</td>
          <td>${r.count}</td>
          <td class="mono">${formatDate(r.latest_generated_at)}</td>
        </tr>`).join("")
    : `<tr><td colspan="4" class="admin-empty">No Notion ID has been generated more than once in this window.</td></tr>`;

  document.getElementById("analytics-month-bars").innerHTML =
    _analyticsBarList(data.by_month, "month", "count", _formatMonthBucket);
  document.getElementById("analytics-request-type-bars").innerHTML =
    _analyticsBarList(data.by_request_type, "request_type", "count");
  document.getElementById("analytics-time-unit-bars").innerHTML =
    _analyticsBarList(data.by_time_unit, "time_unit", "count", _formatTimeUnitLabel);
}

// Debounces a function — waits `ms` after the LAST call before actually
// running, cancelling any pending run each time it's called again. Used
// for the proposals search box now that a keystroke means a real network
// request (server-side search) instead of an instant in-memory filter —
// without this, typing a 6-character search would fire 6 separate
// requests, working against the exact "don't hammer the DB" goal this
// whole pagination change exists for.
function _debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

function wireSearch() {
  document.getElementById("proposals-search").addEventListener(
    "input",
    _debounce(e => {
      adminState.proposalsSearch = e.target.value;
      adminState.proposalsPage = 1;  // a new search always restarts at page 1
      loadProposals();
    }, 350)
  );
  document.getElementById("rates-search").addEventListener("input", e => {
    renderRates(filterRates(e.target.value));
  });
}

function wireProposalsControls() {
  document.querySelectorAll("#proposals-scope-tabs .tier-tab").forEach(btn => {
    btn.addEventListener("click", () => {
      const mineOnly = btn.dataset.scope === "mine";
      if (adminState.proposalsMineOnly === mineOnly) return;
      adminState.proposalsMineOnly = mineOnly;
      adminState.proposalsPage = 1;
      loadProposals();
    });
  });
  document.getElementById("proposals-prev-btn").addEventListener("click", () => {
    if (adminState.proposalsPage <= 1) return;
    adminState.proposalsPage -= 1;
    loadProposals();
  });
  document.getElementById("proposals-next-btn").addEventListener("click", () => {
    if (adminState.proposalsPage >= adminState.proposalsTotalPages) return;
    adminState.proposalsPage += 1;
    loadProposals();
  });
}

// --------------------------------------------------------------------------
// CSV exports (Rates, Markets, Users) — all three used to be (or, for
// Markets/Users, would otherwise have become) a plain `<a href=... download>`
// link, which gives the browser no hook to show any custom UI while the
// file is actually being generated/downloaded — clicking one just sits
// there with zero feedback until the browser's own download UI appears.
// Fetched via JS instead so a spinner can show for exactly as long as the
// request actually takes, then handed to the browser as a real download
// via a throwaway Blob URL.
// --------------------------------------------------------------------------

function wireCsvExports() {
  document.querySelectorAll("[data-csv-export]").forEach(btn => {
    btn.addEventListener("click", () => downloadCsv(btn.dataset.csvExport, btn));
  });
}

async function downloadCsv(url, btn) {
  const originalHtml = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span class="btn-inline-spinner"></span>Exporting…';
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error((await res.text().catch(() => "")) || res.statusText);
    const blob = await res.blob();
    // Filename from the server's Content-Disposition header when present
    // (all three export endpoints set one) — falls back to a generic name
    // rather than failing the download outright if that's ever missing.
    const disposition = res.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="?([^"]+)"?/);
    const filename = match ? match[1] : "export.csv";
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = blobUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(blobUrl);
  } catch (err) {
    alert("Export failed: " + err.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalHtml;
  }
}

// --------------------------------------------------------------------------
// Bulk upload — add/update many products at once from a CSV (same columns
// the Export CSV button produces, so export -> edit in Excel -> re-upload
// is a real workflow, not just a one-way dump).
// --------------------------------------------------------------------------

function wireBulkUpload() {
  document.getElementById("bulk-upload-input").addEventListener("change", async e => {
    const file = e.target.files[0];
    if (!file) return;
    const resultEl = document.getElementById("bulk-upload-result");
    resultEl.classList.remove("hidden");
    resultEl.innerHTML = "Uploading…";

    const formData = new FormData();
    formData.append("file", file);
    try {
      const res = await fetch("/api/admin/products/bulk-upsert", { method: "POST", body: formData });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || res.statusText);
      renderBulkUploadResult(body);
      await loadRates();
    } catch (err) {
      resultEl.innerHTML = `<span class="bur-error">Bulk upload failed: ${escapeHtml(err.message)}</span>`;
    } finally {
      e.target.value = "";  // allow re-selecting the same file name again later
    }
  });
}

function renderBulkUploadResult(body) {
  const resultEl = document.getElementById("bulk-upload-result");
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const parts = [];
  parts.push(`<strong>Processed ${plural(body.total_rows_processed, "row")}.</strong>`);
  const unchanged = (body.unchanged || []).length;
  parts.push(`${body.created.length} added, ${body.updated.length} updated${unchanged ? `, ${unchanged} unchanged` : ""}.`);
  if (body.errors.length) {
    parts.push(`<div class="bur-error">${plural(body.errors.length, "row")} skipped:</div>`);
    parts.push(`<ul>${body.errors.map(e => `<li class="bur-error">${escapeHtml(e)}</li>`).join("")}</ul>`);
  }
  resultEl.innerHTML = parts.join(" ");
}

// --------------------------------------------------------------------------
// Proposal history
// --------------------------------------------------------------------------

async function loadProposals() {
  // Guards the loading-indicator/table against an OLDER, slower request
  // finishing after a NEWER one (e.g. flipping Mine->All then immediately
  // typing a search) — only the request that's still current when it
  // resolves gets to touch the DOM.
  const requestToken = Symbol();
  adminState._proposalsRequestToken = requestToken;
  adminState.proposalsLoading = true;
  renderProposalsLoading();

  const params = new URLSearchParams({
    page: String(adminState.proposalsPage),
    page_size: String(adminState.proposalsPageSize),
    mine: String(adminState.proposalsMineOnly),
    search: adminState.proposalsSearch,
  });
  try {
    const res = await fetch(`/api/admin/proposals?${params}`);
    if (adminState._proposalsRequestToken !== requestToken) return;  // superseded
    const data = await res.json();
    if (!res.ok) throw new Error(detailText(data.detail, res.statusText));
    adminState.proposals = data.proposals || [];
    adminState.proposalsTotalCount = data.total_count || 0;
    adminState.proposalsTotalPages = data.total_pages || 1;
    // The server clamps page to what it actually has available (e.g. a
    // filter change shrinks total_pages below the page we asked for) —
    // mirror that back so Prev/Next and the "Page X of Y" text agree
    // with what's really on screen.
    adminState.proposalsPage = data.page || adminState.proposalsPage;
    renderProposals(adminState.proposals);
  } catch (e) {
    if (adminState._proposalsRequestToken !== requestToken) return;
    document.getElementById("proposals-body").innerHTML =
      `<tr><td colspan="11" class="admin-empty">Failed to load: ${escapeHtml(e.message)}</td></tr>`;
  } finally {
    if (adminState._proposalsRequestToken === requestToken) {
      adminState.proposalsLoading = false;
      renderProposalsPagination();
    }
  }
}

// Shown WHILE a (re-)fetch is in flight — the search box, the Mine/All
// toggle, and Prev/Next all now trigger a real network request instead
// of an instant client-side filter, so each of those deserves visible
// feedback instead of the table just sitting there looking unresponsive
// for however long that request takes.
function renderProposalsLoading() {
  document.getElementById("proposals-body").innerHTML =
    `<tr><td colspan="11" class="admin-empty"><span class="btn-inline-spinner"></span>Loading…</td></tr>`;
  document.getElementById("proposals-prev-btn").disabled = true;
  document.getElementById("proposals-next-btn").disabled = true;
}

function renderProposalsPagination() {
  const { proposalsPage, proposalsTotalPages, proposalsTotalCount, proposalsMineOnly } = adminState;
  document.getElementById("proposals-page-info").textContent =
    `Page ${proposalsPage} of ${proposalsTotalPages}`;
  document.getElementById("proposals-prev-btn").disabled = proposalsPage <= 1;
  document.getElementById("proposals-next-btn").disabled = proposalsPage >= proposalsTotalPages;
  document.getElementById("proposals-count").textContent =
    `${proposalsTotalCount} proposal${proposalsTotalCount === 1 ? "" : "s"}`;
  document.querySelectorAll("#proposals-scope-tabs .tier-tab").forEach(btn => {
    btn.classList.toggle("active", (btn.dataset.scope === "mine") === proposalsMineOnly);
  });
}

function renderProposals(list) {
  const body = document.getElementById("proposals-body");
  renderProposalsPagination();

  if (!list.length) {
    body.innerHTML = `<tr><td colspan="11" class="admin-empty">No proposals match.</td></tr>`;
    return;
  }

  body.innerHTML = list.map(p => {
    const isDraft = p.status === "draft";
    return `
    <tr>
      <td class="mono">${formatDate(p.generated_at || p.updated_at)}</td>
      <td class="mono">${escapeHtml(p.notion_id || "—")}</td>
      <td>${escapeHtml(p.client_name || "—")}</td>
      <td>${escapeHtml(p.requested_by || p.seller_email || "—")}</td>
      <td class="wrap">${escapeHtml(p.proposal_title || p.filename || "—")}</td>
      <td>${isDraft ? '<span class="proposal-status-badge draft">Draft</span>' : ""}</td>
      <td class="mono">${escapeHtml((p.tabs_built || []).join(", "))}</td>
      <td class="mono">${isDraft ? "—" : money(p.total_net)}</td>
      <td class="mono muted">${escapeHtml(p.requester_ip || "—")}</td>
      <td class="wrap muted" title="${escapeAttr(p.requester_user_agent || "")}">${escapeHtml(shortenUA(p.requester_user_agent))}</td>
      <td><a class="reopen-link" href="/?reopen=${encodeURIComponent(p.proposal_id)}" target="_blank" rel="noopener">${isDraft ? "Continue ↗" : "Reopen ↗"}</a></td>
    </tr>
  `;
  }).join("");
}

function shortenUA(ua) {
  if (!ua) return "—";
  // Pull out the most identifying browser/OS token for a compact display.
  const m = ua.match(/(Windows NT [\d.]+|Mac OS X [\d_.]+|Android [\d.]+|iPhone OS [\d_.]+|Linux)/);
  const browserM = ua.match(/(Chrome|Firefox|Safari|Edg|OPR)\/[\d.]+/);
  const os = m ? m[1].replace(/_/g, ".") : "";
  const browser = browserM ? browserM[0] : "";
  return [os, browser].filter(Boolean).join(" · ") || ua.slice(0, 40);
}

// --------------------------------------------------------------------------
// Products — the one product list. Every row is equal and fully editable;
// there is no "original" list to revert to.
// --------------------------------------------------------------------------

async function loadRates() {
  try {
    const res = await fetch("/api/admin/products");
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(detailText(data.detail, res.statusText));
    adminState.rates = data.products || [];
    adminState.productFamilies = data.families || [];
    adminState.ratesLoaded = true;
    document.getElementById("pf-families").innerHTML =
      adminState.productFamilies.map(f => `<option value="${escapeAttr(f)}"></option>`).join("");
    renderRates(filterRates(document.getElementById("rates-search").value));
  } catch (e) {
    document.getElementById("rates-body").innerHTML =
      `<tr><td colspan="7" class="admin-empty">Failed to load: ${escapeHtml(e.message)}</td></tr>`;
  }
}

function filterRates(query) {
  const q = (query || "").trim().toLowerCase();
  if (!q) return adminState.rates;
  return adminState.rates.filter(p => p.name.toLowerCase().includes(q) || p.family.toLowerCase().includes(q));
}

function renderRates(list) {
  const body = document.getElementById("rates-body");
  document.getElementById("rates-count").textContent = `${list.length} product${list.length === 1 ? "" : "s"}`;

  if (!list.length) {
    body.innerHTML = `<tr><td colspan="7" class="admin-empty">No products match.</td></tr>`;
    return;
  }

  body.innerHTML = list.map(p => `
    <tr data-product="${p.id}">
      <td class="mono">${escapeHtml(p.family)}</td>
      <td class="wrap">
        ${escapeHtml(p.name)}
        ${p.is_addon ? '<span class="addon-badge">Add-on</span>' : ""}
      </td>
      <td class="mono">${escapeHtml(p.buying_model)}</td>
      <td>${numInput(p, "base_rate")}</td>
      <td>${numInput(p, "minimum_spend")}</td>
      <td>${numInput(p, "estimated_cpm_for_imps")}</td>
      <td>
        <button class="btn-save-row" data-action="save" data-product="${p.id}">Save</button>
        <button class="btn-secondary" data-action="edit" data-product="${p.id}">✎ Edit</button>
        <button class="btn-secondary" data-action="duplicate" data-product="${p.id}" title="Start a new product from a copy of this one">⧉ Duplicate</button>
        <button class="btn-delete-row" data-action="delete" data-product="${p.id}">Delete</button>
      </td>
    </tr>
  `).join("");

  // Numbers typed into a row but not saved yet survive a re-render (a filter keystroke, another row's save…).
  const dirty = adminState.dirtyRates || (adminState.dirtyRates = {});
  body.querySelectorAll("tr[data-product]").forEach(row => {
    const id = row.dataset.product;
    row.querySelectorAll("input[data-field]").forEach(inp => {
      if (dirty[id] && Object.prototype.hasOwnProperty.call(dirty[id], inp.dataset.field)) inp.value = dirty[id][inp.dataset.field];
      inp.addEventListener("input", () => {
        const saved = inp.getAttribute("value") || "";
        dirty[id] = dirty[id] || {};
        if (inp.value === saved) delete dirty[id][inp.dataset.field]; else dirty[id][inp.dataset.field] = inp.value;
        if (!Object.keys(dirty[id]).length) delete dirty[id];
      });
    });
  });
  body.querySelectorAll('[data-action="save"]').forEach(btn => {
    btn.addEventListener("click", () => onSaveRate(parseInt(btn.dataset.product)));
  });
  body.querySelectorAll('[data-action="edit"]').forEach(btn => {
    btn.addEventListener("click", () => openProductForm("edit", productById(btn.dataset.product)));
  });
  body.querySelectorAll('[data-action="duplicate"]').forEach(btn => {
    btn.addEventListener("click", () => openProductForm("copy", productById(btn.dataset.product)));
  });
  body.querySelectorAll('[data-action="delete"]').forEach(btn => {
    btn.addEventListener("click", () => onDeleteProduct(parseInt(btn.dataset.product)));
  });
}

function productById(id) {
  return adminState.rates.find(p => String(p.id) === String(id));
}

function numInput(product, field) {
  const value = product[field];
  return `<input type="number" step="any" min="0"
            value="${value === null || value === undefined ? "" : value}"
            placeholder="NA"
            data-field="${field}" />`;
}

// The quick numbers right in the table row: rate, minimum spend, est. CPM.
async function onSaveRate(productId) {
  const row = document.querySelector(`tr[data-product="${productId}"]`);
  if (!row) return;
  const btn = row.querySelector('[data-action="save"]');
  btn.disabled = true;
  btn.textContent = "Saving…";

  const payload = {};
  let invalid = null;
  row.querySelectorAll("input[data-field]").forEach(inp => {
    const v = inp.value.trim();
    if (v === "" && inp.dataset.field === "minimum_spend") return;        // a blank minimum means "leave it"
    const n = v === "" ? null : Number(v);
    if (n !== null && (!Number.isFinite(n) || n < 0)) invalid = inp.dataset.field.replace(/_/g, " ");
    payload[inp.dataset.field] = n;
  });
  if (invalid) {
    alert(`Save failed: ${invalid} must be a number of 0 or more.`);
    btn.disabled = false;
    btn.textContent = "Save";
    return;
  }

  try {
    const res = await fetch(`/api/admin/products/${productId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(detailText(body.detail, res.statusText));
    btn.textContent = "Saved ✓";
    if (adminState.dirtyRates) delete adminState.dirtyRates[String(productId)];
    await loadRates();
  } catch (e) {
    alert("Save failed: " + e.message);
    btn.disabled = false;
    btn.textContent = "Save";
  }
}

async function onDeleteProduct(productId) {
  const p = productById(productId);
  if (!p) return;
  if (!confirm(`Delete "${p.name}"? It disappears from every list. Proposals that already use it keep working.`)) return;
  try {
    const res = await fetch(`/api/admin/products/${productId}`, { method: "DELETE" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(detailText(body.detail, res.statusText));
    }
    if (adminState.dirtyRates) delete adminState.dirtyRates[String(productId)];
    await loadRates();
  } catch (e) {
    alert("Delete failed: " + e.message);
  }
}

// --------------------------------------------------------------------------
// One form for adding, editing and duplicating
// --------------------------------------------------------------------------

const PRODUCT_FORM_FIELDS = [
  // [element id, product key, kind]
  ["pf-family", "family", "text"], ["pf-name", "name", "text"], ["pf-short-label", "short_label", "text"],
  ["pf-buying-model", "buying_model", "text"], ["pf-base-rate", "base_rate", "number"],
  ["pf-min-spend", "minimum_spend", "number"], ["pf-est-cpm", "estimated_cpm_for_imps", "number"],
  ["pf-sizes", "sizes", "text"], ["pf-tech-platform", "tech_platform", "text"],
  ["pf-description", "proposal_description", "text"], ["pf-notes", "notes", "text"],
  ["pf-is-addon", "is_addon", "bool"],
  ["pf-flight-min", "minimum_flight_days_min", "number"], ["pf-flight-max", "minimum_flight_days_max", "number"],
  ["pf-wide-orbit", "wide_orbit_code", "text"],
  ["pf-margin-upper", "margin_upper", "number"], ["pf-margin-lower", "margin_lower", "number"],
  ["pf-sla-data", "sla_data_days", "number"], ["pf-sla-creative", "sla_creative_days", "number"],
  ["pf-sla-activate", "sla_activate_days", "number"], ["pf-sla-total", "sla_total_days", "number"],
  ["pf-estimated-impressions", "estimated_impressions", "bool"],
  ["pf-discloses-impressions", "discloses_impressions", "bool"],
  ["pf-national-supported", "national_supported", "bool"],
];

function wireProductForm() {
  const form = document.getElementById("product-form");
  if (!form) return;
  form.addEventListener("submit", onSubmitProduct);
  // A field the browser refuses (a negative, a margin over 1…) may sit inside the collapsed Advanced section —
  // open it, or the message would point at something the planner can't see.
  form.addEventListener("invalid", (ev) => {
    const adv = ev.target.closest && ev.target.closest(".pf-advanced");
    if (adv) adv.open = true;
  }, true);
  document.getElementById("product-cancel-btn").addEventListener("click", closeProductForm);
  document.getElementById("add-product-btn").addEventListener("click", () => openProductForm("add"));
}

function closeProductForm() {
  adminState.productFormToken = Symbol();                                   // a response still in flight is stale now
  const form = document.getElementById("product-form");
  form.dataset.mode = "add";
  form.dataset.productId = "";
  document.getElementById("product-panel-title").textContent = "+ Add a product";
  document.getElementById("product-submit-btn").textContent = "Add Product";
  document.getElementById("product-submit-btn").disabled = false;
  document.getElementById("product-panel").open = false;
  document.getElementById("product-form-error").classList.add("hidden");
}

// mode: "add" (blank), "edit" (a product), "copy" (a new product that starts as a copy of one).
function openProductForm(mode, product) {
  const form = document.getElementById("product-form");
  form.reset();
  adminState.productFormToken = Symbol();
  document.querySelectorAll(".pf-advanced").forEach(d => { d.open = false; });
  form.dataset.mode = mode;
  form.dataset.productId = product ? String(product.id) : "";
  const titles = {
    add: "+ Add a product",
    edit: `✎ Edit ${product ? product.name : ""}`,
    copy: `⧉ New product — a copy of ${product ? product.name : ""}`,
  };
  document.getElementById("product-panel-title").textContent = titles[mode];
  document.getElementById("product-submit-btn").textContent = mode === "edit" ? "Save Changes" : "Add Product";
  document.getElementById("product-submit-btn").disabled = false;
  PRODUCT_FORM_FIELDS.forEach(([id, key, kind]) => {
    const el = document.getElementById(id);
    let value = product ? product[key] : undefined;
    if (mode === "add") value = key === "discloses_impressions" || key === "national_supported" ? true : undefined;
    if (kind === "bool") el.checked = !!value;
    else el.value = value === null || value === undefined ? "" : value;
  });
  if (mode === "copy") document.getElementById("pf-name").value = `${product.name} (copy)`;
  if (mode === "add") document.getElementById("pf-buying-model").value = "Fixed";
  document.getElementById("product-form-error").classList.add("hidden");

  const panel = document.getElementById("product-panel");
  panel.open = true;
  panel.scrollIntoView({ behavior: "smooth", block: "center" });
  const nameEl = document.getElementById("pf-name");
  nameEl.focus();
  if (mode === "copy") nameEl.select();
}

function productFormPayload() {
  const payload = {};
  PRODUCT_FORM_FIELDS.forEach(([id, key, kind]) => {
    const el = document.getElementById(id);
    if (kind === "bool") payload[key] = el.checked;
    else if (kind === "number") {
      const v = el.value.trim();
      payload[key] = v === "" ? null : parseFloat(v);
    } else payload[key] = el.value.trim();
  });
  // blank flight days mean "leave as is", not "zero"
  ["minimum_flight_days_min", "minimum_flight_days_max"].forEach(k => { if (payload[k] === null) delete payload[k]; });
  return payload;
}

async function onSubmitProduct(e) {
  e.preventDefault();
  const form = e.target;
  const mode = form.dataset.mode || "add";
  const errEl = document.getElementById("product-form-error");
  errEl.classList.add("hidden");
  const payload = productFormPayload();
  if (mode === "copy") payload.copy_from_id = parseInt(form.dataset.productId);

  const submitBtn = document.getElementById("product-submit-btn");
  const idleLabel = submitBtn.textContent;
  const token = adminState.productFormToken = Symbol();
  submitBtn.disabled = true;
  submitBtn.textContent = mode === "edit" ? "Saving…" : "Adding…";
  try {
    const res = await fetch(mode === "edit" ? `/api/admin/products/${form.dataset.productId}` : "/api/admin/products", {
      method: mode === "edit" ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(detailText(body.detail, res.statusText));
    if (adminState.productFormToken === token) closeProductForm();         // not if the planner moved on meanwhile
    showProductNotice(mode, body);
    await loadRates();
  } catch (err) {
    if (adminState.productFormToken === token) {
      errEl.textContent = err.message;
      errEl.classList.remove("hidden");
    }
  } finally {
    // Only the form this request belongs to gets its button back; one the planner reopened is already reset.
    if (adminState.productFormToken === token) {
      submitBtn.disabled = false;
      submitBtn.textContent = idleLabel;
    }
  }
}

// What happened to the Restricted Verticals approvals when a product was duplicated.
function showProductNotice(mode, body) {
  const el = document.getElementById("product-notice");
  if (!el) return;
  if (mode !== "copy" || !body || !body.product) { el.classList.add("hidden"); return; }
  const n = body.restrictions_inherited || 0;
  const from = escapeHtml(body.product.copied_from || "The original");
  let html;
  if (body.restrictions_warning) html = `<span class="bur-error">${escapeHtml(body.restrictions_warning)}</span>`;
  else if (n > 0) html = `<strong>${escapeHtml(body.product.name)}</strong> added. It inherits ${n} Restricted Verticals approval${n === 1 ? "" : "s"} from ${from}.`;
  else html = `<strong>${escapeHtml(body.product.name)}</strong> added. ${from} isn't mapped in Restricted Verticals, so the copy isn't either.`;
  el.innerHTML = html;
  el.classList.remove("hidden");
}

// --------------------------------------------------------------------------
// Market config — per-market office address + CC list
// --------------------------------------------------------------------------

function wireMarketConfig() {
  const saveBaseBtn = document.getElementById("save-base-ccs-btn");
  if (saveBaseBtn) saveBaseBtn.addEventListener("click", onSaveBaseCcs);
  const saveT1Btn = document.getElementById("save-t1-ccs-btn");
  if (saveT1Btn) saveT1Btn.addEventListener("click", onSaveT1Ccs);

  const btn = document.getElementById("add-market-btn");
  const input = document.getElementById("new-market-name");
  if (!btn) return;
  btn.addEventListener("click", () => {
    const name = input.value.trim();
    if (!name) return;
    if (name === "__default__" || adminState.markets.some(m => m.market_key.toLowerCase() === name.toLowerCase())) {
      alert(`"${name}" already has an entry below.`);
      return;
    }
    // Client-side only until Save is clicked on this row — mirrors how a
    // blank rate row doesn't hit the server until someone fills it in.
    adminState.markets.push({ market_key: name, is_default: false, address_line1: "", address_line2: "", ccs: [] });
    input.value = "";
    renderMarketConfig();
  });
}

async function loadMarketConfig() {
  try {
    const res = await fetch("/api/admin/market-config");
    const raw = await res.text();
    let data = {};
    if (raw) {
      try {
        data = JSON.parse(raw);
      } catch {
        if (!res.ok) throw new Error(raw.trim() || `Server returned ${res.status}`);
        throw new Error("Server returned an invalid market configuration response.");
      }
    }
    if (!res.ok) {
      throw new Error(data.detail || raw.trim() || `Server returned ${res.status}`);
    }
    adminState.markets = data.markets || [];
    adminState.marketsLoaded = true;
    document.getElementById("base-ccs-input").value = (data.base_ccs || []).join(", ");
    document.getElementById("t1-ccs-input").value = (data.t1_ccs || []).join(", ");
    renderMarketConfig();
  } catch (e) {
    document.getElementById("markets-body").innerHTML =
      `<tr><td colspan="7" class="admin-empty">Failed to load: ${escapeHtml(e.message)}</td></tr>`;
  }
}

async function onSaveBaseCcs() {
  await _saveCcsList("save-base-ccs-btn", "base-ccs-input", "/api/admin/market-config/base-ccs");
}

async function onSaveT1Ccs() {
  await _saveCcsList("save-t1-ccs-btn", "t1-ccs-input", "/api/admin/market-config/t1-ccs");
}

async function _saveCcsList(btnId, inputId, endpoint) {
  const btn = document.getElementById(btnId);
  const input = document.getElementById(inputId);
  const ccs = input.value.split(",").map(s => s.trim()).filter(Boolean);
  btn.disabled = true;
  btn.textContent = "Saving…";
  try {
    const res = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ccs }),
    });
    if (!res.ok) throw new Error(res.statusText);
    input.value = ccs.join(", ");
    btn.textContent = "Saved ✓";
  } catch (e) {
    alert("Save failed: " + e.message);
    btn.textContent = "Save";
  } finally {
    btn.disabled = false;
    setTimeout(() => { btn.textContent = "Save"; }, 1500);
  }
}

function renderMarketConfig() {
  const body = document.getElementById("markets-body");
  const list = adminState.markets;
  document.getElementById("markets-count").textContent =
    `${list.length} market${list.length === 1 ? "" : ""}`.trim();

  if (!list.length) {
    body.innerHTML = `<tr><td colspan="8" class="admin-empty">No markets configured yet.</td></tr>`;
    return;
  }

  body.innerHTML = list.map(m => `
    <tr data-market="${escapeAttr(m.market_key)}">
      <td class="mono">${m.is_default ? "Default (all other markets)" : escapeHtml(m.market_key)}</td>
      <td><input type="text" data-field="address_line1" value="${escapeAttr(m.address_line1)}" placeholder="Street address" /></td>
      <td><input type="text" data-field="address_line2" value="${escapeAttr(m.address_line2)}" placeholder="City, State ZIP" /></td>
      <td><input type="text" data-field="dsc_email" value="${escapeAttr(m.dsc_email)}" placeholder="dsc@entravision.com" /></td>
      <td><input type="text" data-field="dsm_email" value="${escapeAttr(m.dsm_email)}" placeholder="dsm@entravision.com" /></td>
      <td><input type="text" data-field="research_emails" value="${escapeAttr((m.research_emails || []).join(", "))}" placeholder="research@entravision.com, …" /></td>
      <td><input type="text" data-field="ccs" value="${escapeAttr((m.ccs || []).join(", "))}" placeholder="name@entravision.com, …" /></td>
      <td>
        <button class="btn-save-row" data-action="save-market" data-market="${escapeAttr(m.market_key)}">Save</button>
        ${m.is_default ? "" : `<button class="btn-delete-row" data-action="delete-market" data-market="${escapeAttr(m.market_key)}">Delete</button>`}
      </td>
    </tr>
  `).join("");

  body.querySelectorAll('[data-action="save-market"]').forEach(btn => {
    btn.addEventListener("click", () => onSaveMarket(btn.dataset.market));
  });
  body.querySelectorAll('[data-action="delete-market"]').forEach(btn => {
    btn.addEventListener("click", () => onDeleteMarket(btn.dataset.market));
  });
}

async function onSaveMarket(marketKey) {
  const row = document.querySelector(`tr[data-market="${cssEscape(marketKey)}"]`);
  if (!row) return;
  const btn = row.querySelector('[data-action="save-market"]');
  btn.disabled = true;
  btn.textContent = "Saving…";

  const payload = { market_key: marketKey };
  row.querySelectorAll("input[data-field]").forEach(inp => {
    if (inp.dataset.field === "ccs" || inp.dataset.field === "research_emails") {
      payload[inp.dataset.field] = inp.value.split(/[,;\n]/).map(s => s.trim()).filter(Boolean);
    } else {
      payload[inp.dataset.field] = inp.value.trim();
    }
  });

  try {
    const res = await fetch("/api/admin/market-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      throw new Error(errBody.detail || res.statusText);
    }
    btn.textContent = "Saved ✓";
    await loadMarketConfig();
  } catch (e) {
    alert("Save failed: " + e.message);
    btn.disabled = false;
    btn.textContent = "Save";
  }
}

async function onDeleteMarket(marketKey) {
  if (!confirm(`Remove the "${marketKey}" market entry? It'll revert to the default address/CCs.`)) return;
  try {
    const res = await fetch(`/api/admin/market-config/${encodeURIComponent(marketKey)}`, { method: "DELETE" });
    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      throw new Error(errBody.detail || res.statusText);
    }
    await loadMarketConfig();
  } catch (e) {
    alert("Delete failed: " + e.message);
  }
}

// --------------------------------------------------------------------------
// Utilities
// --------------------------------------------------------------------------

function money(n) {
  if (n === null || n === undefined || isNaN(n)) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(n);
}

function formatDate(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString("en-US", { month: "short", day: "numeric", year: "2-digit", hour: "numeric", minute: "2-digit" });
  } catch {
    return iso;
  }
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

function escapeAttr(s) {
  return String(s || "").replace(/"/g, "&quot;");
}

function cssEscape(s) {
  return String(s).replace(/["\\]/g, "\\$&");
}

// --------------------------------------------------------------------------
// Users — who can sign in. Admin-only (the page itself is behind
// _require_login's is_admin check; this is just the UI for it).
// --------------------------------------------------------------------------

async function loadUsers() {
  try {
    const [usersRes, meRes] = await Promise.all([fetch("/api/admin/users"), fetch("/api/me")]);
    const data = await usersRes.json();
    adminState.users = data.users || [];
    adminState.usersLoaded = true;
    if (meRes.ok) adminState.currentUserEmail = (await meRes.json()).email;
    renderUsers();
  } catch (e) {
    document.getElementById("users-body").innerHTML =
      `<tr><td colspan="5" class="admin-empty">Failed to load: ${escapeHtml(e.message)}</td></tr>`;
  }
}

function renderUsers() {
  const body = document.getElementById("users-body");
  const list = adminState.users;
  if (!list.length) {
    body.innerHTML = `<tr><td colspan="5" class="admin-empty">No users yet.</td></tr>`;
    return;
  }
  body.innerHTML = list.map(u => {
    const isSelf = u.email === adminState.currentUserEmail;
    return `
    <tr data-user-id="${escapeAttr(u.id)}" class="${u.disabled ? "row-deleted" : ""}">
      <td class="wrap">${escapeHtml(u.email)}${isSelf ? ' <span class="custom-badge">You</span>' : ""}</td>
      <td>
        <label class="checkbox-label">
          <input type="checkbox" data-action="toggle-admin" ${u.is_admin ? "checked" : ""} ${isSelf ? "disabled title=\"You can't remove your own admin access\"" : ""} />
        </label>
      </td>
      <td>${u.disabled ? '<span class="deleted-badge">Disabled</span>' : '<span class="custom-badge">Active</span>'}</td>
      <td class="mono">${formatDate(u.created_at)}</td>
      <td>
        <button class="btn-secondary" data-action="reset-password">Reset password</button>
        ${isSelf
          ? ""
          : `<button class="btn-secondary" data-action="toggle-disabled">${u.disabled ? "Enable" : "Disable"}</button>
             <button class="btn-delete-row" data-action="delete-user">Delete</button>`}
      </td>
    </tr>
  `;
  }).join("");

  body.querySelectorAll('[data-action="toggle-admin"]').forEach(el => {
    el.addEventListener("change", (e) => onUpdateUser(rowUserId(e.target), { is_admin: e.target.checked }));
  });
  body.querySelectorAll('[data-action="toggle-disabled"]').forEach(btn => {
    btn.addEventListener("click", (e) => {
      const row = e.target.closest("tr");
      const user = adminState.users.find(u => u.id === row.dataset.userId);
      onUpdateUser(row.dataset.userId, { disabled: !user.disabled });
    });
  });
  body.querySelectorAll('[data-action="reset-password"]').forEach(btn => {
    btn.addEventListener("click", (e) => onResetPassword(rowUserId(e.target)));
  });
  body.querySelectorAll('[data-action="delete-user"]').forEach(btn => {
    btn.addEventListener("click", (e) => onDeleteUser(rowUserId(e.target)));
  });
}

function rowUserId(el) {
  return el.closest("tr").dataset.userId;
}

async function onUpdateUser(userId, patch) {
  try {
    const res = await fetch(`/api/admin/users/${encodeURIComponent(userId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || res.statusText);
    }
    await loadUsers();
  } catch (e) {
    alert("Update failed: " + e.message);
    await loadUsers();  // re-render to undo an optimistic checkbox flip, if any
  }
}

async function onResetPassword(userId) {
  const newPassword = prompt("New password for this user (12+ characters — a passphrase is fine):");
  if (newPassword === null) return;  // cancelled
  if (newPassword.length < 12) {
    alert("Password must be at least 12 characters.");
    return;
  }
  await onUpdateUser(userId, { new_password: newPassword });
  alert("Password reset. Share it with them through a secure channel — it won't be shown again here.");
}

async function onDeleteUser(userId) {
  const user = adminState.users.find(u => u.id === userId);
  if (!confirm(`Permanently delete the account for "${user ? user.email : userId}"? This can't be undone.`)) return;
  try {
    const res = await fetch(`/api/admin/users/${encodeURIComponent(userId)}`, { method: "DELETE" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || res.statusText);
    }
    await loadUsers();
  } catch (e) {
    alert("Delete failed: " + e.message);
  }
}

function wireAddUser() {
  const panel = document.getElementById("add-user-panel");
  const form = document.getElementById("add-user-form");
  if (!form) return;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const errEl = document.getElementById("add-user-error");
    errEl.classList.add("hidden");
    const submitBtn = e.target.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    submitBtn.textContent = "Adding…";
    try {
      const res = await fetch("/api/admin/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: document.getElementById("nu-email").value.trim(),
          password: document.getElementById("nu-password").value,
          is_admin: document.getElementById("nu-is-admin").checked,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || res.statusText);
      }
      form.reset();
      panel.open = false;
      await loadUsers();
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove("hidden");
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "Add User";
    }
  });
}


// --------------------------------------------------------------------------
// Disclaimers — keyword-triggered banner callouts for the Excel + PPT
// exports (see app/disclaimers.py). One form serves both "add" and "edit";
// adminState.disclaimerEditingId says which.
// --------------------------------------------------------------------------

async function loadDisclaimers() {
  const list = document.getElementById("dz-list");
  try {
    const res = await fetch("/api/admin/disclaimers");
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(detailText(data.detail, res.statusText));
    adminState.disclaimers = data.disclaimers || [];
    adminState.disclaimerColors = data.colors || {};
    adminState.disclaimerSwatches = data.color_swatches || {};
    adminState.disclaimersLoaded = true;
    fillDisclaimerColorSelect();
    renderDisclaimers();
    updateDisclaimerPreview();
  } catch (e) {
    list.innerHTML = `<p class="admin-empty">Failed to load: ${escapeHtml(e.message)}</p>`;
  }
}

function fillDisclaimerColorSelect() {
  const sel = document.getElementById("dz-color");
  if (sel.options.length) return;
  sel.innerHTML = Object.entries(adminState.disclaimerColors)
    .map(([key, label]) => `<option value="${escapeAttr(key)}">${escapeHtml(label)}</option>`).join("");
}

function swatchStyle(color) {
  const sw = adminState.disclaimerSwatches[color] || { fill: "F8D7DA", text: "58151C" };
  return `background:#${sw.fill};color:#${sw.text};`;
}

function updateDisclaimerPreview() {
  const box = document.getElementById("dz-preview");
  const text = document.getElementById("dz-text").value.trim();
  box.setAttribute("style", swatchStyle(document.getElementById("dz-color").value || "red"));
  box.textContent = text || "Your banner text will appear here, exactly as it will in the export.";
}

function renderDisclaimers() {
  const list = document.getElementById("dz-list");
  if (!adminState.disclaimers.length) {
    list.innerHTML = `<p class="admin-empty">No disclaimers yet.</p>`;
    return;
  }
  list.innerHTML = adminState.disclaimers.map(d => {
    const when = [];
    if (d.keywords.length) when.push(d.keywords.map(k => `<span class="dz-chip">${escapeHtml(k)}</span>`).join(""));
    if (d.season_start && d.season_end) when.push(`<span class="dz-chip dz-chip-season">Season ${escapeHtml(d.season_start)} → ${escapeHtml(d.season_end)}</span>`);
    const applies = { both: "Excel + PowerPoint", excel: "Excel only", ppt: "PowerPoint only" }[d.applies_to] || d.applies_to;
    const isSeed = d.id === "seed-live-sports";
    return `
    <article class="dz-card ${d.is_active ? "" : "dz-inactive"}" data-id="${escapeAttr(d.id)}">
      <header class="dz-card-head">
        <h3>${escapeHtml(d.name)}${d.is_active ? "" : ' <span class="deleted-badge">Off</span>'}${isSeed ? ' <span class="custom-badge">Built-in</span>' : ""}</h3>
        <span class="dz-meta">${escapeHtml(applies)}</span>
      </header>
      <div class="dz-banner" style="${swatchStyle(d.color)}">${escapeHtml(d.banner_text)}</div>
      <div class="dz-triggers"><span class="dz-triggers-label">Triggers on</span>${when.join("") || "—"}</div>
      <div class="dz-actions">
        <label class="checkbox-label"><input type="checkbox" data-action="dz-toggle" ${d.is_active ? "checked" : ""} /> Active</label>
        <button class="btn-secondary" data-action="dz-edit">Edit</button>
        ${isSeed ? "" : '<button class="btn-delete-row" data-action="dz-delete">Delete</button>'}
      </div>
    </article>`;
  }).join("");

  list.querySelectorAll('[data-action="dz-toggle"]').forEach(el => {
    el.addEventListener("change", (e) => toggleDisclaimer(e.target.closest(".dz-card").dataset.id, e.target.checked));
  });
  list.querySelectorAll('[data-action="dz-edit"]').forEach(btn => {
    btn.addEventListener("click", (e) => startEditDisclaimer(e.target.closest(".dz-card").dataset.id));
  });
  list.querySelectorAll('[data-action="dz-delete"]').forEach(btn => {
    btn.addEventListener("click", (e) => deleteDisclaimer(e.target.closest(".dz-card").dataset.id));
  });
}

function disclaimerFormPayload() {
  return {
    name: document.getElementById("dz-name").value.trim(),
    keywords: document.getElementById("dz-keywords").value,
    banner_text: document.getElementById("dz-text").value.trim(),
    color: document.getElementById("dz-color").value,
    applies_to: document.getElementById("dz-applies").value,
    season_start: document.getElementById("dz-season-start").value.trim() || null,
    season_end: document.getElementById("dz-season-end").value.trim() || null,
  };
}

function resetDisclaimerForm() {
  adminState.disclaimerEditingId = null;
  document.getElementById("dz-form").reset();
  document.getElementById("dz-form-summary").textContent = "+ Add disclaimer";
  document.getElementById("dz-submit").textContent = "Add Disclaimer";
  document.getElementById("dz-cancel-edit").classList.add("hidden");
  document.getElementById("dz-error").classList.add("hidden");
  updateDisclaimerPreview();
}

function startEditDisclaimer(id) {
  const d = adminState.disclaimers.find(x => x.id === id);
  if (!d) return;
  adminState.disclaimerEditingId = id;
  document.getElementById("dz-name").value = d.name;
  document.getElementById("dz-keywords").value = d.keywords.join(", ");
  document.getElementById("dz-text").value = d.banner_text;
  document.getElementById("dz-color").value = d.color;
  document.getElementById("dz-applies").value = d.applies_to;
  document.getElementById("dz-season-start").value = d.season_start || "";
  document.getElementById("dz-season-end").value = d.season_end || "";
  document.getElementById("dz-form-summary").textContent = `Editing: ${d.name}`;
  document.getElementById("dz-submit").textContent = "Save Changes";
  document.getElementById("dz-cancel-edit").classList.remove("hidden");
  document.getElementById("dz-error").classList.add("hidden");
  const panel = document.getElementById("dz-form-panel");
  panel.open = true;
  updateDisclaimerPreview();
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function toggleDisclaimer(id, isActive) {
  try {
    const res = await fetch(`/api/admin/disclaimers/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_active: isActive }),
    });
    if (!res.ok) throw new Error(detailText((await res.json().catch(() => ({}))).detail, res.statusText));
  } catch (e) {
    alert("Update failed: " + e.message);
  }
  await loadDisclaimers();
}

async function deleteDisclaimer(id) {
  const d = adminState.disclaimers.find(x => x.id === id);
  if (!confirm(`Delete the "${d ? d.name : id}" disclaimer? Exports will stop including it. This can't be undone.`)) return;
  try {
    const res = await fetch(`/api/admin/disclaimers/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!res.ok) throw new Error(detailText((await res.json().catch(() => ({}))).detail, res.statusText));
    if (adminState.disclaimerEditingId === id) resetDisclaimerForm();
  } catch (e) {
    alert("Delete failed: " + e.message);
  }
  await loadDisclaimers();
}

function wireDisclaimers() {
  const form = document.getElementById("dz-form");
  if (!form) return;
  ["dz-text", "dz-color"].forEach(id => {
    document.getElementById(id).addEventListener("input", updateDisclaimerPreview);
    document.getElementById(id).addEventListener("change", updateDisclaimerPreview);
  });
  document.getElementById("dz-cancel-edit").addEventListener("click", () => {
    resetDisclaimerForm();
    document.getElementById("dz-form-panel").open = false;
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const errEl = document.getElementById("dz-error");
    errEl.classList.add("hidden");
    const submitBtn = document.getElementById("dz-submit");
    const editingId = adminState.disclaimerEditingId;
    const idleLabel = submitBtn.textContent;
    submitBtn.disabled = true;
    submitBtn.textContent = "Saving…";
    try {
      const res = await fetch(
        editingId ? `/api/admin/disclaimers/${encodeURIComponent(editingId)}` : "/api/admin/disclaimers",
        {
          method: editingId ? "PUT" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(disclaimerFormPayload()),
        });
      if (!res.ok) throw new Error(detailText((await res.json().catch(() => ({}))).detail, res.statusText));
      resetDisclaimerForm();
      document.getElementById("dz-form-panel").open = false;
      await loadDisclaimers();
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove("hidden");
      submitBtn.textContent = idleLabel;
    } finally {
      submitBtn.disabled = false;
    }
  });
}


// --------------------------------------------------------------------------
// FAQ — the rows of the exports' "Process FAQs" tab (app/services/faq_entries.py):
// the FAQ table and the "Category and product restrictions" block under it.
// --------------------------------------------------------------------------

const FAQ_BLOCK_LABELS = { faq: "Process FAQ", restriction: "Restriction" };

function isWebLink(url) {
  return /^https?:\/\//i.test(url || "");
}

async function loadFaq() {
  try {
    const res = await fetch("/api/admin/faq");
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(detailText(data.detail, res.statusText));
    adminState.faq = data.entries || [];
    adminState.faqLoaded = true;
    const link = document.getElementById("faq-guidelines-link");
    if (link && data.guidelines_url && isWebLink(data.guidelines_url)) link.href = data.guidelines_url;
    renderFaq();
  } catch (e) {
    document.getElementById("faq-body").innerHTML =
      `<tr><td colspan="6" class="admin-empty">Failed to load: ${escapeHtml(e.message)}</td></tr>`;
  }
}

function renderFaq() {
  const body = document.getElementById("faq-body");
  const entries = adminState.faq;
  document.getElementById("faq-count").textContent = `${entries.length} row${entries.length === 1 ? "" : "s"}`;
  if (!entries.length) {
    body.innerHTML = `<tr><td colspan="6" class="admin-empty">No rows yet.</td></tr>`;
    return;
  }
  // First / last of each block can't move further up / down.
  const bySection = {};
  entries.forEach(e => { (bySection[e.section] = bySection[e.section] || []).push(e.id); });
  body.innerHTML = entries.map(e => {
    const ids = bySection[e.section];
    const first = ids[0] === e.id, last = ids[ids.length - 1] === e.id;
    const linkCell = e.link_url
      ? (isWebLink(e.link_url)
          ? `<a href="${escapeAttr(e.link_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(e.link_url)}</a>`
          : escapeHtml(e.link_url))
      : `<span class="faq-nolink" title="Without a link the title is plain text in the export">no link yet</span>`;
    return `
      <tr data-faq="${e.id}">
        <td class="mono">${escapeHtml(FAQ_BLOCK_LABELS[e.section] || e.section)}</td>
        <td class="wrap"><strong>${escapeHtml(e.services)}</strong></td>
        <td class="wrap">${escapeHtml(e.question)}</td>
        <td class="wrap">${escapeHtml(e.link_title)}</td>
        <td class="wrap faq-link-cell">${linkCell}</td>
        <td class="faq-actions">
          <button class="btn-secondary" data-action="up" data-faq="${e.id}" title="Move up" ${first ? "disabled" : ""}>↑</button>
          <button class="btn-secondary" data-action="down" data-faq="${e.id}" title="Move down" ${last ? "disabled" : ""}>↓</button>
          <button class="btn-secondary" data-action="edit" data-faq="${e.id}">✎ Edit</button>
          <button class="btn-delete-row" data-action="delete" data-faq="${e.id}">Delete</button>
        </td>
      </tr>`;
  }).join("");
  body.querySelectorAll("[data-action]").forEach(btn => {
    btn.addEventListener("click", () => {
      const id = parseInt(btn.dataset.faq);
      if (btn.dataset.action === "edit") openFaqForm(adminState.faq.find(x => x.id === id));
      else if (btn.dataset.action === "delete") onDeleteFaq(id);
      else onMoveFaq(id, btn.dataset.action);
    });
  });
}

function wireFaq() {
  const form = document.getElementById("faq-form");
  if (!form) return;
  form.addEventListener("submit", onSubmitFaq);
  document.getElementById("faq-cancel-btn").addEventListener("click", closeFaqForm);
  document.getElementById("add-faq-btn").addEventListener("click", () => openFaqForm());
}

function closeFaqForm() {
  adminState.faqFormToken = Symbol();
  adminState.faqEditingId = null;
  document.getElementById("faq-panel-title").textContent = "+ Add an FAQ row";
  document.getElementById("faq-submit-btn").textContent = "Add Row";
  document.getElementById("faq-submit-btn").disabled = false;
  document.getElementById("faq-panel").open = false;
  document.getElementById("faq-error").classList.add("hidden");
}

// No entry = add a new row; an entry = edit it.
function openFaqForm(entry) {
  const form = document.getElementById("faq-form");
  form.reset();
  adminState.faqFormToken = Symbol();
  adminState.faqEditingId = entry ? entry.id : null;
  document.getElementById("faq-section").value = entry ? entry.section : "faq";
  document.getElementById("faq-services").value = entry ? entry.services : "";
  document.getElementById("faq-question").value = entry ? entry.question : "";
  document.getElementById("faq-link-title").value = entry ? entry.link_title : "";
  document.getElementById("faq-link-url").value = entry ? entry.link_url : "";
  document.getElementById("faq-panel-title").textContent = entry ? "✎ Edit FAQ row" : "+ Add an FAQ row";
  document.getElementById("faq-submit-btn").textContent = entry ? "Save Changes" : "Add Row";
  document.getElementById("faq-submit-btn").disabled = false;
  document.getElementById("faq-error").classList.add("hidden");
  const panel = document.getElementById("faq-panel");
  panel.open = true;
  panel.scrollIntoView({ behavior: "smooth", block: "center" });
  document.getElementById("faq-services").focus();
}

async function onSubmitFaq(e) {
  e.preventDefault();
  const errEl = document.getElementById("faq-error");
  errEl.classList.add("hidden");
  const editingId = adminState.faqEditingId;
  const payload = {
    section: document.getElementById("faq-section").value,
    services: document.getElementById("faq-services").value.trim(),
    question: document.getElementById("faq-question").value.trim(),
    link_title: document.getElementById("faq-link-title").value.trim(),
    link_url: document.getElementById("faq-link-url").value.trim(),
  };
  const submitBtn = document.getElementById("faq-submit-btn");
  const idleLabel = submitBtn.textContent;
  const token = adminState.faqFormToken = Symbol();
  submitBtn.disabled = true;
  submitBtn.textContent = "Saving…";
  try {
    const res = await fetch(editingId ? `/api/admin/faq/${editingId}` : "/api/admin/faq", {
      method: editingId ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(detailText(body.detail, res.statusText));
    if (adminState.faqFormToken === token) closeFaqForm();        // not if the admin moved on meanwhile
    await loadFaq();
  } catch (err) {
    if (adminState.faqFormToken === token) {
      errEl.textContent = err.message;
      errEl.classList.remove("hidden");
    }
  } finally {
    if (adminState.faqFormToken === token) {
      submitBtn.disabled = false;
      submitBtn.textContent = idleLabel;
    }
  }
}

async function onDeleteFaq(id) {
  const entry = adminState.faq.find(x => x.id === id);
  if (!entry) return;
  if (!confirm(`Delete this row?\n\n${entry.services}`)) return;
  try {
    const res = await fetch(`/api/admin/faq/${id}`, { method: "DELETE" });
    if (!res.ok) throw new Error(detailText((await res.json().catch(() => ({}))).detail, res.statusText));
    if (adminState.faqEditingId === id) closeFaqForm();
    await loadFaq();
  } catch (err) {
    alert("Delete failed: " + err.message);
  }
}

async function onMoveFaq(id, direction) {
  try {
    const res = await fetch(`/api/admin/faq/${id}/move`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ direction }),
    });
    if (!res.ok) throw new Error(detailText((await res.json().catch(() => ({}))).detail, res.statusText));
    await loadFaq();
  } catch (err) {
    alert("Move failed: " + err.message);
  }
}

// --------------------------------------------------------------------------
// Restrictions — Entravision's "Restricted Verticals by Platforms" sheet,
// ingested as the Roadblocks step's source of truth (app/services/
// restrictions.py). Sync/upload replaces everything; the per-vertical forms
// edit the two things a sync can only guess at: trigger keywords and which
// catalog products each sheet entry stands for.
// --------------------------------------------------------------------------

async function loadRestrictions() {
  try {
    const res = await fetch("/api/admin/restrictions");
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(detailText(data.detail, res.statusText));
    applyRestrictionsPayload(data);
  } catch (e) {
    document.getElementById("rs-list").innerHTML = `<p class="admin-empty">Failed to load: ${escapeHtml(e.message)}</p>`;
  }
}

function applyRestrictionsPayload(data) {
  adminState.restrictions = data;
  // Parser warnings only come back from a sync/upload; later saves keep showing the last ones.
  if (data.synced || data.created) adminState.restrictionWarnings = data.warnings || [];
  const warnEl = document.getElementById("rs-warnings");
  const warnings = adminState.restrictionWarnings || [];
  warnEl.innerHTML = warnings.length
    ? `<strong>Worth a look</strong><ul>${warnings.map(w => `<li>${escapeHtml(w)}</li>`).join("")}</ul>`
    : "";
  warnEl.classList.toggle("hidden", !warnings.length);
  document.getElementById("rs-url").value = data.sheet_url || "";
  document.getElementById("rs-status").textContent = data.using_builtin
    ? "Built-in snapshot (Political, Cannabis) — sheet not synced yet"
    : `${data.source_title || "Synced"} · ${data.synced_at ? formatDate(data.synced_at) : ""}${data.synced_by ? " · " + data.synced_by : ""}`;
  renderRestrictions();
}

const RS_KIND_LABEL = { allowlist: "Approved-products list", blanket_hold: "Not accepted anywhere", guidance: "Platform guidance" };

function renderRestrictions() {
  const data = adminState.restrictions;
  const list = document.getElementById("rs-list");
  const known = new Set(data.catalog_names);
  list.innerHTML = data.categories.map(c => {
    const mapping = c.kind === "allowlist" ? `
      <div class="rs-map">
        <div class="dz-triggers-label">Which catalog products each sheet entry covers</div>
        ${c.items.map(item => `
          <label class="rs-field">
            <span class="rs-item">${escapeHtml(item)}</span>
            <textarea rows="2" data-rs-item="${escapeHtml(item)}" placeholder="No catalog product matched — type product names, comma separated">${escapeHtml((c.product_map[item] || []).join(", "))}</textarea>
          </label>`).join("")}
      </div>` : "";
    // A category added by hand has its own editable text; a sheet tab shows the sheet's text read-only.
    const body = c.is_manual
      ? `<div class="rs-own">
           <label class="rs-field">Type
             <select data-rs-kind>
               ${Object.entries(RS_KIND_LABEL).map(([k, label]) => `<option value="${k}" ${k === c.kind ? "selected" : ""}>${escapeHtml(label)}</option>`).join("")}
             </select>
           </label>
           <label class="rs-field">One-line summary
             <input type="text" data-rs-summary maxlength="500" value="${escapeHtml(c.summary)}" />
           </label>
           <label class="rs-field">Details / rules for the AI
             <textarea rows="5" data-rs-body maxlength="20000">${escapeHtml(c.body)}</textarea>
           </label>
           ${c.kind === "allowlist" ? `<label class="rs-field">Approved products <small>(one per line)</small>
             <textarea rows="4" data-rs-items>${escapeHtml(c.items.join("\n"))}</textarea>
           </label>` : ""}
         </div>`
      : `<details class="rs-body"><summary>Sheet text (what the AI reads)</summary><pre>${escapeHtml(c.body)}</pre></details>`;
    const notes = `<label class="rs-field">Extra notes for the AI <small>(rules you add on top${c.is_manual ? "" : " of the sheet text"} — the Roadblocks AI reads them too; kept when the sheet is synced)</small>
        <textarea rows="3" data-rs-notes maxlength="6000" placeholder="e.g. Ads must carry the TTB responsible-drinking line; no creative aimed at under-21s.">${escapeHtml(c.extra_notes || "")}</textarea>
      </label>`;
    return `
    <article class="dz-card" data-rs-category="${escapeHtml(c.name)}" ${c.is_manual ? 'data-rs-manual="1"' : ""}>
      <header class="dz-card-head">
        <h3>${escapeHtml(c.name)}</h3>
        <span class="dz-meta">${c.is_manual ? "Added by hand · " : ""}${escapeHtml(RS_KIND_LABEL[c.kind] || c.kind)}</span>
      </header>
      ${c.is_manual ? "" : `<p class="rs-summary">${escapeHtml(c.summary)}</p>`}
      <label class="rs-field">Suggested when the plan mentions <small>(comma separated — whole words, case-insensitive)</small>
        <input type="text" data-rs-keywords value="${escapeHtml(c.keywords.join(", "))}" />
      </label>
      ${mapping}
      ${body}
      ${notes}
      <div class="dz-actions">
        <span class="add-product-error hidden" data-rs-card-error></span>
        <span class="rs-unknown hidden" data-rs-unknown></span>
        ${c.is_manual ? `<button type="button" class="btn-delete-row" data-rs-delete>Delete category</button>` : ""}
        <button type="button" class="btn-secondary" data-rs-save>Save changes</button>
      </div>
    </article>`;
  }).join("") || `<p class="admin-empty">No verticals yet.</p>`;

  list.querySelectorAll("[data-rs-save]").forEach(btn => {
    btn.addEventListener("click", () => saveRestriction(btn.closest("[data-rs-category]"), known));
  });
  list.querySelectorAll("[data-rs-delete]").forEach(btn => {
    btn.addEventListener("click", () => deleteRestriction(btn.closest("[data-rs-category]")));
  });
}

// FastAPI reports a validation failure as an ARRAY of {msg, loc, ...}; show its messages, not "[object Object]".
function detailText(detail, fallback) {
  const one = d => {
    if (d == null) return "";
    if (typeof d === "string") return d;
    if (typeof d === "object") return d.msg || d.message || d.detail || JSON.stringify(d);
    return String(d);
  };
  const text = Array.isArray(detail) ? detail.map(one).filter(Boolean).join("; ") : one(detail);
  return text || fallback || "Request failed.";
}

function splitNames(text) {
  return String(text || "").split(/[,\n]/).map(s => s.trim()).filter(Boolean);
}

async function saveRestriction(card, known) {
  const name = card.dataset.rsCategory;
  const errEl = card.querySelector("[data-rs-card-error]");
  const unknownEl = card.querySelector("[data-rs-unknown]");
  errEl.classList.add("hidden");
  const productMap = {};
  const unknown = [];
  card.querySelectorAll("[data-rs-item]").forEach(ta => {
    const names = splitNames(ta.value);
    productMap[ta.dataset.rsItem] = names;
    names.forEach(n => { if (!known.has(n)) unknown.push(n); });
  });
  unknownEl.classList.toggle("hidden", !unknown.length);
  unknownEl.textContent = unknown.length ? `Not catalog products: ${unknown.join(", ")}` : "";
  const payload = {
    keywords: card.querySelector("[data-rs-keywords]").value,
    product_map: productMap,
    extra_notes: card.querySelector("[data-rs-notes]").value,
  };
  if (card.dataset.rsManual) {                       // a hand-added category: its own text is editable too
    payload.kind = card.querySelector("[data-rs-kind]").value;
    payload.summary = card.querySelector("[data-rs-summary]").value;
    payload.body = card.querySelector("[data-rs-body]").value;
    const itemsEl = card.querySelector("[data-rs-items]");
    payload.items = itemsEl ? itemsEl.value.split("\n").map(x => x.trim()).filter(Boolean) : [];
    if (payload.kind === "allowlist" && !itemsEl) {  // switched to "approved products" just now: ask for the list first
      errEl.textContent = "Save once to switch the type, then list the approved products.";
      errEl.classList.remove("hidden");
      payload.items = null;
    }
  }
  try {
    const res = await fetch(`/api/admin/restrictions/${encodeURIComponent(name)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(detailText(data.detail, res.statusText));
    applyRestrictionsPayload(data);
    // The re-render above rebuilt this card; put the not-in-the-catalog warning back so it survives the save.
    if (unknown.length) {
      const fresh = document.querySelector(`[data-rs-category="${CSS.escape(name)}"] [data-rs-unknown]`);
      if (fresh) {
        fresh.textContent = `Saved, but not catalog products: ${unknown.join(", ")}`;
        fresh.classList.remove("hidden");
      }
    }
  } catch (e) {
    errEl.textContent = e.message;
    errEl.classList.remove("hidden");
  }
}

async function deleteRestriction(card) {
  const name = card.dataset.rsCategory;
  if (!confirm(`Delete the category "${name}"? Step 07 will no longer offer it and the AI won't read its rules.`)) return;
  const errEl = card.querySelector("[data-rs-card-error]");
  try {
    const res = await fetch(`/api/admin/restrictions/${encodeURIComponent(name)}`, { method: "DELETE" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(detailText(data.detail, res.statusText));
    applyRestrictionsPayload(data);
  } catch (e) {
    errEl.textContent = e.message;
    errEl.classList.remove("hidden");
  }
}

// The "Add a category" form: type decides whether the rules text or the approved-products list is asked for.
function wireAddRestriction() {
  const form = document.getElementById("rs-add-form");
  if (!form) return;
  const kindEl = document.getElementById("rs-add-kind");
  const errEl = document.getElementById("rs-add-error");
  const syncType = () => {
    const allow = kindEl.value === "allowlist";
    document.getElementById("rs-add-items-wrap").classList.toggle("hidden", !allow);
    document.getElementById("rs-add-body-req").classList.toggle("hidden", kindEl.value !== "guidance");
  };
  kindEl.addEventListener("change", syncType);
  syncType();
  document.getElementById("rs-add-cancel").addEventListener("click", () => {
    form.reset();
    syncType();
    errEl.classList.add("hidden");
    document.getElementById("rs-add-panel").open = false;
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errEl.classList.add("hidden");
    const btn = document.getElementById("rs-add-submit");
    const idle = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Adding…";
    try {
      const res = await fetch("/api/admin/restrictions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: document.getElementById("rs-add-name").value,
          kind: kindEl.value,
          keywords: document.getElementById("rs-add-keywords").value,
          summary: document.getElementById("rs-add-summary").value,
          body: document.getElementById("rs-add-body").value,
          items: kindEl.value === "allowlist" ? document.getElementById("rs-add-items").value : "",
          extra_notes: document.getElementById("rs-add-notes").value,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(detailText(data.detail, res.statusText));
      form.reset();
      syncType();
      document.getElementById("rs-add-panel").open = false;
      applyRestrictionsPayload(data);
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove("hidden");
    } finally {
      btn.disabled = false;
      btn.textContent = idle;
    }
  });
}

function wireRestrictions() {
  wireAddRestriction();
  const syncBtn = document.getElementById("rs-sync-btn");
  if (!syncBtn) return;
  const errEl = document.getElementById("rs-error");
  const run = async (btn, label, request) => {
    errEl.classList.add("hidden");
    const idle = btn.textContent;
    btn.disabled = true;
    btn.textContent = label;
    try {
      const res = await request();
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(detailText(data.detail, res.statusText));
      applyRestrictionsPayload(data);
    } catch (e) {
      errEl.textContent = e.message;
      errEl.classList.remove("hidden");
    } finally {
      btn.disabled = false;
      btn.textContent = idle;
    }
  };
  syncBtn.addEventListener("click", () => run(syncBtn, "Syncing…", () => fetch("/api/admin/restrictions/sync", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sheet_url: document.getElementById("rs-url").value.trim() || null }),
  })));
  const uploadBtn = document.getElementById("rs-upload-btn");
  uploadBtn.addEventListener("click", () => {
    const file = document.getElementById("rs-file").files[0];
    if (!file) { errEl.textContent = "Choose an .xlsx file first."; errEl.classList.remove("hidden"); return; }
    run(uploadBtn, "Uploading…", () => new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(new Error("Couldn't read that file."));
      reader.onload = () => {
        const bytes = new Uint8Array(reader.result);
        let bin = "";
        for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
        resolve(fetch("/api/admin/restrictions/upload", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ filename: file.name, data_base64: btoa(bin) }),
        }));
      };
      reader.readAsArrayBuffer(file);
    }));
  });
}

// --------------------------------------------------------------------------
// Forecast — which products get Constanza's "Auto-calculate avails" button on the Avails step, and what it
// computes (app/services/constanza_forecast_map.py). Every change saves as it is made; the server answers with the
// refreshed list, so the page always shows what is actually stored.
// --------------------------------------------------------------------------

function wireForecast() {
  document.getElementById("fc-search").addEventListener("input", renderForecastRows);
  document.getElementById("fc-family-filter").addEventListener("change", renderForecastRows);
  document.getElementById("fc-only-on").addEventListener("change", renderForecastRows);
  document.getElementById("fc-tt-test").addEventListener("click", testTikTokConnection);
}

// ---- TikTok connection (Admin -> Forecast): is it set up, and a one-click end-to-end check. No secret is ever shown. ----------
let _ttTesting = false;                    // a test is running: the button stays off until it ends, whatever else refreshes the status

async function loadTikTokStatus() {
  const state = document.getElementById("fc-tt-state");
  const btn = document.getElementById("fc-tt-test");
  try {
    const res = await fetch("/api/constanza/tiktok/status");
    const s = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(detailText(s.detail, res.statusText));
    if (s.configured) {
      state.innerHTML = `<span class="fc-badge fc-custom">Connected</span> ad account ${escapeHtml(s.advertiser || "")} · impressions = audience × ${escapeHtml(String(s.impressions_per_person))}`;
    } else {
      state.innerHTML = `<span class="fc-badge fc-default">Not connected</span> add <code>${s.missing.map(escapeHtml).join("</code> and <code>")}</code> to the app's secrets`;
    }
    btn.disabled = _ttTesting || !s.configured;
    btn.title = s.configured ? "" : "Add the TikTok secrets first";
  } catch (e) {
    state.textContent = `Couldn't read the TikTok status: ${e.message}`;
  }
}

async function testTikTokConnection() {
  const btn = document.getElementById("fc-tt-test");
  const list = document.getElementById("fc-tt-checks");
  const note = document.getElementById("fc-tt-note");
  if (_ttTesting) return;
  _ttTesting = true;
  btn.disabled = true;
  note.classList.remove("fc-err");
  note.textContent = "Checking with TikTok… (about 10–20 seconds)";
  list.innerHTML = "";
  try {
    const res = await fetch("/api/admin/tiktok/test", { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(detailText(data.detail, res.statusText));
    list.innerHTML = (data.checks || []).map(c => {
      const mark = !c.ok ? "✗" : (c.warn ? "!" : "✓");
      const cls = !c.ok ? "fc-chk-bad" : (c.warn ? "fc-chk-warn" : "fc-chk-ok");
      return `<li class="${cls}"><strong>${mark} ${escapeHtml(c.name)}</strong> <span class="mono">${c.seconds}s</span><div>${escapeHtml(c.detail)}</div></li>`;
    }).join("");
    const bad = (data.checks || []).some(c => !c.ok), warn = (data.checks || []).some(c => c.warn);
    note.textContent = bad ? "Something failed — see below." : (warn ? "Works, with something to look at." : "Everything works ✓");
    note.classList.toggle("fc-err", bad);
  } catch (e) {
    note.textContent = e.message;
    note.classList.add("fc-err");
  } finally {
    _ttTesting = false;
    btn.disabled = false;
    loadTikTokStatus();
  }
}

async function loadForecast() {
  try {
    const res = await fetch("/api/admin/forecast-map");
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(detailText(data.detail, res.statusText));
    adminState.forecastLoaded = true;
    applyForecastPayload(data);
    loadTikTokStatus();
  } catch (e) {
    document.getElementById("fc-body").innerHTML = `<tr><td colspan="8" class="admin-empty">Failed to load: ${escapeHtml(e.message)}</td></tr>`;
    document.getElementById("fc-coverage-body").innerHTML = `<tr><td colspan="4" class="admin-empty">—</td></tr>`;
    loadTikTokStatus();                    // the connection card does not depend on the product table: it still says what it can
  }
}

function applyForecastPayload(data) {
  adminState.forecast = data;
  const select = document.getElementById("fc-family-filter");
  const chosen = select.value;
  select.innerHTML = `<option value="">All families</option>` +
    (data.families || []).map(f => `<option value="${escapeAttr(f)}">${escapeHtml(f)}</option>`).join("");
  select.value = (data.families || []).includes(chosen) ? chosen : "";
  renderForecastSummary();
  renderForecastCoverage();
  renderForecastRows();
}

function renderForecastSummary() {
  const sm = adminState.forecast.summary;
  document.getElementById("fc-summary").innerHTML =
    _analyticsStatCard("Products with the button", sm.enabled, `of ${sm.total} in the catalog`) +
    _analyticsStatCard("Platform data", sm.platform_modeled, "modeled from the DMA avails data") +
    _analyticsStatCard("Population share", sm.generic, "the line's budget, spread by population") +
    (sm.tiktok ? _analyticsStatCard("TikTok API", sm.tiktok, "a live audience-size estimate from TikTok") : "") +
    _analyticsStatCard("Customized", sm.customized, "differ from the built-in default");
}

function renderForecastCoverage() {
  const body = document.getElementById("fc-coverage-body");
  const rows = adminState.forecast.coverage || [];
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="4" class="admin-empty">No platform data is bundled.</td></tr>`;
    return;
  }
  body.innerHTML = rows.map(c => {
    const using = c.products.length
      ? c.products.map(n => `<div class="fc-prod">${escapeHtml(n)}</div>`).join("")
      : `<span class="muted">No product uses this yet</span>`;
    const parked = c.disabled_products.length
      ? `<div class="fc-parked">Button off: ${c.disabled_products.map(escapeHtml).join("; ")}</div>` : "";
    const m = c.missing_product;
    const add = m
      ? `<button type="button" class="btn-secondary fc-add" data-fc-add="${escapeAttr(m.name)}" data-fc-rate="${escapeAttr(m.base_rate == null ? "" : m.base_rate)}" data-fc-model="${escapeAttr(m.buying_model)}" data-fc-min="${escapeAttr(m.minimum_spend)}">+ Add “${escapeHtml(m.name.length > 44 ? m.name.slice(0, 41) + "…" : m.name)}” to the catalog</button>`
      : "";
    return `<tr>
      <td><strong>${escapeHtml(c.label)}</strong><div class="mono">${escapeHtml(c.key)}</div></td>
      <td class="mono">${c.dmas}</td>
      <td class="wrap">${using}${parked}</td>
      <td class="wrap">${add}</td>
    </tr>`;
  }).join("");
  body.querySelectorAll("[data-fc-add]").forEach(btn => btn.addEventListener("click", () => addForecastPlatformProduct(btn)));
}

function _forecastRowHtml(p) {
  const platforms = adminState.forecast.platforms || [];
  const platformOptions = platforms.map(pl =>
    `<option value="${escapeAttr(pl.key)}" ${p.platform === pl.key ? "selected" : ""}>${escapeHtml(pl.label)}</option>`).join("");
  const isPlatform = p.method === "platform";
  const def = p.default || {};
  const defaultText = def.enabled
    ? `On · ${def.method === "platform" ? "platform data" : (def.method === "tiktok" ? "TikTok API (live)" : "population share")}`
    : "Off";
  return `
    <tr class="fc-row ${p.enabled ? "fc-on" : "fc-off"}" data-fc-product="${escapeAttr(p.name)}">
      <td class="wrap">${escapeHtml(p.name)}</td>
      <td>${escapeHtml(p.family)}</td>
      <td><label class="fc-toggle"><input type="checkbox" data-fc="enabled" ${p.enabled ? "checked" : ""} /> <span>${p.enabled ? "On" : "Off"}</span></label></td>
      <td>
        <select data-fc="method" aria-label="What it computes">
          <option value="platform" ${isPlatform ? "selected" : ""}>Platform data</option>
          <option value="generic" ${p.method === "generic" ? "selected" : ""}>Population share</option>
          <option value="tiktok" ${p.method === "tiktok" ? "selected" : ""}>TikTok API (live)</option>
        </select>
      </td>
      <td>
        <select data-fc="platform" aria-label="Platform data" ${isPlatform ? "" : "disabled"}>
          ${isPlatform ? "" : `<option value="" selected>—</option>`}${platformOptions}
        </select>
      </td>
      <td><input type="checkbox" data-fc="hispanic_weighted" aria-label="Hispanic weighting" ${p.hispanic_weighted ? "checked" : ""} ${isPlatform ? "" : "disabled"} /></td>
      <td><span class="fc-badge ${p.customized ? "fc-custom" : "fc-default"}" title="Built-in default: ${escapeAttr(defaultText)}">${p.customized ? "Customized" : "Default"}</span></td>
      <td class="fc-actions">
        <span class="fc-saved" data-fc-status></span>
        ${p.customized ? `<button type="button" class="btn-delete-row" data-fc-reset title="Back to the built-in default (${escapeAttr(defaultText)})">Reset</button>` : ""}
      </td>
    </tr>`;
}

function renderForecastRows() {
  const body = document.getElementById("fc-body");
  const all = adminState.forecast.products || [];
  const q = document.getElementById("fc-search").value.trim().toLowerCase();
  const family = document.getElementById("fc-family-filter").value;
  const onlyOn = document.getElementById("fc-only-on").checked;
  const list = all.filter(p =>
    (!family || p.family === family) && (!onlyOn || p.enabled) &&
    (!q || p.name.toLowerCase().includes(q) || p.family.toLowerCase().includes(q)));
  document.getElementById("fc-count").textContent = `${list.length} of ${all.length} products`;
  if (!list.length) {
    body.innerHTML = `<tr><td colspan="8" class="admin-empty">No products match.</td></tr>`;
    return;
  }
  body.innerHTML = list.map(_forecastRowHtml).join("");
  body.querySelectorAll("tr[data-fc-product]").forEach(wireForecastRow);
}

function wireForecastRow(tr) {
  tr.querySelectorAll("[data-fc]").forEach(el => el.addEventListener("change", () => saveForecastRow(tr, el.dataset.fc)));
  const reset = tr.querySelector("[data-fc-reset]");
  if (reset) reset.addEventListener("click", () => resetForecastRow(tr));
}

function _forecastStatus(tr, text, isError) {
  const el = tr.querySelector("[data-fc-status]");
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("fc-err", !!isError);
  if (text && !isError) setTimeout(() => { if (el.textContent === text) el.textContent = ""; }, 2000);
}

function _forecastRowState(name) {
  return (adminState.forecast.products || []).find(p => p.name === name);
}

async function saveForecastRow(tr, changed) {
  const name = tr.dataset.fcProduct;
  const before = _forecastRowState(name);
  const platforms = adminState.forecast.platforms || [];
  const enabled = tr.querySelector('[data-fc="enabled"]').checked;
  const method = tr.querySelector('[data-fc="method"]').value;
  let platform = tr.querySelector('[data-fc="platform"]').value || null;
  let hispanic = tr.querySelector('[data-fc="hispanic_weighted"]').checked;
  if (method === "platform") {
    // Switching a product to platform data with nothing chosen yet: use the platform it would have by default, else the first one.
    if (!platform) platform = (before && before.default && before.default.platform) || (platforms[0] && platforms[0].key) || null;
  } else {
    platform = null; hispanic = false;
  }
  tr.querySelectorAll("[data-fc]").forEach(el => { el.disabled = true; });
  _forecastStatus(tr, "Saving…", false);
  try {
    const res = await fetch("/api/admin/forecast-map", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ product_name: name, enabled, method, platform, hispanic_weighted: hispanic }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(detailText(data.detail, res.statusText));
    applyForecastPayload(data);
    const fresh = document.querySelector(`#fc-body tr[data-fc-product="${CSS.escape(name)}"]`);
    if (fresh) _forecastStatus(fresh, "Saved ✓", false);
  } catch (e) {
    renderForecastRows();        // put the row back to what is actually stored
    const fresh = document.querySelector(`#fc-body tr[data-fc-product="${CSS.escape(name)}"]`);
    if (fresh) _forecastStatus(fresh, e.message, true);
  }
}

async function resetForecastRow(tr) {
  const name = tr.dataset.fcProduct;
  tr.querySelectorAll("[data-fc], [data-fc-reset]").forEach(el => { el.disabled = true; });
  _forecastStatus(tr, "Resetting…", false);
  try {
    const res = await fetch("/api/admin/forecast-map/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ product_name: name }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(detailText(data.detail, res.statusText));
    applyForecastPayload(data);
    const fresh = document.querySelector(`#fc-body tr[data-fc-product="${CSS.escape(name)}"]`);
    if (fresh) _forecastStatus(fresh, "Back to default ✓", false);
  } catch (e) {
    renderForecastRows();
    const fresh = document.querySelector(`#fc-body tr[data-fc-product="${CSS.escape(name)}"]`);
    if (fresh) _forecastStatus(fresh, e.message, true);
  }
}

async function addForecastPlatformProduct(btn) {
  const name = btn.dataset.fcAdd;
  const rate = btn.dataset.fcRate ? `$${btn.dataset.fcRate} ${btn.dataset.fcModel}` : btn.dataset.fcModel;
  if (!confirm(`Add "${name}" to the Product Catalog?\n\nIt starts with a PLACEHOLDER rate (${rate}, $${Number(btn.dataset.fcMin || 0).toLocaleString("en-US")} minimum). Set the real rate in Product Catalog before quoting it to a client.`)) return;
  btn.disabled = true;
  btn.textContent = "Adding…";
  try {
    const res = await fetch("/api/admin/forecast-map/add-product", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ product_name: name }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(detailText(data.detail, res.statusText));
    applyForecastPayload(data);
    adminState.ratesLoaded = false;        // the Product Catalog tab must show the new product next time it opens
  } catch (e) {
    alert("Couldn't add it: " + e.message);
    btn.disabled = false;
    btn.textContent = "Retry: add to the catalog";
  }
}
