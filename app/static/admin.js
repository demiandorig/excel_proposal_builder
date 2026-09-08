/* ========================================================================
 * Entravision Proposal Builder — Admin Console controller
 * ======================================================================== */

const adminState = {
  proposals: [],
  rates: [],
  ratesLoaded: false,
  markets: [],
  marketsLoaded: false,
};

document.addEventListener("DOMContentLoaded", () => {
  wireTabs();
  wireSearch();
  wireAddProduct();
  wireEditProduct();
  wireBulkUpload();
  wireMarketConfig();
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
      document.getElementById("tab-rates").classList.toggle("hidden", tab !== "rates");
      document.getElementById("tab-markets").classList.toggle("hidden", tab !== "markets");
      if (tab === "rates" && !adminState.ratesLoaded) loadRates();
      if (tab === "markets" && !adminState.marketsLoaded) loadMarketConfig();
    });
  });
}

function wireSearch() {
  document.getElementById("proposals-search").addEventListener("input", e => {
    renderProposals(filterProposals(e.target.value));
  });
  document.getElementById("rates-search").addEventListener("input", e => {
    renderRates(filterRates(e.target.value));
  });
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
  try {
    const res = await fetch("/api/admin/proposals");
    const data = await res.json();
    adminState.proposals = data.proposals || [];
    renderProposals(adminState.proposals);
  } catch (e) {
    document.getElementById("proposals-body").innerHTML =
      `<tr><td colspan="9" class="admin-empty">Failed to load: ${escapeHtml(e.message)}</td></tr>`;
  }
}

function filterProposals(query) {
  const q = query.trim().toLowerCase();
  if (!q) return adminState.proposals;
  return adminState.proposals.filter(p =>
    (p.client_name || "").toLowerCase().includes(q) ||
    (p.seller_email || "").toLowerCase().includes(q) ||
    (p.requested_by || "").toLowerCase().includes(q) ||
    (p.notion_id || "").toLowerCase().includes(q) ||
    (p.proposal_title || "").toLowerCase().includes(q)
  );
}

function renderProposals(list) {
  const body = document.getElementById("proposals-body");
  document.getElementById("proposals-count").textContent =
    `${list.length} proposal${list.length === 1 ? "" : "s"}`;

  if (!list.length) {
    body.innerHTML = `<tr><td colspan="10" class="admin-empty">No proposals generated yet.</td></tr>`;
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
    p.name.toLowerCase().includes(q) || p.family.toLowerCase().includes(q)
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
    <tr data-product="${escapeAttr(p.name)}">
      <td class="mono">${escapeHtml(p.family)}</td>
      <td class="wrap">
        ${escapeHtml(p.name)}
        ${p.has_override ? '<span class="override-badge">Override</span>' : ""}
        ${p.is_custom ? '<span class="custom-badge">Custom</span>' : ""}
        ${p.is_addon ? '<span class="addon-badge">Add-on</span>' : ""}
      </td>
      <td class="mono">${escapeHtml(p.buying_model)}</td>
      <td>${numInput(p, "base_rate")}</td>
      <td>${numInput(p, "minimum_spend")}</td>
      <td>${numInput(p, "estimated_cpm_for_imps")}</td>
      <td>
        <button class="btn-save-row" data-action="save" data-product="${escapeAttr(p.name)}">Save</button>
        <button class="btn-revert" data-action="revert" data-product="${escapeAttr(p.name)}"
                ${p.has_override ? "" : "disabled"}>Revert</button>
        <button class="btn-secondary" data-action="edit" data-product="${escapeAttr(p.name)}">✎ Edit</button>
        ${p.is_custom ? `<button class="btn-delete-row" data-action="delete" data-product="${escapeAttr(p.name)}">Delete</button>` : ""}
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
    btn.addEventListener("click", () => onDeleteProduct(btn.dataset.product));
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

async function onDeleteProduct(productName) {
  if (!confirm(`Permanently delete "${productName}"? This can't be undone.`)) return;
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

function onOpenEditProduct(productName) {
  const p = adminState.rates.find(r => r.name === productName);
  if (!p) return;

  document.getElementById("edit-product-name-label").textContent = `— ${p.name}`;
  document.getElementById("edit-product-form").dataset.productName = p.name;
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
    const data = await res.json();
    adminState.markets = data.markets || [];
    adminState.marketsLoaded = true;
    document.getElementById("base-ccs-input").value = (data.base_ccs || []).join(", ");
    document.getElementById("t1-ccs-input").value = (data.t1_ccs || []).join(", ");
    renderMarketConfig();
  } catch (e) {
    document.getElementById("markets-body").innerHTML =
      `<tr><td colspan="5" class="admin-empty">Failed to load: ${escapeHtml(e.message)}</td></tr>`;
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
    body.innerHTML = `<tr><td colspan="5" class="admin-empty">No markets configured yet.</td></tr>`;
    return;
  }

  body.innerHTML = list.map(m => `
    <tr data-market="${escapeAttr(m.market_key)}">
      <td class="mono">${m.is_default ? "Default (all other markets)" : escapeHtml(m.market_key)}</td>
      <td><input type="text" data-field="address_line1" value="${escapeAttr(m.address_line1)}" placeholder="Street address" /></td>
      <td><input type="text" data-field="address_line2" value="${escapeAttr(m.address_line2)}" placeholder="City, State ZIP" /></td>
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
