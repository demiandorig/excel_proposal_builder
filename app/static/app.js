/* ========================================================================
 * Entravision Proposal Builder — front-end controller
 * ======================================================================== */

const state = {
  catalog: null,            // { families: [...], products_by_family: {...} }
  productIndex: {},         // name -> product object (flat lookup)
  parsed: null,             // parsed ProposalRequest dict
  suggestedTabs: null,
  strategyBrief: null,      // confirmed AI strategy brief (or null if skipped)
  roadblocks: null,         // Step 05 AI roadblocks/restrictions result (or null if skipped)
  lineItems: [],            // array of { id, product_name, monthly_budget, months, ... }
  // Keyed by line item `id` — NOT product_name. Two lines can share the same
  // product (e.g. same product, different targeting), so a name-keyed dict
  // would silently collide between them; id is unique per line even then.
  availsData: {},           // id -> { max_imps, max_spend, est_uniques, ... }
  // Step 04's Add-Ons module — fixed-price extras (Services/Measurement
  // catalog families), picked separately from the main line-items table.
  // Keyed by product_name (not id — an add-on is either picked or not,
  // there's no "duplicate with different targeting" concept for these).
  // Proposal-wide: NOT part of the per-tier snapshot pattern below, since
  // the same add-ons apply regardless of which budget option is active.
  addons: {},               // product_name -> amount (presence = picked)
  rateOverrideOpen: new Set(),  // transient UI state — which row indices show the rate-override input
  proposalId: null,
  proposalSummary: null,
  enrichment: null,         // Step 07 AI email content (subjects/bodies) — reprompt-able in place
  // The REAL naming-convention title, once known (from a successful Generate
  // or from reopening a past proposal) — the persistent name bar shows this
  // verbatim instead of the live best-guess preview once it's set. Cleared
  // whenever the planner goes back to Step 02, since editing client name/
  // request type/start date there can change the real title on next Generate.
  finalProposalTitle: null,
  // Tiered budget options (up to 4 — "A".."D"). state.lineItems/availsData
  // ALWAYS hold the currently-active tier's data (same as before tiers
  // existed — no other code needs to change); `tiers` holds a snapshot for
  // every OTHER tier, swapped in/out by switchTier(). See "Tiered budget
  // options" section below for the full read/write contract.
  tiers: [],                // [{ label, lineItems, availsData }] — every tier EXCEPT the active one
  activeTierLabel: "A",
  step: 1,
  // The highest step number reached so far this session — lets the top nav
  // pills be clickable up to (but not past) wherever the wizard has
  // actually gotten to, without letting the planner skip ahead into a step
  // whose data was never populated. Reset only by resetAll().
  furthestStep: 1,
};

const TIER_LABELS = ["A", "B", "C", "D"];

// Unique-enough id for a line item (stable identity across renders/edits,
// used to key avails data independent of product name so duplicate-product
// lines don't collide).
function newLineItemId() {
  return "li_" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
}

// --------------------------------------------------------------------------
// Examples (loaded from textarea, not server)
// --------------------------------------------------------------------------

const EXAMPLE_1 = `Requested by: Carlos Renteria
Salesperson market: Phoenix (Tier 1) (R ana.gomez@entravision.com)
Salesperson email: carlos.renteria@entravision.com
CCs:
Request type: Avails / Estimates Only (I don't need a proposal right now)
────────────
>>> Avails/Presentation/Proposal Page details <<<
Client name: Bill Luke Auto Group
Client website: https://billluke.com/servicedepartment
Agency name:
Agency Fee:
────────────
Start date:
End date:
Total months: 3
Monthly budget:
Tiered budget?: false
Tier #1: | Tier #2:
Tier #3: | Tier #4:
────────────
Chosen campaign goal: Traffic / Drive To Website and Clicks
────────────
Target details:
Language of campaign: English AND Spanish (Separate)
Geo: 15 Mile Radius from 2425 W Camelback Rd, Phoenix AZ 85015
Demo: A21+
Behavioral: Owners of Chrysler, Jeep, Dodge, and Ram vehicles
Contextual: Entertainment & Sports
────────────
Products selected: Connected TV (OTT) - Entravision Plus
CTV/OTT specifics:
Device type: Connected TV (Large Screens) AND OTT (Mobile Devices)
Inventory language: Spanish & English Content targeting Hispanics.
────────────
Additional comments from the salesperson:
Client wants to promote their service department to brand owners in a 15 mile radius around their main store. Looking for Max avails, recommended budget, and they are CPM sensitive.`;

const EXAMPLE_2 = `Requested by: Camilo Arias
Salesperson market: Los Angeles (Tier 1) (R amartindelcampo@entravision.com)
Salesperson email: camilo.arias@entravision.com
CCs:
Request type: Renewal Proposal Request
────────────
## **>>> Avails/Presentation/Proposal Page details <<<**
Client name:
Client website:
Agency name:
Agency Fee:
────────────
Start date:
End date:
Total months:
Monthly budget:
────────────
**Products selected:**
────────────
## **>>> Renewal Request <<<**
> AE or AM Requesting: Account Executive / DSM / SVP
> Type of changes request: Renewal Proposal With Minor Changes Request
> Client: Fronteras Del Norte
> Changes description: Fronteras Del Norte is returning and has increased their monthly budget from $5k to $7.5k. The $5k is currently split evenly with $2.5k focused on Los Angeles and $2.5k on Northern California. I believe the new budget should be $5k Los Angeles and keep the $2.5k in Northern California. I think we should grow Meta and Google SEM instead of adding more digital products. The client wants to add retargeting to the campaign. He also mentioned he was interested in Email marketing.
> Campaign dates: 2026-06-01 - 2026-06-30
> Renewal budget: 7500 | $5k for Los Angeles and $2.5k for Northern California
> Former AE/AM:
> Additional comments: Lets meet if needed!
> Due date: 2026-05-15`;

// --------------------------------------------------------------------------
// Init
// --------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", async () => {
  await loadCatalog();
  wireEvents();
  await maybeReopenProposal();
});

// --------------------------------------------------------------------------
// Reopen an existing proposal (e.g. from the Admin console's "Reopen" link,
// /?reopen={proposal_id}) — pre-fills the wizard instead of starting blank.
// --------------------------------------------------------------------------

async function maybeReopenProposal() {
  const params = new URLSearchParams(window.location.search);
  const reopenId = params.get("reopen");
  if (!reopenId) return;

  try {
    const res = await fetch(`/api/proposal/${encodeURIComponent(reopenId)}/reopen`);
    if (!res.ok) {
      alert("Could not reopen that proposal: " + res.statusText);
      return;
    }
    const data = await res.json();

    state.parsed = data.request || {};
    state.suggestedTabs = null;
    state.strategyBrief = data.strategy_brief || null;
    // Reopening carries the REAL title from when this proposal was last
    // generated — show it verbatim rather than a fresh live-guess.
    state.finalProposalTitle = data.proposal_title || null;
    // Reopened proposals don't carry multi-tier state (pre-dates that
    // feature, or was generated before the reopen_state was extended for
    // it) — reopen always resumes as a single tier "A".
    state.tiers = [];
    state.activeTierLabel = "A";
    const availsData = data.avails_data || {};

    // Restore Add-Ons picks (absent entirely on a proposal generated before
    // this feature existed — defaults to none picked, not an error).
    state.addons = {};
    (data.addons || []).forEach(a => { state.addons[a.product_name] = a.amount; });
    renderAddonsModule();

    // Older saved proposals (before line items carried a stable id) had
    // avails keyed by product_name — assign fresh ids now and carry any
    // such avails entry over so nothing is silently lost on reopen.
    state.lineItems = (data.line_items || []).map(li => {
      const item = { ...li };
      if (!item.id) {
        const newId = newLineItemId();
        if (availsData[item.product_name] && !availsData[newId]) {
          availsData[newId] = availsData[item.product_name];
        }
        item.id = newId;
      }
      return item;
    });
    state.availsData = availsData;

    fillForm(state.parsed);
    const digits = (state.parsed.notion_id || "").replace(/^EVC-/, "");
    document.getElementById("notion-id-input").value = digits;
    document.getElementById("notion-id-pill").textContent = state.parsed.notion_id || "";
    renderMatchedProducts(state.parsed);

    renderLineItems();
    // A reopened proposal already has every step's data (it was fully
    // generated once) — let the nav pills jump anywhere immediately
    // instead of only unlocking as the planner re-visits each step.
    state.furthestStep = 7;
    goToStep(4);  // straight to Curate — the paste/review content is already known
  } catch (e) {
    alert("Reopen failed: " + e.message);
  }
}

async function loadCatalog() {
  const res = await fetch("/api/catalog");
  state.catalog = await res.json();
  // Build flat index
  for (const fam of state.catalog.families) {
    for (const p of state.catalog.products_by_family[fam]) {
      state.productIndex[p.name] = p;
    }
  }
  // Populate the product picker dropdown — add-ons are excluded here, they're
  // not "products" a campaign is built around and have their own module
  // (below the line-items table) instead, with no suggested budget.
  const picker = document.getElementById("product-picker");
  for (const fam of state.catalog.families) {
    const productsInFamily = state.catalog.products_by_family[fam].filter(p => !p.is_addon);
    if (!productsInFamily.length) continue;  // e.g. Services/Measurement are all-addon families
    const group = document.createElement("optgroup");
    group.label = fam;
    for (const p of productsInFamily) {
      const opt = document.createElement("option");
      opt.value = p.name;
      opt.textContent = p.name;
      group.appendChild(opt);
    }
    picker.appendChild(group);
  }
  renderAddonsModule();
}

function wireEvents() {
  document.getElementById("load-example-1").addEventListener("click", () => {
    document.getElementById("notion-input").value = EXAMPLE_1;
    document.getElementById("notion-id-input").value = "48213";
  });
  document.getElementById("load-example-2").addEventListener("click", () => {
    document.getElementById("notion-input").value = EXAMPLE_2;
    document.getElementById("notion-id-input").value = "50127";
  });
  document.getElementById("parse-btn").addEventListener("click", onParse);

  // Notion ID — digits only, max 5
  document.getElementById("notion-id-input").addEventListener("input", (e) => {
    e.target.value = e.target.value.replace(/\D/g, "").slice(0, 5);
  });

  // Logo = reset to step 1 with confirmation
  document.getElementById("logo-reset").addEventListener("click", (e) => {
    e.preventDefault();
    if (state.step === 1 || confirm("Start a new proposal? Your current work will be lost.")) {
      resetAll();
    }
  });

  // Step nav buttons
  document.querySelectorAll("[data-back]").forEach(b => {
    b.addEventListener("click", () => goToStep(parseInt(b.dataset.back)));
  });
  document.querySelectorAll("[data-next]").forEach(b => {
    b.addEventListener("click", () => onNext(parseInt(b.dataset.next)));
  });

  // Step nav PILLS — jump directly to any step already reached. Capture
  // any in-progress edits on the step being left first, same as onNext
  // does, so nothing typed gets silently dropped by jumping away.
  document.querySelectorAll(".step[data-step]").forEach(pill => {
    pill.addEventListener("click", () => {
      const target = parseInt(pill.dataset.step);
      if (target === state.step || target > state.furthestStep) return;
      if (state.step === 2) syncFormToParsed();
      if (state.step === 4) syncLineItemsFromTable();
      if (state.step === 6) syncAvailsFromGrid();
      goToStep(target);
    });
  });

  // Strategy step
  document.getElementById("strategy-skip-btn").addEventListener("click", () => onNext(4));
  document.getElementById("strategy-confirm-btn").addEventListener("click", () => onNext(4));
  document.getElementById("reprompt-btn").addEventListener("click", () => {
    document.getElementById("reprompt-area").classList.remove("hidden");
    document.getElementById("reprompt-btn").style.display = "none";
  });
  document.getElementById("reprompt-cancel-btn").addEventListener("click", () => {
    document.getElementById("reprompt-area").classList.add("hidden");
    document.getElementById("reprompt-btn").style.display = "";
  });
  document.getElementById("reprompt-submit-btn").addEventListener("click", onStrategyReprompt);

  // Roadblocks step
  document.getElementById("roadblocks-skip-btn").addEventListener("click", () => onNext(6));
  document.getElementById("roadblocks-regenerate-btn").addEventListener("click", () => onRoadblocksGenerate());

  // Curation
  document.getElementById("add-product-btn").addEventListener("click", onAddProduct);
  document.getElementById("recommend-btn").addEventListener("click", onRecommend);
  document.getElementById("add-tier-btn").addEventListener("click", () => addTier());

  // Avails — copy from another budget option
  document.getElementById("copy-avails-btn").addEventListener("click", onCopyAvails);

  // Generate
  document.getElementById("generate-btn").addEventListener("click", onGenerate);
  document.getElementById("drive-upload-btn").addEventListener("click", onDriveUpload);

  // Step 07 — reprompt the emails based on the planner's final review
  document.getElementById("email-reprompt-btn").addEventListener("click", () => {
    document.getElementById("email-reprompt-area").classList.remove("hidden");
    document.getElementById("email-reprompt-btn").style.display = "none";
  });
  document.getElementById("email-reprompt-cancel-btn").addEventListener("click", () => {
    document.getElementById("email-reprompt-area").classList.add("hidden");
    document.getElementById("email-reprompt-btn").style.display = "";
    document.getElementById("email-reprompt-input").value = "";
  });
  document.getElementById("email-reprompt-submit-btn").addEventListener("click", onEmailReprompt);

  // Copy-to-clipboard buttons (delegated — buttons may not exist yet)
  document.addEventListener("click", e => {
    const btn = e.target.closest("[data-copy-target]");
    if (!btn) return;
    const target = document.getElementById(btn.dataset.copyTarget);
    if (!target) return;
    navigator.clipboard.writeText(target.textContent).then(() => {
      const orig = btn.textContent;
      btn.textContent = "Copied!";
      setTimeout(() => { btn.textContent = orig; }, 1800);
    }).catch(() => {
      // Fallback for older browsers
      const ta = document.createElement("textarea");
      ta.value = target.textContent;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      btn.textContent = "Copied!";
      setTimeout(() => { btn.textContent = btn.dataset.copyTarget.includes("internal") ? "Copy" : "Copy"; }, 1800);
    });
  });

  // New proposal restart
  document.getElementById("new-proposal-btn").addEventListener("click", () => {
    if (confirm("Start a new proposal? This will clear all current work.")) {
      resetAll();
    }
  });
}

