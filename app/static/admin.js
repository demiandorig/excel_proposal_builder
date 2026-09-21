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
  rates: [],
  ratesLoaded: false,
  markets: [],
  marketsLoaded: false,
  users: [],
  usersLoaded: false,
  currentUserEmail: null,  // set by loadUsers()'s /api/me call — lets the Users tab hide "disable/delete self" actions
};

document.addEventListener("DOMContentLoaded", () => {
  wireTabs();
  wireSearch();
  wireProposalsControls();
  wireAnalyticsControls();
  wireAddProduct();
  wireEditProduct();
  wireBulkUpload();
  wireMarketConfig();
  wireAddUser();
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
      document.getElementById("tab-markets").classList.toggle("hidden", tab !== "markets");
      document.getElementById("tab-users").classList.toggle("hidden", tab !== "users");
      if (tab === "analytics" && !adminState.analyticsLoaded) loadAnalytics();
      if (tab === "rates" && !adminState.ratesLoaded) loadRates();
      if (tab === "markets" && !adminState.marketsLoaded) loadMarketConfig();
      if (tab === "users" && !adminState.usersLoaded) loadUsers();
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
    _analyticsBarList(data.by_time_unit, "time_unit", "count", u => u.charAt(0).toUpperCase() + u.slice(1));
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
      renderRates(filterRates(document.getElementById("rates-search").value));
    } catch (err) {
      resultEl.innerHTML = `<span class="bur-error">Bulk upload failed: ${escapeHtml(err.message)}</span>`;
    } finally {
      e.target.value = "";  // allow re-selecting the same file name again later
    }
  });
}

