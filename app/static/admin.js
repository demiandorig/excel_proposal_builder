/* ========================================================================
 * Entravision Proposal Builder — Admin Console controller
 * ======================================================================== */

const adminState = {
  proposals: [],
  rates: [],
  ratesLoaded: false,
};

document.addEventListener("DOMContentLoaded", () => {
  wireTabs();
  wireSearch();
  wireAddProduct();
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
      if (tab === "rates" && !adminState.ratesLoaded) loadRates();
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
      </td>
      <td class="mono">${escapeHtml(p.buying_model)}</td>
      <td>${numInput(p, "base_rate")}</td>
      <td>${numInput(p, "minimum_spend")}</td>
      <td>${numInput(p, "estimated_cpm_for_imps")}</td>
      <td>
        <button class="btn-save-row" data-action="save" data-product="${escapeAttr(p.name)}">Save</button>
        <button class="btn-revert" data-action="revert" data-product="${escapeAttr(p.name)}"
                ${p.has_override ? "" : "disabled"}>Revert</button>
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