// --------------------------------------------------------------------------
// Reset / new proposal
// --------------------------------------------------------------------------

function resetAll() {
  state.parsed = null;
  state.suggestedTabs = null;
  state.strategyBrief = null;
  state.roadblocks = null;
  state.lineItems = [];
  state.availsData = {};
  state.tiers = [];
  state.activeTierLabel = "A";
  state.proposalId = null;
  state.proposalSummary = null;
  state.enrichment = null;
  state.finalProposalTitle = null;
  state.furthestStep = 1;
  state.addons = {};

  document.getElementById("notion-input").value = "";
  document.getElementById("notion-id-input").value = "";
  document.getElementById("notion-id-pill").textContent = "";
  document.getElementById("result").classList.add("hidden");
  document.getElementById("drive-status").classList.add("hidden");
  document.querySelectorAll("[data-field]").forEach(el => { el.value = ""; });
  document.getElementById("line-items-body").innerHTML = "";
  document.getElementById("avails-grid").innerHTML = "";
  document.getElementById("parse-warnings").classList.add("hidden");
  document.getElementById("matched-products-row").classList.add("hidden");
  _resetStrategyUI();
  _resetRoadblocksUI();
  renderAddonsModule();  // re-render so the checkboxes visually clear too, not just state.addons

  goToStep(1);
}

// --------------------------------------------------------------------------
// Persistent proposal-name bar
//
// Mirrors app/services/ai_enricher.py's build_proposal_title()/_get_doc_type()/
// _to_title_case() to show a live best-guess title while the planner is still
// curating — the REAL title is always computed server-side at Generate time
// (campaign name is AI-inferred there, and a missing Notion ID would only
// then fall back to the sequential counter). If that Python naming logic
// ever changes, mirror the change here too so the two don't drift.
// --------------------------------------------------------------------------

const _AVAILS_ONLY_REQUEST_TYPES = new Set([
  "avails / estimates only (i don't need a proposal right now)",
  "avails / estimates only",
  "avails/estimates only",
  "quick strategic question / need guidance",
  "quick question",
]);
const _MONTH_ABBRS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

function _previewDocType(requestType) {
  const rt = (requestType || "").trim().toLowerCase();
  if (rt.includes("question")) return "Question";
  if (rt.includes("renewal")) return "Digital Renewal Plan";
  if (rt.includes("audit")) return "Digital Research";
  if (_AVAILS_ONLY_REQUEST_TYPES.has(rt)) return "Avails";
  return "Digital Media Proposal";
}

function _previewTitleCase(text) {
  if (!text) return text;
  return text.split(" ").map(w => w ? w[0].toUpperCase() + w.slice(1) : w).join(" ");
}

function buildProposalNamePreview(parsed) {
  if (!parsed) return "";
  const shortId = (parsed.notion_id || "").trim() || "----";
  const campaignName = _previewTitleCase((parsed.client_name || "Campaign").trim()) || "Campaign";
  const docType = _previewDocType(parsed.request_type);
  let monYY;
  const d = parsed.start_date ? new Date(parsed.start_date + "T00:00:00") : new Date();
  if (isNaN(d.getTime())) {
    const now = new Date();
    monYY = _MONTH_ABBRS[now.getMonth()] + String(now.getFullYear()).slice(2);
  } else {
    monYY = _MONTH_ABBRS[d.getMonth()] + String(d.getFullYear()).slice(2);
  }
  return `${shortId} | ${campaignName} | Entravision | ${monYY} | ${docType}`.replace(/ {2,}/g, " ").trim();
}

function updateProposalNameBar() {
  const bar = document.getElementById("proposal-name-bar");
  if (!state.parsed) {
    bar.classList.add("hidden");
    return;
  }
  const title = state.finalProposalTitle || buildProposalNamePreview(state.parsed);
  if (!title) {
    bar.classList.add("hidden");
    return;
  }
  document.getElementById("proposal-name-text").textContent = title;
  bar.classList.remove("hidden");
}

// --------------------------------------------------------------------------
// Step navigation
// --------------------------------------------------------------------------

function goToStep(n) {
  state.step = n;
  state.furthestStep = Math.max(state.furthestStep, n);
  // Editing client name/request type/start date is only possible back on
  // Step 02 — once there, the last-generated title (if any) can no longer
  // be trusted as still-accurate, so drop back to the live preview until
  // the planner generates again.
  if (n === 2) state.finalProposalTitle = null;
  for (let i = 1; i <= 7; i++) {
    document.getElementById(`step-${i}`).classList.toggle("hidden", i !== n);
    const navEl = document.querySelector(`.step[data-step="${i}"]`);
    navEl.classList.toggle("active", i === n);
    // "done" reflects the furthest point reached, not just "before the
    // current step" — otherwise stepping back to review something un-marks
    // every later step as done even though nothing there was undone.
    navEl.classList.toggle("done", i < state.furthestStep);
    // Clickable up to (not past) wherever the wizard has actually gotten
    // to — lets the planner jump back to fix something, or jump forward
    // again to a step they'd already reached, without skipping ahead into
    // a step whose data was never populated. Excludes the current step
    // itself — clicking it would be a no-op, so it shouldn't look clickable.
    navEl.classList.toggle("clickable", i <= state.furthestStep && i !== n);
  }
  updateProposalNameBar();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function onNext(n) {
  // Capture form edits before advancing
  if (state.step === 2) syncFormToParsed();
  if (state.step === 4) syncLineItemsFromTable();
  if (state.step === 6) syncAvailsFromGrid();

  if (n === 3) {
    // Trigger AI strategy brief generation
    goToStep(3);
    onStrategyGenerate();
    return;
  }
  if (n === 4) {
    // Pre-populate budget + line items for Curate step
    const budget = state.parsed.monthly_budget || parseBudgetFromRenewal(state.parsed);
    if (budget) {
      document.getElementById("total-budget-target").value = budget;
    }
    if (state.lineItems.length === 0) {
      state.lineItems = (state.parsed.products_selected || []).map(name => {
        const p = state.productIndex[name];
        return {
          id: newLineItemId(),
          product_name: name,
          monthly_budget: p ? (p.minimum_spend || 0) : 0,
          months: state.parsed.total_months || 3,
          rate_override: null,
          notes_override: null,
          target_override: null,
          target_secondary: null,
          estimated_cpm_override: null,
          is_added_value: false,
        };
      });
      if (budget && state.lineItems.length > 0) {
        distributeBudgetProportionally(state.lineItems, budget);
      }
      renderLineItems();

      // Auto-pre-fill Step 02's Tier #1-4 amounts as separate Step 04
      // options — each starts as the same product mix as Option A, rescaled
      // to that tier's target monthly budget. Guarded by the same
      // lineItems.length===0 check above, so revisiting Step 04 later
      // doesn't re-run this and duplicate/reset options the planner has
      // since edited or removed. Capped at 3 additional options (A + 3 =
      // the app's 4-option max) — a 4th non-blank tier value beyond that
      // has nowhere to go and is skipped.
      if (state.parsed.tiered_budget) {
        const tierTargets = [state.parsed.tier_1, state.parsed.tier_2, state.parsed.tier_3, state.parsed.tier_4]
          .map(v => parseFloat(String(v || "").replace(/[^0-9.]/g, "")))
          .filter(v => !isNaN(v) && v > 0);
        const baseLabel = state.activeTierLabel;  // "A" — addTier() always leaves the newest tier active
        tierTargets.slice(0, 3).forEach(target => {
          if (1 + state.tiers.length < 4) addTier(target);
        });
        if (state.activeTierLabel !== baseLabel) switchTier(baseLabel);  // land back on the base option, not the last one created
      }
    }
  }
  if (n === 5) {
    // Trigger AI roadblocks check
    goToStep(5);
    onRoadblocksGenerate();
    return;
  }
  if (n === 6) renderAvailsGrid();
  if (n === 7) renderGenerateSummary();
  goToStep(n);
}

// Parse a budget number out of renewal_budget field (e.g. "7500 | $5k for LA...")
function parseBudgetFromRenewal(parsed) {
  const rb = parsed.renewal_budget || "";
  if (!rb) return null;
  const m = rb.match(/[\d,]+(\.\d+)?/);
  if (m) return parseFloat(m[0].replace(/,/g, ""));
  return null;
}

// Distribute a total monthly budget across line items proportionally by catalog rate weight
function distributeBudgetProportionally(items, totalBudget) {
  if (!items.length) return;
  // Weight by catalog minimum_spend (proxy for product "size")
  const weights = items.map(li => {
    const p = state.productIndex[li.product_name];
    return p ? Math.max(p.minimum_spend || 1, 1) : 1;
  });
  const totalWeight = weights.reduce((a, b) => a + b, 0);
  items.forEach((li, i) => {
    const share = Math.round((weights[i] / totalWeight) * totalBudget / 50) * 50;
    const p = state.productIndex[li.product_name];
    li.monthly_budget = Math.max(share, p ? (p.minimum_spend || 0) : 0);
  });
  // Adjust last item to make sum exact
  const sum = items.reduce((s, li) => s + li.monthly_budget, 0);
  const diff = totalBudget - sum;
  if (items.length > 0) items[items.length - 1].monthly_budget = Math.max(0, items[items.length - 1].monthly_budget + diff);
}

// --------------------------------------------------------------------------
// Step 1 → 2: Parse
// --------------------------------------------------------------------------

async function onParse() {
  const notionDigits = document.getElementById("notion-id-input").value.trim();
  if (!/^\d{4,5}$/.test(notionDigits)) {
    alert("Enter the Notion Request ID first — a 4 or 5 digit number (e.g. EVC-48213).");
    document.getElementById("notion-id-input").focus();
    return;
  }
  const text = document.getElementById("notion-input").value.trim();
  if (!text) {
    alert("Paste the Notion request first.");
    return;
  }
  const btn = document.getElementById("parse-btn");
  btn.disabled = true;
  btn.textContent = "Parsing…";
  try {
    const res = await fetch("/api/parse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notion_text: text }),
    });
    if (!res.ok) {
      alert("Parse failed: " + res.statusText);
      return;
    }
    const data = await res.json();
    state.parsed = data.request;
    state.parsed.notion_id = "EVC-" + notionDigits;
    state.suggestedTabs = data.suggested_tabs;
    state.lineItems = [];  // reset so step 3 re-populates from fresh parse
    state.availsData = {};
    state.tiers = [];
    state.activeTierLabel = "A";
    fillForm(state.parsed);
    renderWarnings(state.parsed.warnings || []);
    renderMatchedProducts(state.parsed);
    renderSuggestedTabs(data.suggested_tabs);
    document.getElementById("notion-id-pill").textContent = state.parsed.notion_id;
    goToStep(2);
  } finally {
    btn.disabled = false;
    btn.textContent = "Parse →";
  }
}