function renderBulkUploadResult(body) {
  const resultEl = document.getElementById("bulk-upload-result");
  const parts = [];
  parts.push(`<strong>Processed ${body.total_rows_processed} row${body.total_rows_processed === 1 ? "" : "s"}.</strong>`);
  parts.push(`${body.created.length} added, ${body.updated_as_rate_override.length} rate override${body.updated_as_rate_override.length === 1 ? "" : "s"} updated, ${body.updated_custom_product.length} custom product${body.updated_custom_product.length === 1 ? "" : "s"} replaced.`);
  if (body.errors.length) {
    parts.push(`<div class="bur-error">${body.errors.length} row${body.errors.length === 1 ? "" : "s"} skipped:</div>`);
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
    if (!res.ok) throw new Error(data.detail || res.statusText);
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
      `<tr><td colspan="10" class="admin-empty">Failed to load: ${escapeHtml(e.message)}</td></tr>`;
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
    `<tr><td colspan="10" class="admin-empty"><span class="btn-inline-spinner"></span>Loading…</td></tr>`;
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
    body.innerHTML = `<tr><td colspan="10" class="admin-empty">No proposals match.</td></tr>`;
    return;
  }

  body.innerHTML = list.map(p => `
    <tr>
      <td class="mono">${formatDate(p.generated_at)}</td>
      <td class="mono">${escapeHtml(p.notion_id || "—")}</td>
      <td>${escapeHtml(p.client_name || "—")}</td>
      <td>${escapeHtml(p.requested_by || p.seller_email || "—")}</td>
      <td class="wrap">${escapeHtml(p.proposal_title || p.filename || "—")}</td>
      <td class="mono">${escapeHtml((p.tabs_built || []).join(", "))}</td>
      <td class="mono">${money(p.total_net)}</td>
      <td class="mono muted">${escapeHtml(p.requester_ip || "—")}</td>
      <td class="wrap muted" title="${escapeAttr(p.requester_user_agent || "")}">${escapeHtml(shortenUA(p.requester_user_agent))}</td>
      <td><a class="reopen-link" href="/?reopen=${encodeURIComponent(p.proposal_id)}" target="_blank" rel="noopener">Reopen ↗</a></td>
    </tr>
  `).join("");
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
// Rate overrides
// --------------------------------------------------------------------------

async function loadRates() {
  try {
    const res = await fetch("/api/admin/rates");
    const data = await res.json();
    adminState.rates = data.products || [];
    adminState.ratesLoaded = true;
    renderRates(adminState.rates);
  } catch (e) {
    document.getElementById("rates-body").innerHTML =
      `<tr><td colspan="7" class="admin-empty">Failed to load: ${escapeHtml(e.message)}</td></tr>`;
  }
}

function filterRates(query) {
  const q = query.trim().toLowerCase();
  if (!q) return adminState.rates;
  return adminState.rates.filter(p =>
    p.name.toLowerCase().includes(q) || p.family.toLowerCase().includes(q) ||
    (p.stable_name || "").toLowerCase().includes(q)  // finds a renamed product by its former name too
  );
}

function renderRates(list) {
  const body = document.getElementById("rates-body");
  document.getElementById("rates-count").textContent =
    `${list.length} product${list.length === 1 ? "" : "s"}`;

  if (!list.length) {
    body.innerHTML = `<tr><td colspan="7" class="admin-empty">No products match.</td></tr>`;
    return;
  }

  body.innerHTML = list.map(p => `
    <tr data-product="${escapeAttr(p.stable_name)}" class="${p.is_deleted ? "row-deleted" : ""}">
      <td class="mono">${escapeHtml(p.family)}</td>
      <td class="wrap">
        ${escapeHtml(p.name)}
        ${p.has_override ? '<span class="override-badge">Override</span>' : ""}
        ${p.is_custom ? '<span class="custom-badge">Custom</span>' : ""}
        ${p.is_addon ? '<span class="addon-badge">Add-on</span>' : ""}
        ${p.is_deleted ? '<span class="deleted-badge">Deleted</span>' : ""}
        ${p.name !== p.stable_name ? `<span class="rename-hint" title="Original catalog name — still recognized in old pastes and saved proposals">was: ${escapeHtml(p.stable_name)}</span>` : ""}
      </td>
      <td class="mono">${escapeHtml(p.buying_model)}</td>
      <td>${numInput(p, "base_rate")}</td>
      <td>${numInput(p, "minimum_spend")}</td>
      <td>${numInput(p, "estimated_cpm_for_imps")}</td>
      <td>
        <button class="btn-save-row" data-action="save" data-product="${escapeAttr(p.stable_name)}" ${p.is_deleted ? "disabled" : ""}>Save</button>
        <button class="btn-revert" data-action="revert" data-product="${escapeAttr(p.stable_name)}"
                ${p.has_override ? "" : "disabled"}>Revert</button>
        <button class="btn-secondary" data-action="edit" data-product="${escapeAttr(p.stable_name)}" ${p.is_deleted ? "disabled" : ""}>✎ Edit</button>
        ${p.is_deleted
          ? `<button class="btn-secondary" data-action="restore" data-product="${escapeAttr(p.stable_name)}">↺ Restore</button>`
          : `<button class="btn-delete-row" data-action="delete" data-product="${escapeAttr(p.stable_name)}" data-custom="${p.is_custom ? "1" : "0"}">Delete</button>`}
      </td>
    </tr>
  `).join("");

  body.querySelectorAll('[data-action="save"]').forEach(btn => {
    btn.addEventListener("click", () => onSaveRate(btn.dataset.product));
  });
  body.querySelectorAll('[data-action="revert"]').forEach(btn => {
    btn.addEventListener("click", () => onRevertRate(btn.dataset.product));
  });
  body.querySelectorAll('[data-action="edit"]').forEach(btn => {
    btn.addEventListener("click", () => onOpenEditProduct(btn.dataset.product));
  });
  body.querySelectorAll('[data-action="delete"]').forEach(btn => {
    btn.addEventListener("click", () => onDeleteProduct(btn.dataset.product, btn.dataset.custom === "1"));
  });
  body.querySelectorAll('[data-action="restore"]').forEach(btn => {
    btn.addEventListener("click", () => onRestoreProduct(btn.dataset.product));
  });
}

function numInput(product, field) {
  const value = product[field];
  const catalogValue = product[`catalog_${field}`];
  const isOverridden = product.has_override && value !== catalogValue;
  return `<input type="number" step="0.01" min="0"
            value="${value === null || value === undefined ? "" : value}"
            placeholder="NA"
            class="${isOverridden ? "overridden" : ""}"
            data-field="${field}" />`;
}

async function onSaveRate(productName) {
  const row = document.querySelector(`tr[data-product="${cssEscape(productName)}"]`);
  if (!row) return;
  const btn = row.querySelector('[data-action="save"]');
  btn.disabled = true;
  btn.textContent = "Saving…";

  const payload = { product_name: productName };
  row.querySelectorAll("input[data-field]").forEach(inp => {
    const v = inp.value.trim();
    payload[inp.dataset.field] = v === "" ? null : parseFloat(v);
  });

  try {
    const res = await fetch("/api/admin/rates", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(res.statusText);
    btn.textContent = "Saved ✓";
    await loadRates();  // refresh so the override badge / revert button update
    // Re-apply the search filter after reload
    renderRates(filterRates(document.getElementById("rates-search").value));
  } catch (e) {
    alert("Save failed: " + e.message);
    btn.disabled = false;
    btn.textContent = "Save";
  }
}

async function onRevertRate(productName) {
  if (!confirm(`Revert "${productName}" to its catalog default rate?`)) return;
  try {
    await fetch(`/api/admin/rates/${encodeURIComponent(productName)}`, { method: "DELETE" });
    await loadRates();
    renderRates(filterRates(document.getElementById("rates-search").value));
  } catch (e) {
    alert("Revert failed: " + e.message);
  }
}

async function onDeleteProduct(productName, isCustom) {
  const confirmMsg = isCustom
    ? `Permanently delete "${productName}"? This can't be undone.`
    : `Delete "${productName}"? It'll be hidden from new proposals (and the catalog dropdown), but any proposal that already uses it keeps working, and you can restore it here later.`;
  if (!confirm(confirmMsg)) return;
  try {
    const res = await fetch(`/api/admin/products/${encodeURIComponent(productName)}`, { method: "DELETE" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || res.statusText);
    }
    await loadRates();
    renderRates(filterRates(document.getElementById("rates-search").value));
  } catch (e) {
    alert("Delete failed: " + e.message);
  }
}

async function onRestoreProduct(productName) {
  try {
    const res = await fetch(`/api/admin/products/${encodeURIComponent(productName)}/restore`, { method: "POST" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || res.statusText);
    }
    await loadRates();
    renderRates(filterRates(document.getElementById("rates-search").value));
  } catch (e) {
    alert("Restore failed: " + e.message);
  }
}

// --------------------------------------------------------------------------
// Add a new product
// --------------------------------------------------------------------------

function wireAddProduct() {
  const panel = document.getElementById("add-product-panel");
  const form = document.getElementById("add-product-form");
  const cancelBtn = document.getElementById("add-product-cancel-btn");
  if (!form) return;

  form.addEventListener("submit", onAddProduct);
  cancelBtn.addEventListener("click", () => {
    form.reset();
    document.getElementById("add-product-error").classList.add("hidden");
    panel.open = false;
  });
}

async function onAddProduct(e) {
  e.preventDefault();
  const errEl = document.getElementById("add-product-error");
  errEl.classList.add("hidden");

  const val = id => document.getElementById(id).value.trim();
  const numOrNull = id => {
    const v = val(id);
    return v === "" ? null : parseFloat(v);
  };

  const payload = {
    family: val("np-family"),
    name: val("np-name"),
    short_label: val("np-short-label") || null,
    buying_model: val("np-buying-model"),
    base_rate: numOrNull("np-base-rate"),
    minimum_spend: numOrNull("np-min-spend"),
    estimated_cpm_for_imps: numOrNull("np-est-cpm"),
    sizes: val("np-sizes") || null,
    tech_platform: val("np-tech-platform") || null,
    proposal_description: val("np-description") || null,
    notes: val("np-notes") || null,
    is_addon: document.getElementById("np-is-addon").checked,
  };

  const submitBtn = e.target.querySelector('button[type="submit"]');
  submitBtn.disabled = true;
  submitBtn.textContent = "Adding…";

  try {
    const res = await fetch("/api/admin/products", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || res.statusText);
    }
    e.target.reset();
    document.getElementById("add-product-panel").open = false;
    await loadRates();
    renderRates(filterRates(document.getElementById("rates-search").value));
  } catch (err) {
    errEl.textContent = err.message;
    errEl.classList.remove("hidden");
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Add Product";
  }
}

// --------------------------------------------------------------------------
// Edit an existing product (built-in or custom) — every field, not just
// the 3 the inline rate-table row edits. Reuses the add-product panel's
// visual pattern (a second <details> form) rather than a modal.
// --------------------------------------------------------------------------

function wireEditProduct() {
  const panel = document.getElementById("edit-product-panel");
  const form = document.getElementById("edit-product-form");
  const cancelBtn = document.getElementById("edit-product-cancel-btn");
  if (!form) return;

  form.addEventListener("submit", onSaveEditProduct);
  cancelBtn.addEventListener("click", () => {
    panel.open = false;
    document.getElementById("edit-product-error").classList.add("hidden");
  });
}

function onOpenEditProduct(stableName) {
  const p = adminState.rates.find(r => r.stable_name === stableName);
  if (!p) return;

  document.getElementById("edit-product-name-label").textContent = `— ${p.name}`;
  document.getElementById("edit-product-form").dataset.productName = p.stable_name;
  document.getElementById("ep-family").value = p.family || "";
  document.getElementById("ep-name").value = p.name || "";
  document.getElementById("ep-short-label").value = p.short_label || "";
  document.getElementById("ep-buying-model").value = p.buying_model || "Fixed";
  document.getElementById("ep-base-rate").value = p.base_rate ?? "";
  document.getElementById("ep-min-spend").value = p.minimum_spend ?? "";
  document.getElementById("ep-est-cpm").value = p.estimated_cpm_for_imps ?? "";
  document.getElementById("ep-sizes").value = p.sizes || "";
  document.getElementById("ep-tech-platform").value = p.tech_platform || "";
  document.getElementById("ep-description").value = p.proposal_description || "";
  document.getElementById("ep-notes").value = p.notes || "";
  document.getElementById("ep-is-addon").checked = !!p.is_addon;
  document.getElementById("edit-product-error").classList.add("hidden");

  const panel = document.getElementById("edit-product-panel");
  panel.open = true;
  panel.scrollIntoView({ behavior: "smooth", block: "center" });
}

async function onSaveEditProduct(e) {
  e.preventDefault();
  const errEl = document.getElementById("edit-product-error");
  errEl.classList.add("hidden");

  const val = id => document.getElementById(id).value.trim();
  // Empty = "leave as the catalog default" (same convention the single-
  // field rate editor already uses), not "override to blank" — so an
  // untouched field never accidentally stamps an empty-string override.
  const strOrNull = id => val(id) || null;
  const numOrNull = id => {
    const v = val(id);
    return v === "" ? null : parseFloat(v);
  };

  const productName = e.target.dataset.productName;
  const payload = {
    product_name: productName,
    new_name: strOrNull("ep-name"),
    family: strOrNull("ep-family"),
    short_label: strOrNull("ep-short-label"),
    buying_model: strOrNull("ep-buying-model"),
    base_rate: numOrNull("ep-base-rate"),
    minimum_spend: numOrNull("ep-min-spend"),
    estimated_cpm_for_imps: numOrNull("ep-est-cpm"),
    sizes: strOrNull("ep-sizes"),
    tech_platform: strOrNull("ep-tech-platform"),
    proposal_description: strOrNull("ep-description"),
    notes: strOrNull("ep-notes"),
    is_addon: document.getElementById("ep-is-addon").checked,
  };

  const submitBtn = e.target.querySelector('button[type="submit"]');
  submitBtn.disabled = true;
  submitBtn.textContent = "Saving…";

  try {
    const res = await fetch("/api/admin/products/edit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || res.statusText);
    }
    document.getElementById("edit-product-panel").open = false;
    await loadRates();
    renderRates(filterRates(document.getElementById("rates-search").value));
  } catch (err) {
    errEl.textContent = err.message;
    errEl.classList.remove("hidden");
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Save Changes";
  }
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
    body.innerHTML = `<tr><td colspan="7" class="admin-empty">No markets configured yet.</td></tr>`;
    return;
  }

  body.innerHTML = list.map(m => `
    <tr data-market="${escapeAttr(m.market_key)}">
      <td class="mono">${m.is_default ? "Default (all other markets)" : escapeHtml(m.market_key)}</td>
      <td><input type="text" data-field="address_line1" value="${escapeAttr(m.address_line1)}" placeholder="Street address" /></td>
      <td><input type="text" data-field="address_line2" value="${escapeAttr(m.address_line2)}" placeholder="City, State ZIP" /></td>
      <td><input type="text" data-field="dsc_email" value="${escapeAttr(m.dsc_email)}" placeholder="dsc@entravision.com" /></td>
      <td><input type="text" data-field="dsm_email" value="${escapeAttr(m.dsm_email)}" placeholder="dsm@entravision.com" /></td>
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
    if (inp.dataset.field === "ccs") {
      payload.ccs = inp.value.split(",").map(s => s.trim()).filter(Boolean);
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