function fillForm(req) {
  document.querySelectorAll("[data-field]").forEach(el => {
    const f = el.dataset.field;
    if (el.type === "checkbox") {
      el.checked = !!req[f];
      return;
    }
    if (req[f] === null || req[f] === undefined) {
      el.value = "";
    } else {
      el.value = req[f];
    }
  });
}

function syncFormToParsed() {
  document.querySelectorAll("[data-field]").forEach(el => {
    const f = el.dataset.field;
    if (el.type === "checkbox") {
      state.parsed[f] = el.checked;
      return;
    }
    let v = el.value;
    if (el.type === "number" && v !== "") v = parseFloat(v);
    if (v === "" && (el.type === "number")) v = null;
    state.parsed[f] = v;
  });
}

function renderWarnings(warnings) {
  const wrap = document.getElementById("parse-warnings");
  if (!warnings.length) {
    wrap.classList.add("hidden");
    return;
  }
  wrap.classList.remove("hidden");
  wrap.innerHTML = `<strong>Parser notes:</strong><ul>${warnings.map(w => `<li>${escapeHtml(w)}</li>`).join("")}</ul>`;
}

function renderMatchedProducts(req) {
  const row = document.getElementById("matched-products-row");
  const list = document.getElementById("matched-products-list");
  const matched = req.products_selected || [];
  const rawPick = req.products_selected_raw || "";

  if (!matched.length && !rawPick) {
    row.classList.add("hidden");
    return;
  }

  const pills = matched.map(name =>
    `<span class="matched-pill">${escapeHtml(name)}</span>`
  );

  if (rawPick && matched.length === 0) {
    pills.push(`<span class="matched-pill unmatched">No catalog match for: ${escapeHtml(rawPick.substring(0, 60))}</span>`);
  }

  list.innerHTML = pills.join(" ");
  row.classList.remove("hidden");
}

function renderSuggestedTabs(tabs) {
  const active = Object.entries(tabs).filter(([_, v]) => v).map(([k]) => prettyTabName(k));
  document.getElementById("suggested-tabs").textContent = active.join(" · ") || "(none)";
}

function prettyTabName(key) {
  return {
    net: "Proposal A (Net)",
    wsections: "Proposal A (wsections)",
    gross: "Proposal A (Gross)",
    avails_only: "Avails-Only",
    dooh_summary: "DOOH Summary",
    dooh_screenlist: "DOOH Screenlist",
  }[key] || key;
}

// --------------------------------------------------------------------------
// Step 3: AI Strategy Brief
// --------------------------------------------------------------------------

function _resetStrategyUI() {
  document.getElementById("strategy-loading").classList.add("hidden");
  document.getElementById("strategy-error").classList.add("hidden");
  document.getElementById("strategy-brief").classList.add("hidden");
  document.getElementById("reprompt-area").classList.add("hidden");
  document.getElementById("reprompt-btn").style.display = "none";
  document.getElementById("strategy-confirm-btn").style.display = "none";
  document.getElementById("strategy-download-link").classList.add("hidden");
  document.getElementById("strategy-search-note").classList.add("hidden");
  document.getElementById("reprompt-input").value = "";
}

async function onStrategyGenerate(reprompt = null) {
  _resetStrategyUI();
  const loadingEl = document.getElementById("strategy-loading");
  const loadingText = document.getElementById("strategy-loading-text");
  loadingEl.classList.remove("hidden");
  loadingText.textContent = `Researching ${state.parsed.client_name || "client"}…`;

  try {
    const res = await fetch("/api/strategy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request: state.parsed, reprompt }),
    });
    const brief = await res.json();
    loadingEl.classList.add("hidden");

    if (brief.error && !brief.strategy_summary) {
      const errEl = document.getElementById("strategy-error");
      errEl.classList.remove("hidden");
      errEl.innerHTML = `<strong>Strategy brief unavailable:</strong> ${escapeHtml(brief.error)}<br>
        <small>Set <code>OPENAI_API_KEY</code> to enable this step. You can skip and curate manually.</small>`;
      return;
    }

    renderStrategyBrief(brief);
  } catch (e) {
    document.getElementById("strategy-loading").classList.add("hidden");
    const errEl = document.getElementById("strategy-error");
    errEl.classList.remove("hidden");
    errEl.textContent = "Request failed: " + e.message;
  }
}

async function onStrategyReprompt() {
  const text = document.getElementById("reprompt-input").value.trim();
  if (!text) { alert("Enter your correction first."); return; }
  await onStrategyGenerate(text);
}

function renderStrategyBrief(brief) {
  state.strategyBrief = brief;

  const noteEl = document.getElementById("strategy-search-note");
  if (!brief.used_web_search) {
    noteEl.classList.remove("hidden");
    noteEl.textContent = "⚠ " + (brief.error || "Generated without live web search — verify stats before relying on them.");
  } else {
    noteEl.classList.add("hidden");
  }

  setText("brief-client-summary", brief.client_summary || "");
  setText("brief-market-context", brief.market_context || "");
  setText("brief-objectives", brief.objectives_analysis || "");
  setText("brief-strategy-summary", brief.strategy_summary || "");

  // Budget note
  const budget = state.parsed.monthly_budget || parseBudgetFromRenewal(state.parsed) || 0;
  const months = state.parsed.total_months || 3;
  const budgetNote = document.getElementById("brief-budget-note");
  budgetNote.textContent = budget
    ? `Based on $${money(budget).replace("$","")}/mo × ${months} mo = ${money(budget * months)} total`
    : "";

  // Tactics cards
  const tacticsEl = document.getElementById("brief-tactics");
  tacticsEl.innerHTML = "";
  (brief.recommended_tactics || []).forEach(t => {
    const pct = t.suggested_budget_pct || 0;
    const alloc = budget ? Math.round(budget * pct / 100 / 50) * 50 : null;
    const card = document.createElement("div");
    card.className = "tactic-card";
    card.innerHTML = `
      <div class="tactic-header">
        <span class="tactic-family">${escapeHtml(t.product_family)}</span>
        <span class="tactic-pct">${pct}%${alloc ? ` · ~${money(alloc)}/mo` : ""}</span>
      </div>
      <p class="tactic-rationale">${escapeHtml(t.rationale)}</p>
      <p class="tactic-data">📊 ${escapeHtml(t.data_point)} <em class="tactic-citation">(${escapeHtml(t.citation)})</em></p>
      <p class="tactic-advantage">⚡ ${escapeHtml(t.entravision_advantage)}</p>
    `;
    tacticsEl.appendChild(card);
  });

  // Key insights
  const insightsList = document.getElementById("brief-insights");
  insightsList.innerHTML = (brief.key_insights || [])
    .map(i => `<li>${escapeHtml(i)}</li>`).join("");

  document.getElementById("strategy-brief").classList.remove("hidden");
  document.getElementById("reprompt-btn").style.display = "";
  document.getElementById("strategy-confirm-btn").style.display = "";

  const dlLink = document.getElementById("strategy-download-link");
  if (brief.doc_token) {
    dlLink.href = `/api/download-strategy/${brief.doc_token}`;
    dlLink.classList.remove("hidden");
  } else {
    dlLink.classList.add("hidden");
  }
}

// --------------------------------------------------------------------------
// Step 5: AI Roadblocks / Restrictions Check
// --------------------------------------------------------------------------

function _resetRoadblocksUI() {
  document.getElementById("roadblocks-loading").classList.add("hidden");
  document.getElementById("roadblocks-error").classList.add("hidden");
  document.getElementById("roadblocks-content").classList.add("hidden");
  document.getElementById("roadblocks-regenerate-btn").style.display = "none";
  document.getElementById("roadblocks-download-link").classList.add("hidden");
  document.getElementById("roadblocks-search-note").classList.add("hidden");
}

async function onRoadblocksGenerate() {
  _resetRoadblocksUI();
  const loadingEl = document.getElementById("roadblocks-loading");
  loadingEl.classList.remove("hidden");
  document.getElementById("roadblocks-loading-text").textContent =
    `Searching platform policies for ${state.parsed.client_name || "this client"}…`;

  try {
    // Covers every product across every budget option — a tier-B-only
    // product still needs its platform restrictions checked even while
    // tier A is the active tab.
    const unionByProduct = new Map();
    allTiersForSubmit().forEach(t => (t.line_items || []).forEach(li => {
      if (!unionByProduct.has(li.product_name)) unionByProduct.set(li.product_name, li);
    }));

    const res = await fetch("/api/roadblocks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        request: state.parsed,
        line_items: [...unionByProduct.values()],
        strategy_brief: state.strategyBrief || null,
      }),
    });
    const data = await res.json();
    loadingEl.classList.add("hidden");

    if (data.error && !data.product_roadblocks?.length) {
      const errEl = document.getElementById("roadblocks-error");
      errEl.classList.remove("hidden");
      errEl.innerHTML = `<strong>Roadblocks check unavailable:</strong> ${escapeHtml(data.error)}<br>
        <small>You can skip this step and continue.</small>`;
      return;
    }

    renderRoadblocks(data);
  } catch (e) {
    loadingEl.classList.add("hidden");
    const errEl = document.getElementById("roadblocks-error");
    errEl.classList.remove("hidden");
    errEl.textContent = "Request failed: " + e.message;
  }
}

function renderRoadblocks(data) {
  state.roadblocks = data;

  setText("roadblocks-summary", data.overall_summary || "");

  const noteEl = document.getElementById("roadblocks-search-note");
  if (!data.used_web_search) {
    noteEl.classList.remove("hidden");
    noteEl.textContent = "⚠ " + (data.error || "Generated without live web search — verify against current platform policies.");
  }

  const RISK_ORDER = { high: 0, medium: 1, low: 2 };
  const cards = document.getElementById("roadblocks-cards");
  const items = [...(data.product_roadblocks || [])].sort(
    (a, b) => (RISK_ORDER[a.risk_level] ?? 3) - (RISK_ORDER[b.risk_level] ?? 3)
  );
  cards.innerHTML = items.map(item => `
    <div class="roadblock-card risk-${escapeAttr(item.risk_level || "low")}">
      <div class="roadblock-card-head">
        <span class="roadblock-product">${escapeHtml(item.product_name)}</span>
        <span class="risk-badge risk-${escapeAttr(item.risk_level || "low")}">${escapeHtml((item.risk_level || "low").toUpperCase())} RISK</span>
      </div>
      ${(item.risks || []).map(r => `
        <div class="roadblock-risk">
          <p class="roadblock-issue">⚠ ${escapeHtml(r.issue)}</p>
          <p class="roadblock-detail">${escapeHtml(r.detail)}</p>
          ${r.source ? `<p class="roadblock-source">Source: ${escapeHtml(r.source)}</p>` : ""}
        </div>
      `).join("")}
      ${item.recommended_mitigation ? `<p class="roadblock-mitigation"><strong>Mitigation:</strong> ${escapeHtml(item.recommended_mitigation)}</p>` : ""}
    </div>
  `).join("") || `<p class="roadblocks-empty">No specific roadblocks found for this product mix.</p>`;

  document.getElementById("roadblocks-content").classList.remove("hidden");
  document.getElementById("roadblocks-regenerate-btn").style.display = "";

  const dlLink = document.getElementById("roadblocks-download-link");
  if (data.doc_token) {
    dlLink.href = `/api/download-roadblocks/${data.doc_token}`;
    dlLink.classList.remove("hidden");
  } else {
    dlLink.classList.add("hidden");
  }
}

// --------------------------------------------------------------------------
// Tiered budget options (up to 4 — "A".."D")
//
// state.lineItems / state.availsData ALWAYS hold the currently-ACTIVE
// tier's data — every existing curation/avails function keeps working
// unmodified, exactly as before this feature existed. state.tiers holds a
// snapshot for every OTHER (inactive) tier. switchTier() swaps the active
// buffer with a stored snapshot; addTier()/removeTier() add or drop a
// snapshot. allTiersForSubmit() flattens both into one array, in label
// order, for sending to /api/generate.
// --------------------------------------------------------------------------

function allTiersForSubmit() {
  return [
    { label: state.activeTierLabel, line_items: state.lineItems, avails_data: state.availsData },
    ...state.tiers.map(t => ({ label: t.label, line_items: t.lineItems, avails_data: t.availsData })),
  ].sort((a, b) => a.label.localeCompare(b.label));
}

function switchTier(label) {
  if (label === state.activeTierLabel) return;
  const idx = state.tiers.findIndex(t => t.label === label);
  if (idx === -1) return;
  const target = state.tiers[idx];

  // Replace the target's snapshot (about to become active) with a fresh
  // snapshot of the tier we're leaving — one swap, order doesn't matter
  // since every lookup here is by label, not position.
  state.tiers.splice(idx, 1, { label: state.activeTierLabel, lineItems: state.lineItems, availsData: state.availsData });

  state.activeTierLabel = label;
  state.lineItems = target.lineItems;
  state.availsData = target.availsData;
  state.rateOverrideOpen.clear();

  renderLineItems();
  renderAvailsGrid();
}

// targetBudget: when given, the clone's line-item budgets are rescaled to
// sum to it (used for auto-pre-filling Step 02's Tier #1-4 amounts as
// options); omitted (manual "+ Add Option" click), the clone just keeps
// the source tier's current budgets unchanged, for the planner to adjust.
function addTier(targetBudget) {
  const totalTiers = 1 + state.tiers.length;
  if (totalTiers >= 4) return;
  const usedLabels = new Set([state.activeTierLabel, ...state.tiers.map(t => t.label)]);
  const nextLabel = TIER_LABELS.find(l => !usedLabels.has(l));
  if (!nextLabel) return;

  // Snapshot the tier we're leaving active...
  state.tiers.push({ label: state.activeTierLabel, lineItems: state.lineItems, availsData: state.availsData });

  // ...then make the NEW tier active, starting as a clone of it — "Add
  // Option" copies the current mix so the planner adjusts from there,
  // rather than starting from a blank curation table.
  const idMap = {};
  const clonedItems = state.lineItems.map(li => {
    const newId = newLineItemId();
    idMap[li.id] = newId;
    return { ...li, id: newId };
  });
  const clonedAvails = {};
  Object.keys(state.availsData).forEach(oldId => {
    if (idMap[oldId]) clonedAvails[idMap[oldId]] = { ...state.availsData[oldId] };
  });
  if (targetBudget) distributeBudgetProportionally(clonedItems, targetBudget);

  state.activeTierLabel = nextLabel;
  state.lineItems = clonedItems;
  state.availsData = clonedAvails;
  state.rateOverrideOpen.clear();

  renderLineItems();
  renderAvailsGrid();
}

function removeTier(label) {
  const totalTiers = 1 + state.tiers.length;
  if (totalTiers <= 1) return;  // always keep at least one option

  if (label === state.activeTierLabel) {
    // Switch to some other tier first so there's always an active buffer.
    const fallback = state.tiers[0];
    if (!fallback) return;
    switchTier(fallback.label);
  }
  const idx = state.tiers.findIndex(t => t.label === label);
  if (idx !== -1) state.tiers.splice(idx, 1);

  renderLineItems();
  renderAvailsGrid();
}

function renderAllTierTabStrips() {
  renderTierTabStrip("tier-tabs", { removable: true });
  renderTierTabStrip("tier-tabs-avails", { removable: false });

  const totalTiers = 1 + state.tiers.length;
  const multiTier = totalTiers > 1;

  const addBtn = document.getElementById("add-tier-btn");
  if (addBtn) addBtn.style.display = totalTiers >= 4 ? "none" : "";

  const hint = document.getElementById("tier-switcher-hint");
  if (hint) hint.classList.toggle("hidden", !multiTier);

  const availsWrap = document.getElementById("tier-switcher-avails-wrap");
  if (availsWrap) availsWrap.classList.toggle("hidden", !multiTier);

  // Avails-specific hint — names which option is active right now, since
  // silently editing the wrong (or only) tab is exactly how an option ends
  // up shipping with no avails at all.
  const availsHint = document.getElementById("tier-switcher-hint-avails");
  if (availsHint) availsHint.classList.toggle("hidden", !multiTier);
  const activeLabelEl = document.getElementById("tier-switcher-active-label");
  if (activeLabelEl) activeLabelEl.textContent = `Option ${state.activeTierLabel}`;

  // "Copy avails from" dropdown — every OTHER tier, so copying is one click.
  const copySource = document.getElementById("copy-avails-source");
  if (copySource) {
    const otherLabels = state.tiers.map(t => t.label).sort();
    copySource.innerHTML = otherLabels.map(l => `<option value="${l}">Option ${l}</option>`).join("");
    copySource.parentElement.classList.toggle("hidden", !multiTier || otherLabels.length === 0);
  }
}

// Copies avails from another tier into the currently-active one, matched by
// product name (tiers don't share line-item ids). Only fills line items
// that don't already have avails entered — won't clobber anything the
// planner already typed for this option.
function onCopyAvails() {
  const sourceLabel = document.getElementById("copy-avails-source").value;
  if (!sourceLabel) return;
  const source = state.tiers.find(t => t.label === sourceLabel);
  if (!source) return;

  const sourceAvailsByProduct = new Map();
  source.lineItems.forEach(li => {
    const avail = source.availsData[li.id];
    if (avail && (avail.max_imps != null || avail.max_spend != null) && !sourceAvailsByProduct.has(li.product_name)) {
      sourceAvailsByProduct.set(li.product_name, avail);
    }
  });

  let copiedCount = 0;
  state.lineItems.forEach(li => {
    const existing = state.availsData[li.id];
    const hasExisting = existing && (existing.max_imps != null || existing.max_spend != null);
    const match = sourceAvailsByProduct.get(li.product_name);
    if (!hasExisting && match) {
      state.availsData[li.id] = { ...match };
      copiedCount++;
    }
  });

  renderAvailsGrid();
  if (copiedCount === 0) {
    alert(`No avails to copy — Option ${sourceLabel} has nothing entered for products in Option ${state.activeTierLabel} (or they're already filled in here).`);
  }
}

function renderTierTabStrip(containerId, opts) {
  const tabsEl = document.getElementById(containerId);
  if (!tabsEl) return;
  const totalTiers = 1 + state.tiers.length;
  const allLabels = [state.activeTierLabel, ...state.tiers.map(t => t.label)].sort();

  tabsEl.innerHTML = allLabels.map(label => `
    <button type="button" class="tier-tab ${label === state.activeTierLabel ? "active" : ""}" data-tier="${label}">
      Option ${label}
      ${opts.removable && totalTiers > 1 ? `<span class="tier-tab-remove" data-tier-remove="${label}" title="Remove this option">×</span>` : ""}
    </button>
  `).join("");

  tabsEl.querySelectorAll(".tier-tab").forEach(btn => {
    btn.addEventListener("click", (e) => {
      if (e.target.closest("[data-tier-remove]")) return;
      switchTier(btn.dataset.tier);
    });
  });
  if (opts.removable) {
    tabsEl.querySelectorAll("[data-tier-remove]").forEach(el => {
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        if (!confirm(`Remove Option ${el.dataset.tierRemove}? This can't be undone.`)) return;
        removeTier(el.dataset.tierRemove);
      });
    });
  }
}

// --------------------------------------------------------------------------
// Step 4: Curation
// --------------------------------------------------------------------------

function renderLineItems() {
  renderAllTierTabStrips();
  const tbody = document.getElementById("line-items-body");
  tbody.innerHTML = "";
  state.lineItems.forEach((li, idx) => {
    const p = state.productIndex[li.product_name] || {};
    const tr = document.createElement("tr");
    const minSpend = p.minimum_spend || 0;
    // Added Value: a $0 (or below-minimum) budget is deliberate here, not
    // an oversight — don't flag it.
    const belowMin = !li.is_added_value && li.monthly_budget < minSpend;
    const rateOpen = state.rateOverrideOpen.has(idx) || li.rate_override != null || li.estimated_cpm_override != null;
    // Fixed-model products (Meta, YouTube, TikTok, LinkedIn, Spotify,
    // Branded Content, ...) have no real per-unit rate to override — their
    // RATE column instead edits the ESTIMATED CPM that drives the "Est. $"
    // impressions calc in Step 06/the export. Kept visually distinct
    // ("Est. $X CPM", not just "$X CPM") so it's never mistaken for a real
    // billing rate the way a bare number would be.
    const isFixedModel = (p.pricing_model || "").toUpperCase() === "FIXED";
    const effectiveCpm = li.estimated_cpm_override != null ? li.estimated_cpm_override : p.estimated_cpm_for_imps;

    // Draggable at the ROW level (native HTML5 drag-and-drop needs the
    // dragged element itself to carry `draggable`), but the dragstart
    // handler below only lets the drag actually begin when it started on
    // the handle cell — so clicking/selecting text anywhere else in the
    // row (budget input, target textarea, etc.) behaves normally.
    tr.draggable = true;
    tr.dataset.idx = idx;
    tr.className = "line-item-row";

    tr.innerHTML = `
      <td class="col-drag"><span class="drag-handle" title="Drag to reorder">⠿</span></td>
      <td class="idx">${idx + 1}</td>
      <td class="product">
        ${escapeHtml(li.product_name)}
        <span class="family-tag">${escapeHtml(p.family || "")}</span>
      </td>
      <td class="model">${escapeHtml(p.pricing_model || "—")}</td>
      <td class="rate-cell">
        ${rateOpen ? (isFixedModel ? `
          <input type="number" step="0.5" min="0" class="rate-override-input est-cpm-input"
                 placeholder="${p.estimated_cpm_for_imps != null ? "Catalog: $" + p.estimated_cpm_for_imps : "No catalog estimate"}"
                 value="${li.estimated_cpm_override != null ? li.estimated_cpm_override : ""}"
                 data-idx="${idx}" data-key="estimated_cpm_override" />
          <button class="btn-rate-reset" data-idx="${idx}" data-field="estimated_cpm_override" title="Revert to catalog estimate">×</button>
        ` : `
          <input type="number" step="0.01" min="0" class="rate-override-input"
                 placeholder="${formatRate(p)}"
                 value="${li.rate_override != null ? li.rate_override : ""}"
                 data-idx="${idx}" data-key="rate_override" />
          <button class="btn-rate-reset" data-idx="${idx}" data-field="rate_override" title="Revert to catalog rate">×</button>
        `) : (isFixedModel ? `
          <span class="rate-display est-cpm-display">${effectiveCpm != null ? `Est. $${effectiveCpm} CPM` : "No estimate"}</span>
          <button class="btn-rate-override" data-idx="${idx}" title="Set an estimated CPM for the impressions calc (not a real billing rate)">✎</button>
        ` : `
          <span class="rate-display">${formatRate(p)}</span>
          <button class="btn-rate-override" data-idx="${idx}" title="Override this rate">✎</button>
        `)}
      </td>
      <td class="min">${money(minSpend)}</td>
      <td class="col-budget">
        <input type="number" step="50" min="0" value="${li.monthly_budget}"
               class="${belowMin ? "below-min" : ""}"
               data-idx="${idx}" data-key="monthly_budget" />
        <label class="added-value-toggle">
          <input type="checkbox" data-idx="${idx}" ${li.is_added_value ? "checked" : ""} data-added-value-toggle />
          Added Value ($0 OK)
        </label>
      </td>
      <td class="col-months">
        <input type="number" step="1" min="1" value="${li.months}"
               data-idx="${idx}" data-key="months" />
      </td>
      <td class="col-target">
        <textarea rows="2" placeholder="(catalog default — describe the audience)"
               data-idx="${idx}" data-key="target_override">${escapeHtml(li.target_override || "")}</textarea>
        <label class="secondary-toggle">
          <input type="checkbox" data-idx="${idx}" ${li.target_secondary != null ? "checked" : ""} data-secondary-toggle />
          + Secondary audience
        </label>
        ${li.target_secondary != null ? `
          <textarea rows="2" class="secondary-target-input" placeholder="e.g. Spanish-speaking A18+ (for added scale)"
                 data-idx="${idx}" data-key="target_secondary">${escapeHtml(li.target_secondary || "")}</textarea>
        ` : ""}
      </td>
      <td class="col-note">
        <input type="text" placeholder="(no note)"
               value="${escapeHtml(li.notes_override || "")}"
               data-idx="${idx}" data-key="notes_override" />
      </td>
      <td class="col-row-actions">
        <button class="btn-duplicate" data-idx="${idx}" title="Duplicate this line (e.g. same product, different targeting)">⧉</button>
        <button class="btn-remove" data-idx="${idx}" title="Remove">×</button>
      </td>
    `;
    tbody.appendChild(tr);
  });
  // Wire row events
  tbody.querySelectorAll("input:not([data-secondary-toggle]):not([data-added-value-toggle]), textarea").forEach(inp => {
    inp.addEventListener("input", onLineItemEdit);
  });
  tbody.querySelectorAll("[data-secondary-toggle]").forEach(cb => {
    cb.addEventListener("change", () => onToggleSecondaryTarget(parseInt(cb.dataset.idx)));
  });
  tbody.querySelectorAll("[data-added-value-toggle]").forEach(cb => {
    cb.addEventListener("change", () => {
      const idx = parseInt(cb.dataset.idx);
      state.lineItems[idx].is_added_value = cb.checked;
      renderLineItems();  // refreshes the below-minimum highlight immediately
    });
  });
  tbody.querySelectorAll(".btn-duplicate").forEach(btn => {
    btn.addEventListener("click", () => onDuplicateLineItem(parseInt(btn.dataset.idx)));
  });
  tbody.querySelectorAll(".btn-remove").forEach(btn => {
    btn.addEventListener("click", () => {
      const idx = parseInt(btn.dataset.idx);
      const removed = state.lineItems[idx];
      if (removed) delete state.availsData[removed.id];
      state.lineItems.splice(idx, 1);
      state.rateOverrideOpen.clear();  // indices shift on removal — avoid pointing at the wrong row
      renderLineItems();
    });
  });
  tbody.querySelectorAll(".btn-rate-override").forEach(btn => {
    btn.addEventListener("click", () => {
      state.rateOverrideOpen.add(parseInt(btn.dataset.idx));
      renderLineItems();
    });
  });
  tbody.querySelectorAll(".btn-rate-reset").forEach(btn => {
    btn.addEventListener("click", () => {
      const idx = parseInt(btn.dataset.idx);
      // Which field this clears depends on whether the row was in real-rate
      // mode or estimated-CPM mode when the reset button was rendered.
      state.lineItems[idx][btn.dataset.field] = null;
      state.rateOverrideOpen.delete(idx);
      renderLineItems();
    });
  });
  wireLineItemDrag(tbody);
  updateTotals();
}

// --------------------------------------------------------------------------
// Drag-to-reorder line items (Step 04). Native HTML5 drag-and-drop, no
// library — `draggable` sits on the <tr> (required: the browser only drags
// the element that actually carries the attribute), but dragstart bails
// out unless it began on the grip handle, so normal clicks/selection in
// the row's own inputs and textareas aren't hijacked into a drag.
// --------------------------------------------------------------------------

function wireLineItemDrag(tbody) {
  const rows = tbody.querySelectorAll("tr.line-item-row");

  const clearDropIndicators = () => {
    rows.forEach(r => r.classList.remove("drag-over-top", "drag-over-bottom"));
  };

  rows.forEach(row => {
    row.addEventListener("dragstart", e => {
      if (!e.target.closest(".drag-handle")) {
        e.preventDefault();
        return;
      }
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", row.dataset.idx);
      row.classList.add("dragging");
    });

    row.addEventListener("dragover", e => {
      e.preventDefault();  // required for drop to fire on this element
      const rect = row.getBoundingClientRect();
      const insertAfter = e.clientY > rect.top + rect.height / 2;
      clearDropIndicators();
      row.classList.add(insertAfter ? "drag-over-bottom" : "drag-over-top");
    });

    row.addEventListener("drop", e => {
      e.preventDefault();
      clearDropIndicators();
      const fromIdx = parseInt(e.dataTransfer.getData("text/plain"));
      const dropOnIdx = parseInt(row.dataset.idx);
      if (isNaN(fromIdx) || isNaN(dropOnIdx)) return;
      const rect = row.getBoundingClientRect();
      const insertAfter = e.clientY > rect.top + rect.height / 2;
      moveLineItem(fromIdx, dropOnIdx, insertAfter);
    });

    row.addEventListener("dragend", () => {
      row.classList.remove("dragging");
      clearDropIndicators();
    });
  });
}

function moveLineItem(fromIdx, dropOnIdx, insertAfter) {
  // `target` is the position (in the ORIGINAL, pre-removal array) before
  // which the dragged item should land. Removing the dragged item first
  // shifts everything after it left by one — so if the target sits after
  // where the item used to be, that shift has to be un-done by one before
  // re-inserting, or the item lands one slot further than intended.
  let target = insertAfter ? dropOnIdx + 1 : dropOnIdx;
  if (fromIdx < target) target -= 1;
  if (target === fromIdx) return;  // dropped back where it started

  const [item] = state.lineItems.splice(fromIdx, 1);
  state.lineItems.splice(target, 0, item);
  state.rateOverrideOpen.clear();  // indices shift — avoid pointing at the wrong row
  renderLineItems();
}

function onLineItemEdit(e) {
  const idx = parseInt(e.target.dataset.idx);
  const key = e.target.dataset.key;
  let v = e.target.value;
  if (e.target.type === "number") {
    // rate_override / estimated_cpm_override are optional — an emptied
    // field means "no override, fall back to the catalog default", not 0.
    // Required numeric fields (monthly_budget, months) fall back to 0 so
    // the payload sent to the backend always stays a valid number.
    const isOptionalOverride = key === "rate_override" || key === "estimated_cpm_override";
    v = v === "" ? (isOptionalOverride ? null : 0) : parseFloat(v);
  }
  state.lineItems[idx][key] = v;
  updateTotals();
  if (key === "monthly_budget") {
    const li = state.lineItems[idx];
    const p = state.productIndex[li.product_name] || {};
    const minSpend = p.minimum_spend || 0;
    e.target.classList.toggle("below-min", !li.is_added_value && (v || 0) < minSpend);
  }
}

// Secondary audience (e.g. a broader look-alike layered on a narrow primary
// intent segment, for added scale/avails) — a per-line checkbox that
// reveals a second textarea. Uses `null` vs "" (not a boolean flag) to mean
// "no secondary" vs "secondary enabled, currently blank" so a blank-but-
// enabled field doesn't disappear the moment the planner clears it while typing.
function onToggleSecondaryTarget(idx) {
  const li = state.lineItems[idx];
  if (!li) return;
  li.target_secondary = li.target_secondary == null ? "" : null;
  renderLineItems();
}

function syncLineItemsFromTable() {
  // No-op: state.lineItems is already in sync via onLineItemEdit
}

function updateTotals() {
  const monthly = state.lineItems.reduce((s, li) => s + (li.monthly_budget || 0), 0);
  const flight = state.lineItems.reduce((s, li) => s + (li.monthly_budget || 0) * (li.months || 1), 0);
  document.getElementById("monthly-total").textContent = money(monthly);
  document.getElementById("flight-total").textContent = money(flight);
}

function onAddProduct() {
  const picker = document.getElementById("product-picker");
  const name = picker.value;
  if (!name) return;
  const p = state.productIndex[name];
  const budget = state.parsed?.monthly_budget || parseBudgetFromRenewal(state.parsed) || 0;
  state.lineItems.push({
    id: newLineItemId(),
    product_name: name,
    monthly_budget: p.minimum_spend || 0,
    months: state.parsed?.total_months || 3,
    rate_override: null,
    notes_override: null,
    target_override: null,
    target_secondary: null,
    estimated_cpm_override: null,
    is_added_value: false,
  });
  picker.value = "";
  renderLineItems();
}

// --------------------------------------------------------------------------
// Step 4: Add-Ons module — fixed-price extras (Services/Measurement
// catalog families), checked on/off separately from the main product mix.
// No suggested budget: the price defaults to the catalog's minimum_spend
// (a flat fee, even for the one CPP-modeled add-on — add-ons don't need a
// rate-times-volume calc, just an editable flat number) and is fully
// planner-editable per proposal. Proposal-wide, so this renders once (on
// catalog load) rather than on every tier switch/line-item re-render.
// --------------------------------------------------------------------------

function renderAddonsModule() {
  const list = document.getElementById("addons-list");
  if (!list || !state.catalog) return;

  const addonProducts = state.catalog.families
    .flatMap(fam => state.catalog.products_by_family[fam])
    .filter(p => p.is_addon);

  if (!addonProducts.length) {
    list.innerHTML = `<p class="addons-empty">No add-ons configured yet — an admin can add some from the Rates tab.</p>`;
    return;
  }

  list.innerHTML = addonProducts.map(p => {
    const picked = Object.prototype.hasOwnProperty.call(state.addons, p.name);
    const amount = picked ? state.addons[p.name] : (p.minimum_spend || 0);
    return `
      <label class="addon-row">
        <input type="checkbox" data-addon-toggle="${escapeHtml(p.name)}" ${picked ? "checked" : ""} />
        <span class="addon-info">
          <span class="addon-name">${escapeHtml(p.name)} <span class="family-tag">${escapeHtml(p.family)}</span></span>
          ${p.description ? `<span class="addon-desc">${escapeHtml(p.description)}</span>` : ""}
        </span>
        <span class="addon-price">
          <span class="addon-price-prefix">$</span>
          <input type="number" step="1" min="0" value="${amount}" ${picked ? "" : "disabled"}
                 data-addon-amount="${escapeHtml(p.name)}" />
        </span>
      </label>
    `;
  }).join("");

  list.querySelectorAll("[data-addon-toggle]").forEach(cb => {
    cb.addEventListener("change", () => onToggleAddon(cb.dataset.addonToggle, cb.checked));
  });
  list.querySelectorAll("[data-addon-amount]").forEach(inp => {
    inp.addEventListener("input", () => {
      const name = inp.dataset.addonAmount;
      if (Object.prototype.hasOwnProperty.call(state.addons, name)) {
        state.addons[name] = parseFloat(inp.value) || 0;
      }
    });
  });
}

function onToggleAddon(name, isPicked) {
  if (isPicked) {
    const p = state.productIndex[name];
    state.addons[name] = p ? (p.minimum_spend || 0) : 0;
  } else {
    delete state.addons[name];
  }
  renderAddonsModule();
}

function onDuplicateLineItem(idx) {
  const original = state.lineItems[idx];
  if (!original) return;
  const copy = { ...original, id: newLineItemId() };
  // Carry over any avails already entered for the original line, so
  // duplicating a filled-in row for a targeting variant doesn't lose them.
  if (state.availsData[original.id]) {
    state.availsData[copy.id] = { ...state.availsData[original.id] };
  }
  state.lineItems.splice(idx + 1, 0, copy);
  state.rateOverrideOpen.clear();  // indices shift — avoid pointing at the wrong row
  renderLineItems();
}

async function onRecommend() {
  const budget = parseFloat(document.getElementById("total-budget-target").value);
  if (!budget || budget <= 0) {
    alert("Enter a target monthly budget first.");
    return;
  }
  const res = await fetch("/api/recommend", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      request: state.parsed,
      monthly_budget: budget,
      strategy_brief: state.strategyBrief || null,
    }),
  });
  if (!res.ok) {
    alert("Recommend failed: " + res.statusText);
    return;
  }
  const data = await res.json();
  state.lineItems = data.line_items.map(li => ({ id: newLineItemId(), ...li }));
  state.availsData = {};  // previous avails were keyed to the old line items' ids
  renderLineItems();
}

// --------------------------------------------------------------------------
// Step 4: Avails
// --------------------------------------------------------------------------

// Bidirectional avails calc, mirroring the AdFlo Excel formulas:
//   CPM:   spend = imps * rate / 1000        (exact — not an estimate)
//   CPP:   spend = imps * rate                (exact — not an estimate)
//   Fixed / estimated-CPM products: spend = imps * est_cpm / 1000  (an estimate)
//   else:  no calc possible (planner enters both by hand)
// Each function returns {value, estimated} or null when the model doesn't support it.
// Step 04's per-line estimated-CPM override (li.estimated_cpm_override)
// takes precedence over the catalog's own estimated_cpm_for_imps — applied
// by building a "virtual" product with the override baked in, so
// calcMaxSpendFromImps/calcMaxImpsFromSpend/computeSovPct don't need their
// own override-handling logic duplicated three times.
function _effectiveProduct(li, p) {
  if (li && li.estimated_cpm_override != null) {
    return { ...p, estimated_cpm_for_imps: li.estimated_cpm_override };
  }
  return p;
}

// --------------------------------------------------------------------------
// Avg. frequency = Max Imps / Est. Uniques. A 4th avails field, editable
// and fully interchangeable with the other two: entering any two of
// {impressions, uniques, frequency} calculates the third, mirroring the
// existing imps<->spend basis pattern but across three variables instead
// of two. Defaults to 5.5 for the same "estimated CPM" product pool as the
// Step 04 estimated-CPM editor (Meta, YouTube, TikTok, LinkedIn, Spotify,
// Branded Content, ...) — there's no meaningful average-frequency baseline
// to assume for a real CPM/CPP product, so those start blank.
// --------------------------------------------------------------------------

function _isEstimateCpmProduct(li, p) {
  const eff = _effectiveProduct(li, p);
  return (eff.pricing_model || "").toUpperCase() === "FIXED" && eff.estimated_cpm_for_imps != null;
}

function _defaultFrequency(li, p) {
  return _isEstimateCpmProduct(li, p) ? 5.5 : null;
}

// Mutates `entry` in place, deriving ONE other field from whichever of the
// remaining two is available, based on which field was just edited:
//   - uniques edited: uniques × frequency (or the 5.5 default) -> imps
//   - frequency edited: prefer uniques × frequency -> imps; else imps / frequency -> uniques
//   - imps edited: prefer imps / uniques -> frequency (uniques already known,
//     so frequency is the "shown, calculated" side); else imps / frequency -> uniques
// The default frequency is only ever WRITTEN into entry.frequency once it's
// actually used for a real calculation — not just because a line rendered
// with it showing — same lesson as the free-form-avails default: a value
// that only ever lived in the display, never in state, silently does
// nothing at export time.
function _applyFrequencyTriangle(entry, editedKey, defaultFreq) {
  const imps = entry.max_imps;
  const uniques = entry.est_uniques;
  const freq = entry.frequency != null ? entry.frequency : defaultFreq;

  if (editedKey === "est_uniques") {
    if (uniques != null && freq != null) {
      entry.max_imps = Math.round(uniques * freq);
      if (entry.frequency == null) entry.frequency = freq;
    }
  } else if (editedKey === "frequency") {
    if (uniques != null && entry.frequency != null) {
      entry.max_imps = Math.round(uniques * entry.frequency);
    } else if (imps != null && entry.frequency != null) {
      entry.est_uniques = Math.round(imps / entry.frequency);
    }
  } else if (editedKey === "max_imps") {
    if (uniques != null) {
      entry.frequency = Math.round((imps / uniques) * 10) / 10;
    } else if (freq != null) {
      entry.est_uniques = Math.round(imps / freq);
      if (entry.frequency == null) entry.frequency = freq;
    }
  }
}

function calcMaxSpendFromImps(p, maxImps) {
  if (!maxImps || maxImps <= 0) return null;
  const model = (p.pricing_model || "").toUpperCase();
  const rate = p.rate;
  const estCpm = p.estimated_cpm_for_imps;

  if (model === "CPM" && rate != null) return { value: maxImps * rate / 1000, estimated: false };
  if (model === "CPP" && rate != null) return { value: maxImps * rate, estimated: false };
  if ((model === "FIXED" || p.estimated_impressions) && estCpm) return { value: maxImps * estCpm / 1000, estimated: true };
  return null;
}

function calcMaxImpsFromSpend(p, maxSpend) {
  if (!maxSpend || maxSpend <= 0) return null;
  const model = (p.pricing_model || "").toUpperCase();
  const rate = p.rate;
  const estCpm = p.estimated_cpm_for_imps;

  if (model === "CPM" && rate) return { value: maxSpend / rate * 1000, estimated: false };
  if (model === "CPP" && rate) return { value: maxSpend / rate, estimated: false };
  if ((model === "FIXED" || p.estimated_impressions) && estCpm) return { value: maxSpend * 1000 / estCpm, estimated: true };
  return null;
}

// --------------------------------------------------------------------------
// SOV ("Share of Voice") traffic light — how much of the planner-entered
// avails ceiling the Step 04-curated monthly budget would consume.
// <65% green/ok, 65-79% yellow, 80-89% orange, >=90% red.
// --------------------------------------------------------------------------

function computeSovPct(li, p, entry) {
  if (!li || !li.monthly_budget) return null;
  if (entry.max_spend) {
    return li.monthly_budget / entry.max_spend * 100;
  }
  if (entry.max_imps) {
    const spendResult = calcMaxSpendFromImps(_effectiveProduct(li, p), entry.max_imps);
    if (spendResult && spendResult.value) {
      return li.monthly_budget / spendResult.value * 100;
    }
  }
  return null;
}

function sovTier(pct) {
  if (pct >= 90) return "red";
  if (pct >= 80) return "orange";
  if (pct >= 65) return "yellow";
  return "green";
}

// Updates both the compact badge and the plain-language helper line beneath
// the fields from a single computed percentage — shared by the initial
// render, the live keystroke listener, and the blur handler so all three
// stay in sync with one rendering rule.
function applySovDisplay(lid, pct) {
  const badge = document.getElementById(`sov-badge-${escapeAttr(lid)}`);
  const helper = document.getElementById(`sov-helper-${escapeAttr(lid)}`);
  if (pct === null) {
    if (badge) { badge.textContent = ""; badge.className = "sov-badge"; }
    if (helper) helper.textContent = "";
    return;
  }
  const tier = sovTier(pct);
  const pctLabel = pct.toFixed(0);
  if (badge) {
    badge.className = `sov-badge sov-${tier}`;
    badge.textContent = `${pctLabel}% of avails`;
  }
  if (helper) {
    helper.className = `sov-helper sov-${tier}`;
    helper.textContent = `Proposed product allocation uses: ${pctLabel}% of total avails`;
  }
}

function updateSovBadge(lid) {
  const li = state.lineItems.find(x => x.id === lid);
  const p = state.productIndex[li ? li.product_name : ""] || {};
  const entry = state.availsData[lid] || {};
  applySovDisplay(lid, computeSovPct(li, p, entry));
}

// Number-formatted (US, comma-grouped) input helpers — kept as plain text
// inputs so we can show "1,234" / "$1,234" instead of a bare number.
function formatImpsDisplay(n, estimated) {
  if (n === null || n === undefined || isNaN(n)) return "";
  const formatted = Math.round(n).toLocaleString("en-US");
  return estimated ? `Est. ${formatted}` : formatted;
}
function formatSpendDisplay(n, estimated) {
  if (n === null || n === undefined || isNaN(n)) return "";
  const formatted = "$" + Math.round(n).toLocaleString("en-US");
  return estimated ? `Est. ${formatted}` : formatted;
}
function formatPlainDisplay(n) {
  if (n === null || n === undefined || isNaN(n)) return "";
  return Math.round(n).toLocaleString("en-US");
}
function parseFormattedInput(s) {
  if (!s) return null;
  const digits = s.replace(/[^0-9.]/g, "");
  if (!digits) return null;
  const n = parseFloat(digits);
  return isNaN(n) ? null : n;
}

function renderAvailsGrid() {
  renderAllTierTabStrips();
  const rt = (state.parsed.request_type || "").toLowerCase();
  const needs = rt.includes("avails") || rt.includes("full presentation");
  const pill = document.getElementById("avails-status-pill");
  pill.className = "avails-status-pill " + (needs ? "required" : "optional");
  pill.textContent = needs ? "Required" : "Optional";
  document.getElementById("avails-blurb").textContent = needs
    ? "This request type calls for avails. Enter either impressions or spend — the other auto-calculates. Values go into columns N/O/P of the proposal."
    : "This request type doesn't strictly require avails. Skip if you're not adding them now — columns N/O/P will be left blank for later input.";

  const grid = document.getElementById("avails-grid");
  grid.innerHTML = "";
  state.lineItems.forEach((li, idx) => {
    if (!li.id) li.id = newLineItemId();  // safety net for any line item that slipped through without one
    const p = state.productIndex[li.product_name] || {};
    const existing = state.availsData[li.id] || {};
    // When the same product appears more than once, distinguish the cards by
    // their target override (or position) so the planner knows which is which.
    const dupeCount = state.lineItems.filter(x => x.product_name === li.product_name).length;
    const subtitle = dupeCount > 1
      ? (li.target_override ? ` — ${li.target_override}` : ` — line ${idx + 1}`)
      : "";
    // Free-form beats numeric avails whenever the planner has explicitly
    // chosen one (`existing.freeform` set); with no explicit choice yet,
    // Search products default to free-form (there's no meaningful avails
    // ceiling to calculate for SEM the way there is for impression-based
    // products) and everything else defaults to the normal numeric fields.
    // Materialized into state.availsData immediately when the DEFAULT is
    // free-form (Search) and hasn't been explicitly chosen either way yet
    // — otherwise a planner who never touches the already-checked checkbox
    // and just types straight into the pre-shown textarea would have that
    // text silently do nothing at export time: write_avails_cells() only
    // checks avail.get("freeform"), it has no family-based fallback of its
    // own. Deliberately NOT done for the false/numeric default — that
    // would stamp a `{freeform: false}` entry onto every line the instant
    // Step 06 renders, even ones the planner never touches, which would
    // wrongly make renderGenerateSummary()'s "no avails entered for this
    // option" check think avails exist just because the object has a key.
    const isFreeform = existing.freeform !== undefined ? existing.freeform : (p.family === "Search");
    if (existing.freeform === undefined && isFreeform) {
      state.availsData[li.id] = state.availsData[li.id] || {};
      state.availsData[li.id].freeform = true;
    }
    const card = document.createElement("div");
    card.className = "avails-card";
    card.innerHTML = `
      <div class="avails-card-head">
        <h3>${escapeHtml(li.product_name)}${escapeHtml(subtitle)}</h3>
        <span class="sov-badge" id="sov-badge-${escapeAttr(li.id)}"></span>
      </div>
      <label class="freeform-toggle">
        <input type="checkbox" data-lid="${escapeAttr(li.id)}" data-freeform-toggle ${isFreeform ? "checked" : ""} />
        Free-form — type anything in these, no calculation
      </label>
      <div class="avails-fields">
        <label>Max Recommended Monthly Imps
          ${isFreeform ? `
            <input type="text" placeholder='e.g. "50 to 100"'
                   value="${escapeHtml(existing.max_imps_text || "")}"
                   data-lid="${escapeAttr(li.id)}" data-key="max_imps_text" />
          ` : `
            <input type="text" inputmode="numeric" placeholder="—"
                   value="${formatImpsDisplay(existing.max_imps, existing.max_imps_estimated)}"
                   data-lid="${escapeAttr(li.id)}" data-key="max_imps" />
          `}
        </label>
        <label>Max Recommended Monthly Spend
          ${isFreeform ? `
            <input type="text" placeholder='e.g. "TBD"'
                   value="${escapeHtml(existing.max_spend_text || "")}"
                   data-lid="${escapeAttr(li.id)}" data-key="max_spend_text" />
          ` : `
            <input type="text" inputmode="numeric" placeholder="—"
                   value="${formatSpendDisplay(existing.max_spend, existing.max_spend_estimated)}"
                   data-lid="${escapeAttr(li.id)}" data-key="max_spend" />
          `}
        </label>
        <label>Est. Monthly Uniques
          ${isFreeform ? `
            <input type="text" placeholder='e.g. "n/a"'
                   value="${escapeHtml(existing.est_uniques_text || "")}"
                   data-lid="${escapeAttr(li.id)}" data-key="est_uniques_text" />
          ` : `
            <input type="text" inputmode="numeric" placeholder="—"
                   value="${formatPlainDisplay(existing.est_uniques)}"
                   data-lid="${escapeAttr(li.id)}" data-key="est_uniques" />
          `}
        </label>
        ${!isFreeform ? `
        <label>Avg. Frequency
          <input type="text" inputmode="decimal" placeholder="—"
                 value="${existing.frequency != null ? existing.frequency : (_defaultFrequency(li, p) != null ? _defaultFrequency(li, p) : "")}"
                 data-lid="${escapeAttr(li.id)}" data-key="frequency" />
        </label>
        ` : ""}
      </div>
      <p class="sov-helper" id="sov-helper-${escapeAttr(li.id)}"></p>
    `;
    grid.appendChild(card);
    // Naturally blanks itself for a free-form entry — computeSovPct() has no
    // max_spend/max_imps to work with there and returns null either way.
    updateSovBadge(li.id);
  });

  grid.querySelectorAll("[data-freeform-toggle]").forEach(cb => {
    cb.addEventListener("change", () => {
      const lid = cb.dataset.lid;
      state.availsData[lid] = state.availsData[lid] || {};
      state.availsData[lid].freeform = cb.checked;
      renderAvailsGrid();
    });
  });
  // Free-form text inputs (max_imps_text/max_spend_text/est_uniques_text):
  // stored verbatim, no parsing — matched by the "_text" suffix so this
  // covers all three without repeating the same three-line handler thrice.
  grid.querySelectorAll('input[data-key$="_text"]').forEach(inp => {
    inp.addEventListener("input", () => {
      const lid = inp.dataset.lid;
      state.availsData[lid] = state.availsData[lid] || {};
      state.availsData[lid][inp.dataset.key] = inp.value;
    });
  });

  // On focus: strip formatting so the raw number is easy to edit. Excludes
  // the free-form checkbox (not a value-bearing text field — its `.value`
  // is meaningless, always "on" per the checkbox default) and the "_text"
  // free-form inputs above (running parseFormattedInput on "50 to 100"
  // would strip the letters and glue the digits together into "50100").
  grid.querySelectorAll('input:not([data-freeform-toggle]):not([data-key$="_text"])').forEach(inp => {
    inp.addEventListener("focus", e => {
      const raw = parseFormattedInput(e.target.value);
      e.target.value = raw === null ? "" : String(Math.round(raw));
    });
  });

  // On every keystroke: update the SOV traffic-light badge in real time,
  // using the value as typed so far (not waiting for blur's reformatting).
  grid.querySelectorAll("input[data-key='max_imps'], input[data-key='max_spend']").forEach(inp => {
    inp.addEventListener("input", e => {
      const lid = e.target.dataset.lid;
      const key = e.target.dataset.key;
      const liveValue = parseFormattedInput(e.target.value);
      const liveEntry = { ...(state.availsData[lid] || {}), [key]: liveValue };
      const li = state.lineItems.find(x => x.id === lid);
      const p = state.productIndex[li ? li.product_name : ""] || {};
      applySovDisplay(lid, computeSovPct(li, p, liveEntry));
    });
  });

  // On blur: parse, store, recalc the paired field, and reformat both.
  // Excludes the free-form checkbox (its own "change" listener above
  // handles it — matching it here too would stamp a stray entry[undefined]
  // into state.availsData, since a checkbox carries no data-key of its own)
  // and the "_text" free-form inputs (their own "input" listener above
  // already stores them verbatim; running this handler on them too would
  // both overwrite that with a numeric parse of the same text AND mangle
  // it in the process — parseFormattedInput("50 to 100") strips the
  // letters and glues what's left into "50100").
  grid.querySelectorAll('input:not([data-freeform-toggle]):not([data-key$="_text"])').forEach(inp => {
    inp.addEventListener("blur", e => {
      const lid = e.target.dataset.lid;
      const key = e.target.dataset.key;
      const li = state.lineItems.find(x => x.id === lid);
      const p = state.productIndex[li ? li.product_name : ""] || {};
      state.availsData[lid] = state.availsData[lid] || {};
      const entry = state.availsData[lid];

      const value = parseFormattedInput(e.target.value);
      entry[key] = value;

      const impsInputEl = () => grid.querySelector(`[data-lid="${escapeAttr(lid)}"][data-key="max_imps"]`);
      const spendInputEl = () => grid.querySelector(`[data-lid="${escapeAttr(lid)}"][data-key="max_spend"]`);
      const uniquesInputEl = () => grid.querySelector(`[data-lid="${escapeAttr(lid)}"][data-key="est_uniques"]`);
      const freqInputEl = () => grid.querySelector(`[data-lid="${escapeAttr(lid)}"][data-key="frequency"]`);

      // Recompute Max Spend from a (possibly just-derived, not just
      // directly-typed) Max Imps value — shared by the direct "typed into
      // Imps" path below and the "derived via the uniques/frequency
      // triangle" path, so a chain like Uniques -> Imps -> Spend flows all
      // the way through in one blur, not just the first hop.
      const recalcSpendFromImps = (impsValue) => {
        const spendResult = calcMaxSpendFromImps(_effectiveProduct(li, p), impsValue);
        if (spendResult !== null) {
          entry.max_spend = Math.round(spendResult.value);
          entry.max_spend_estimated = spendResult.estimated;
          const el = spendInputEl();
          if (el) el.value = formatSpendDisplay(entry.max_spend, entry.max_spend_estimated);
        }
      };

      if (key === "max_imps") {
        entry.max_imps_estimated = false;  // directly typed — authoritative
        entry.basis = "imps";  // tells the Excel export which cell to make the live formula's source
        e.target.value = formatImpsDisplay(value, false);

        if (value !== null) {
          recalcSpendFromImps(value);
        } else {
          entry.max_spend = null;
          const el = spendInputEl();
          if (el) el.value = "";
        }

        // Impressions <-> Uniques <-> Frequency triangle (independent of
        // the spend calc above — Uniques/Frequency never drive spend
        // directly, only through Impressions).
        _applyFrequencyTriangle(entry, "max_imps", _defaultFrequency(li, p));
        const uEl = uniquesInputEl(), fEl = freqInputEl();
        if (uEl) uEl.value = formatPlainDisplay(entry.est_uniques);
        if (fEl) fEl.value = entry.frequency != null ? entry.frequency : "";
      } else if (key === "max_spend") {
        entry.max_spend_estimated = false;  // directly typed — authoritative
        entry.basis = "spend";  // tells the Excel export which cell to make the live formula's source
        e.target.value = formatSpendDisplay(value, false);

        const impsResult = calcMaxImpsFromSpend(_effectiveProduct(li, p), value);
        const impsInput = impsInputEl();
        if (impsResult !== null) {
          entry.max_imps = Math.round(impsResult.value);
          entry.max_imps_estimated = impsResult.estimated;
          if (impsInput) impsInput.value = formatImpsDisplay(entry.max_imps, entry.max_imps_estimated);
          // Spend -> Imps just derived above; let it also refresh Uniques/
          // Frequency (e.g. Frequency was already known -> Uniques updates).
          _applyFrequencyTriangle(entry, "max_imps", _defaultFrequency(li, p));
          const uEl = uniquesInputEl(), fEl = freqInputEl();
          if (uEl) uEl.value = formatPlainDisplay(entry.est_uniques);
          if (fEl) fEl.value = entry.frequency != null ? entry.frequency : "";
        } else if (value === null) {
          entry.max_imps = null;
          if (impsInput) impsInput.value = "";
        }
      } else if (key === "est_uniques") {
        e.target.value = formatPlainDisplay(value);
        _applyFrequencyTriangle(entry, "est_uniques", _defaultFrequency(li, p));
        if (entry.max_imps != null) {
          entry.max_imps_estimated = false;
          entry.basis = "imps";
          const el = impsInputEl();
          if (el) el.value = formatImpsDisplay(entry.max_imps, false);
          recalcSpendFromImps(entry.max_imps);
          const fEl = freqInputEl();
          if (fEl) fEl.value = entry.frequency != null ? entry.frequency : "";
        }
      } else if (key === "frequency") {
        e.target.value = value != null ? value : "";
        _applyFrequencyTriangle(entry, "frequency", _defaultFrequency(li, p));
        if (entry.max_imps != null) {
          entry.max_imps_estimated = false;
          entry.basis = "imps";
          const el = impsInputEl();
          if (el) el.value = formatImpsDisplay(entry.max_imps, false);
          recalcSpendFromImps(entry.max_imps);
        }
        const uEl = uniquesInputEl();
        if (uEl) uEl.value = formatPlainDisplay(entry.est_uniques);
      }

      updateSovBadge(lid);  // refresh against the final, rounded/settled values
    });
  });
}

function syncAvailsFromGrid() {
  // No-op: state.availsData is kept in sync via the blur listeners above.
}

// --------------------------------------------------------------------------
// Step 5: Generate
// --------------------------------------------------------------------------

function renderGenerateSummary() {
  const monthly = state.lineItems.reduce((s, li) => s + (li.monthly_budget || 0), 0);
  const flight = state.lineItems.reduce((s, li) => s + (li.monthly_budget || 0) * (li.months || 1), 0);
  const fee = state.parsed.agency_fee || 0;
  const gross = fee > 0 ? flight / (1 - fee) : flight;
  const months = state.parsed.total_months
    || (state.lineItems[0] && state.lineItems[0].months)
    || parseBudgetFromRenewal(state.parsed) && state.lineItems[0]?.months
    || 3;

  const allTiers = allTiersForSubmit();
  const multiTier = allTiers.length > 1;
  const tierLabel = multiTier ? ` (Option ${state.activeTierLabel})` : "";

  // Catch the exact gap that caused an option to silently ship with no
  // avails: some products across the proposal DO have avails entered, but
  // one whole option has none at all — flag it before generating rather
  // than after.
  const availsWarningEl = document.getElementById("tiers-avails-warning");
  if (availsWarningEl) {
    const anyAvailsAnywhere = allTiers.some(t => Object.keys(t.avails_data || {}).length > 0);
    const emptyTiers = multiTier
      ? allTiers.filter(t => (t.line_items || []).length > 0 && Object.keys(t.avails_data || {}).length === 0)
      : [];
    if (anyAvailsAnywhere && emptyTiers.length > 0) {
      availsWarningEl.classList.remove("hidden");
      availsWarningEl.innerHTML = `<strong>⚠ No avails entered for ${emptyTiers.map(t => `Option ${escapeHtml(t.label)}`).join(", ")}</strong> — other options have avails, so ${emptyTiers.length === 1 ? "this one" : "these"} will export without any. Go back to Step 06, switch to that tab, and enter avails (or use "Copy avails from") if that's not intentional.`;
    } else {
      availsWarningEl.classList.add("hidden");
      availsWarningEl.innerHTML = "";
    }
  }

  const summary = document.getElementById("generate-summary");
  summary.innerHTML = `
    <div class="sum-row"><span class="lbl">Client</span><span class="val">${escapeHtml(state.parsed.client_name || "—")}</span></div>
    <div class="sum-row"><span class="lbl">Seller</span><span class="val mono">${escapeHtml(state.parsed.salesperson_email || "—")}</span></div>
    <div class="sum-row"><span class="lbl">Request type</span><span class="val mono">${escapeHtml(state.parsed.request_type || "—")}</span></div>
    <div class="sum-row"><span class="lbl">Products${tierLabel}</span><span class="val">${state.lineItems.length}</span></div>
    <div class="sum-row"><span class="lbl">Monthly total (Net)${tierLabel}</span><span class="val">${money(monthly)}</span></div>
    <div class="sum-row"><span class="lbl">Flight total (Net)${tierLabel}</span><span class="val">${money(flight)}</span></div>
    ${fee > 0 ? `<div class="sum-row"><span class="lbl">Agency fee</span><span class="val mono">${(fee*100).toFixed(2)}%</span></div>` : ""}
    ${fee > 0 ? `<div class="sum-row"><span class="lbl">Flight total (Gross)${tierLabel}</span><span class="val">${money(gross)}</span></div>` : ""}
    ${multiTier ? `<div class="sum-row sum-tiers-row"><span class="lbl">Budget Options</span><span class="val">${allTiers.map(t => {
      const m = (t.line_items || []).reduce((s, li) => s + (li.monthly_budget || 0), 0);
      return `Option ${escapeHtml(t.label)}: ${money(m)}/mo · ${(t.line_items || []).length} products`;
    }).join(" &nbsp;·&nbsp; ")}</span></div>` : ""}
  `;
  // Sync suggested tabs into the checkboxes
  Object.entries(state.suggestedTabs || {}).forEach(([k, v]) => {
    const cb = document.querySelector(`.tabs-override [data-tab="${k}"]`);
    if (cb) cb.checked = !!v;
  });
}

async function onGenerate() {
  const forceTabs = {};
  document.querySelectorAll(".tabs-override [data-tab]").forEach(cb => {
    forceTabs[cb.dataset.tab] = cb.checked;
  });
  const payload = {
    request: state.parsed,
    line_items: state.lineItems,     // legacy field — kept for back-compat; the server prefers `tiers` when present
    tiers: allTiersForSubmit(),
    force_tabs: forceTabs,
    avails_data: state.availsData,
    strategy_brief: state.strategyBrief || null,
    addons: Object.entries(state.addons).map(([product_name, amount]) => ({ product_name, amount })),
  };
  const btn = document.getElementById("generate-btn");
  btn.disabled = true;
  btn.textContent = "Generating + Intelligence Pack…";
  try {
    const res = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert("Generation failed: " + (err.detail || res.statusText));
      return;
    }
    const data = await res.json();
    state.proposalId = data.proposal_id;
    state.proposalSummary = data.summary;
    showResult(data);
  } finally {
    btn.disabled = false;
    btn.textContent = "⬇ Generate Proposal";
  }
}

// --------------------------------------------------------------------------
// Gamma outline — a paste-in handoff for the "Media Strategy Co-Pilot" GPT
// (https://chatgpt.com/g/g-68c0554e...), which has no callable API of its
// own: the planner copies this text and pastes it into that GPT's chat
// themselves, rather than this app calling it directly. Built entirely
// from data already gathered by Steps 01-06 — no extra request, no AI call.
// --------------------------------------------------------------------------

function buildGammaOutline() {
  const req = state.parsed || {};
  const lines = [];

  const campaignName = (state.enrichment && state.enrichment.campaign_name) || req.client_name || "Campaign";
  lines.push(`MEDIA PLAN OUTLINE — ${campaignName}`);
  if (state.finalProposalTitle) lines.push(state.finalProposalTitle);
  lines.push("");

  lines.push("CLIENT & CAMPAIGN");
  if (req.client_name) lines.push(`- Client: ${req.client_name}`);
  if (req.client_website) lines.push(`- Website: ${req.client_website}`);
  if (req.campaign_goal) lines.push(`- Goal: ${req.campaign_goal}`);
  if (req.geo) lines.push(`- Geo: ${req.geo}`);
  const targeting = [req.demo, req.behavioral, req.contextual ? `Contextual: ${req.contextual}` : ""]
    .filter(Boolean).join(" | ");
  if (targeting) lines.push(`- Target: ${targeting}`);
  const flightBits = [];
  if (req.start_date) flightBits.push(req.start_date + (req.end_date ? ` – ${req.end_date}` : ""));
  if (req.total_months) flightBits.push(`(${req.total_months} month${req.total_months === 1 ? "" : "s"})`);
  if (flightBits.length) lines.push(`- Flight: ${flightBits.join(" ")}`);
  lines.push("");

  const brief = state.strategyBrief;
  if (brief && !brief.error && (brief.strategy_summary || brief.client_summary)) {
    lines.push("STRATEGY BRIEF");
    if (brief.client_summary) lines.push(`Client/Category: ${brief.client_summary}`);
    if (brief.market_context) lines.push(`Market Context: ${brief.market_context}`);
    if (brief.objectives_analysis) lines.push(`Objectives: ${brief.objectives_analysis}`);
    if (brief.strategy_summary) lines.push(`Strategy: ${brief.strategy_summary}`);
    if (brief.key_insights && brief.key_insights.length) {
      lines.push("Key Insights:");
      brief.key_insights.forEach(k => lines.push(`  - ${k}`));
    }
    lines.push("");
  }

  const tiers = allTiersForSubmit();
  lines.push(tiers.length > 1 ? "MEDIA PLAN OPTIONS" : "MEDIA PLAN");
  tiers.forEach(t => {
    if (tiers.length > 1) lines.push(`Option ${t.label}:`);
    let tierTotal = 0;
    (t.line_items || []).forEach(li => {
      const p = state.productIndex[li.product_name] || {};
      tierTotal += (li.monthly_budget || 0) * (li.months || 1);
      const target = li.target_override || targeting || "(campaign default)";
      lines.push(`  - ${li.product_name}${p.family ? ` (${p.family})` : ""} — ${money(li.monthly_budget)}/mo × ${li.months}mo — Target: ${target}`);
    });
    lines.push(`  Option total: ${money(tierTotal)}`);
    lines.push("");
  });

  const addonEntries = Object.entries(state.addons || {});
  if (addonEntries.length) {
    lines.push("ADD-ONS");
    addonEntries.forEach(([name, amount]) => lines.push(`  - ${name}: ${money(amount)}`));
    lines.push("");
  }

  const rb = state.roadblocks;
  if (rb && !rb.error && (rb.overall_summary || (rb.product_roadblocks || []).length)) {
    lines.push("ROADBLOCKS / CONSIDERATIONS");
    if (rb.overall_summary) lines.push(rb.overall_summary);
    (rb.product_roadblocks || []).forEach(pr => {
      lines.push(`  - ${pr.product_name} (${pr.risk_level || "low"} risk)`);
      (pr.risks || []).forEach(r => lines.push(`      • ${r.issue}${r.detail ? " — " + r.detail : ""}`));
    });
    lines.push("");
  }

  return lines.join("\n").trim();
}

function showResult(data) {
  const result = document.getElementById("result");
  result.classList.remove("hidden");

  // Proposal title (naming convention) — now the REAL, server-computed
  // title (campaign name AI-inferred, real ID assigned), so the persistent
  // name bar should show this verbatim from here on rather than its guess.
  state.finalProposalTitle = data.proposal_title || null;
  updateProposalNameBar();

  const titleEl = document.getElementById("result-title");
  if (data.proposal_title) {
    titleEl.textContent = data.proposal_title;
    titleEl.classList.remove("hidden");
  } else {
    titleEl.classList.add("hidden");
  }

  document.getElementById("result-meta").innerHTML = `
    Tabs: ${data.summary.tabs_built.join(" · ")}<br>
    Net: ${money(data.summary.total_net)} · Gross: ${money(data.summary.total_gross)}<br>
    File: ${escapeHtml(data.filename)}
  `;

  const dl = document.getElementById("download-link");
  dl.href = `/api/download/${data.proposal_id}`;

  document.getElementById("gamma-outline-body").textContent = buildGammaOutline();

  // AI enrichment section
  const aiContent = document.getElementById("ai-content");
  const aiError = document.getElementById("ai-error");
  const enrichment = data.enrichment || {};
  state.enrichment = enrichment;

  aiContent.classList.add("hidden");
  aiError.classList.add("hidden");
  document.getElementById("enrichment-search-note").classList.add("hidden");
  document.getElementById("email-reprompt-area").classList.add("hidden");
  document.getElementById("email-reprompt-btn").style.display = "";
  document.getElementById("email-reprompt-input").value = "";

  if (enrichment.error && !enrichment.internal_email_body) {
    // AI was skipped or fully failed
    aiError.classList.remove("hidden");
    aiError.textContent = "ⓘ " + enrichment.error;
  } else if (enrichment.internal_email_body || enrichment.client_email_body) {
    aiContent.classList.remove("hidden");

    const searchNoteEl = document.getElementById("enrichment-search-note");
    if (!enrichment.used_web_search) {
      searchNoteEl.classList.remove("hidden");
      searchNoteEl.textContent = "⚠ " + (enrichment.error || "Generated without live web search — verify stats before sending.");
    } else {
      searchNoteEl.classList.add("hidden");
    }

    // Internal email
    setText("internal-email-subject", "Subject: " + (enrichment.internal_email_subject || ""));
    setText("internal-email-body", enrichment.internal_email_body || "");

    // Client email
    setText("client-email-subject", "Subject: " + (enrichment.client_email_subject || ""));
    setText("client-email-body", enrichment.client_email_body || "");

    // Word doc download link
    const docLink = document.getElementById("download-email-doc");
    if (enrichment.has_email_doc) {
      docLink.href = `/api/download-email/${data.proposal_id}`;
      docLink.classList.remove("hidden");
    } else {
      docLink.classList.add("hidden");
    }
  }

  result.scrollIntoView({ behavior: "smooth", block: "start" });
}

// --------------------------------------------------------------------------
// Step 7: reprompt the emails based on the planner's final review
// --------------------------------------------------------------------------

async function onEmailReprompt() {
  const text = document.getElementById("email-reprompt-input").value.trim();
  if (!text) { alert("Enter what you'd like to change first."); return; }
  if (!state.proposalId || !state.enrichment) return;

  const btn = document.getElementById("email-reprompt-submit-btn");
  btn.disabled = true;
  btn.textContent = "Regenerating…";

  try {
    const res = await fetch(`/api/proposal/${state.proposalId}/reprompt-emails`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        request: state.parsed,
        line_items: state.lineItems,
        campaign_name: state.enrichment.campaign_name || "",
        current_internal_subject: state.enrichment.internal_email_subject || "",
        current_internal_body: state.enrichment.internal_email_body || "",
        current_client_subject: state.enrichment.client_email_subject || "",
        current_client_body: state.enrichment.client_email_body || "",
        reprompt: text,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      alert("Reprompt failed: " + (data.detail || res.statusText));
      return;
    }
    if (data.error) {
      alert("Reprompt didn't fully succeed: " + data.error);
    }

    state.enrichment.internal_email_subject = data.internal_email_subject;
    state.enrichment.internal_email_body = data.internal_email_body;
    state.enrichment.client_email_subject = data.client_email_subject;
    state.enrichment.client_email_body = data.client_email_body;

    setText("internal-email-subject", "Subject: " + (data.internal_email_subject || ""));
    setText("internal-email-body", data.internal_email_body || "");
    setText("client-email-subject", "Subject: " + (data.client_email_subject || ""));
    setText("client-email-body", data.client_email_body || "");

    document.getElementById("email-reprompt-area").classList.add("hidden");
    document.getElementById("email-reprompt-btn").style.display = "";
    document.getElementById("email-reprompt-input").value = "";
  } catch (e) {
    alert("Request failed: " + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "↺ Regenerate Emails";
  }
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

async function onDriveUpload() {
  if (!state.proposalId) return;
  const status = document.getElementById("drive-status");
  status.classList.remove("hidden", "ok", "warn");
  status.textContent = "Uploading to Drive…";
  const res = await fetch("/api/drive/upload", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      proposal_id: state.proposalId,
      seller_email: state.parsed.salesperson_email || "",
    }),
  });
  const data = await res.json();
  if (data.uploaded) {
    status.classList.add("ok");
    status.innerHTML = `✓ Uploaded — <a href="${data.shareable_link}" target="_blank" rel="noopener">Open in Drive</a>`;
  } else if (data.auth_url) {
    // Need OAuth authorization
    status.classList.add("warn");
    status.innerHTML = `Drive not authorized. <a href="${data.auth_url}" target="_blank">Click here to authorize Google Drive</a>, then try uploading again.`;
  } else {
    status.classList.add("warn");
    status.textContent = "⚠ " + (data.reason || "Upload not configured");
  }
}

// --------------------------------------------------------------------------
// Utilities
// --------------------------------------------------------------------------

function money(n) {
  if (n === null || n === undefined || isNaN(n)) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(n);
}

function formatRate(p) {
  if (p.rate == null) return "—";
  if (p.pricing_model === "CPM") return `$${p.rate} CPM`;
  if (p.pricing_model === "CPP") return `$${p.rate} /pt`;
  return `$${p.rate}`;
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function escapeAttr(s) {
  // For use in HTML attribute values — also strip characters that break querySelector
  return String(s || "").replace(/[^a-zA-Z0-9._-]/g, "_");
}
