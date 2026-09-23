/* ========================================================================
 * Entravision Proposal Builder — front-end controller
 * ======================================================================== */

const state = {
  catalog: null,            // { families: [...], products_by_family: {...} }
  productIndex: {},         // name -> product object (flat lookup)
  parsed: null,             // parsed ProposalRequest dict
  rawNotionText: null,      // the exact Step 01 paste that produced `parsed` — sent to /api/generate and saved to reopen_state so a reopen can refill the textarea (not re-derivable from `parsed` once past Step 01)
  suggestedTabs: null,
  strategyBrief: null,      // confirmed AI strategy brief (or null if skipped)
  roadblocks: null,         // Step 07 AI roadblocks/restrictions result (or null if skipped)
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
  // Step 06's plan-wide default-split choice — "even" (equal $ per month)
  // or "prorated" (weighted by each month's actual active days in the
  // flight). Proposal-wide, not per-tier (one control "at the top" of the
  // step, not per-option) — governs what a NEW default gets computed as
  // (checkbox-check, reset button, a newly-added line auto-joining); never
  // retroactively rewrites a line's already-set allocations, same "don't
  // silently overwrite a planner's figure" rule Monthly Breakdown already
  // applies to date changes. Defaults to "even" per explicit instruction
  // (day-proration was this app's own earlier default; even is now what
  // the planner actually wants, with day-proration kept as an opt-in).
  mbDistributionMode: "even",
  // Step 04's Week/Month/Quarter toggle — "week" | "month" | "quarter".
  // Proposal-wide (not per-tier — the toggle lives once at Step 04 and
  // governs the whole proposal, same reasoning as mbDistributionMode
  // above), drives which of the _mbPeriodsBetween granularities Curate/
  // Avails/Step 06/the export all use. Defaults to "month" — this app's
  // original, only-ever behavior before the toggle existed.
  timeUnit: "month",
  // Step 06's "combine adjacent periods into one bucket" control for the
  // ACTIVE tier — [["2026-09","2026-10"], ...], each inner array 2+
  // period keys. TIER-scoped (like lineItems/availsData below, swapped by
  // switchTier/addTier/removeTier) because every line item in a tier
  // shares the same resolved dates and therefore the same period list —
  // see TierModel.period_merge_groups' own comment in main.py for why
  // this isn't per-line-item.
  activeTierPeriodMergeGroups: [],
  rateOverrideOpen: new Set(),  // transient UI state — which row indices show the rate-override input
  objectiveOtherOpen: new Set(),  // transient UI state — which row indices show the free-text "Other" objective input
  proposalId: null,
  proposalSummary: null,
  enrichment: null,         // Step 07 AI email content (subjects/bodies) — reprompt-able in place
  // The REAL naming-convention title, once known (from a successful Generate
  // or from reopening a past proposal) — the persistent name bar shows this
  // verbatim instead of the live best-guess preview once it's set. Cleared
  // whenever the planner goes back to Step 02, since editing client name/
  // request type/start date there can change the real title on next Generate.
  finalProposalTitle: null,
  // Planner override for the proposal name bar's campaign-name segment
  // (the one AI-guessed/client-name-derived part of the naming
  // convention) — null means "use the usual guess". Cleared whenever the
  // planner returns to Step 02, same invalidation rule finalProposalTitle
  // already follows, since editing client name there changes what the
  // un-overridden guess would even be.
  manualCampaignNameOverride: null,
  // "My Proposal History" modal (Step 01) — a compact, always-scoped-to-
  // the-caller-own-email view (see GET /api/my-proposals), independent
  // of the wizard's own state and never sent anywhere.
  myProposals: { page: 1, pageSize: 10, search: "", totalPages: 1, totalCount: 0 },
  // Tiered budget options (up to 10 — "A".."J"). state.lineItems/availsData
  // ALWAYS hold the currently-active tier's data (same as before tiers
  // existed — no other code needs to change); `tiers` holds a snapshot for
  // every OTHER tier, swapped in/out by switchTier(). See "Tiered budget
  // options" section below for the full read/write contract.
  tiers: [],                // [{ label, name, geo, lineItems, availsData }] — every tier EXCEPT the active one
  activeTierLabel: "A",
  // Planner-given display name for the ACTIVE tier (e.g. "Independent"),
  // shown instead of "Option A" everywhere a seller/client actually reads
  // this — null falls back to "Option {label}". Swapped in/out of the
  // `tiers` snapshots alongside lineItems/availsData by switchTier().
  activeTierName: null,
  // Per-tier geo override (e.g. two options targeting different DMAs) —
  // null falls back to the campaign-level Geo from Step 02. Same
  // swap-in/swap-out treatment as activeTierName, above.
  activeTierGeo: null,
  // Per-tier flight-date override (e.g. two options running different
  // windows) — null falls back to the campaign-level Start/End date from
  // Step 02. Same swap-in/swap-out treatment as activeTierGeo, above.
  // Deliberately NOT carried over to a brand-new option by addTier() (see
  // its comment) — unlike geo, a new option defaulting to the previous
  // option's exact date range is more likely to be wrong than right.
  activeTierStartDate: null,
  activeTierEndDate: null,
  step: 1,
  // The highest step number reached so far this session — lets the top nav
  // pills be clickable up to (but not past) wherever the wizard has
  // actually gotten to, without letting the planner skip ahead into a step
  // whose data was never populated. Reset only by resetAll().
  furthestStep: 1,
};

const TIER_LABELS = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"];

// Per-line objective dropdown (Step 04) — deliberately the same fixed list
// for every product regardless of family; a given product might realistically
// only serve 1-2 of these, but that filtering isn't built yet, so every line
// gets every choice for now. "Other" isn't a real value stored anywhere —
// selecting it just reveals a free-text input; whatever's typed there IS
// the stored value (see renderLineItems()'s objective cell).
const OBJECTIVE_OPTIONS = ["Awareness", "Website Conversion", "Click-To-Call", "Conquesting", "Lead Generation"];

// Best-effort match of Step 02's inferred campaign goal onto one of the
// fixed dropdown options above — used only to default a new line item's
// objective, never anything the planner can't immediately see/change.
// Falls back to the raw goal text verbatim (renders as a custom "Other"
// value) when nothing recognizable matches, rather than silently
// discarding whatever was already inferred.
function _mapCampaignGoalToObjective(goal) {
  if (!goal) return null;
  const g = goal.toLowerCase();
  if (g.includes("click") || g.includes("call")) return "Click-To-Call";
  if (g.includes("conquest")) return "Conquesting";
  if (g.includes("lead")) return "Lead Generation";
  if (g.includes("convers") || g.includes("website") || g.includes("traffic")) return "Website Conversion";
  if (g.includes("aware")) return "Awareness";
  return goal;
}

// Whether row `idx`'s objective dropdown should show "Other…" selected (and
// its free-text input revealed) — either because the planner just picked
// "Other…" this session (state.objectiveOtherOpen, a transient UI flag,
// mirroring rateOverrideOpen's pattern for the same kind of "alternate
// input mode" toggle) or because the stored value is already custom text
// that doesn't match any of the fixed options (e.g. a reopened proposal, or
// a campaign goal that didn't map to one of the 5 presets).
function _objectiveIsOther(li, idx) {
  if (state.objectiveOtherOpen.has(idx)) return true;
  return !!(li.objective_override && !OBJECTIVE_OPTIONS.includes(li.objective_override));
}

// The one NET<->GROSS formula, shared by the top-level totals and every
// per-line Gross budget input below — matches app/services/proposal_generator.py
// and every Excel/PPTX export formula exactly (gross = net / (1 - fee)), so
// what Step 04 shows is never out of step with what actually gets exported.
function _netToGross(net, fee) {
  const gross = fee ? net / (1 - fee) : net;
  return Math.round(gross * 100) / 100;
}
function _grossToNet(gross, fee) {
  const net = fee ? gross * (1 - fee) : gross;
  return Math.round(net * 100) / 100;
}

// Agency fee is meant to be a fraction (0–0.99, e.g. 0.15 for 15%) —
// _netToGross/_grossToNet above both assume that range. But a planner
// naturally types "15" for "15%" (the field is just labeled "Agency
// Fee", no visible "%"), and nothing stopped that raw "15" from being
// stored and used directly — 1 - 15 = -14, so every "Gross" figure came
// out negative (the exact bug this fixes). Mirrors notion_parser.py's
// own _parse_agency_fee(), which already does this same normalization
// server-side for a fresh Notion paste — this is the shared client-side
// equivalent for the two places a planner can ALSO set/override the fee
// directly (this function, and Step 02's own field via syncFormToParsed).
// Returns null (not 0) for anything blank/unparseable/out-of-range, same
// "couldn't make sense of it, treat as no fee" convention as the Python side.
function _normalizeAgencyFee(raw) {
  if (raw === "" || raw == null) return null;
  const n = parseFloat(raw);
  if (isNaN(n)) return null;
  const frac = n >= 1 ? n / 100 : n;
  return (frac >= 0 && frac < 1) ? frac : null;
}

function onCurateAgencyFeeInput(e) {
  state.parsed.agency_fee = _normalizeAgencyFee(e.target.value);
  // Keep Step 02's own field in sync so going back there shows the same value.
  const step2Field = document.querySelector('[data-field="agency_fee"]');
  if (step2Field) step2Field.value = e.target.value;
  // Full re-render, not just updateTotals() — toggling the fee between
  // 0 and a real value shows/hides every row's Gross budget cell, a
  // structural change, not just a number update.
  renderLineItems();
}

// Unique-enough id for a line item (stable identity across renders/edits,
// used to key avails data independent of product name so duplicate-product
// lines don't collide).
function newLineItemId() {
  return "li_" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
}

// --------------------------------------------------------------------------
// Init
// --------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", async () => {
  // loadCatalog() has no try/catch of its own (a plain `await res.json()`
  // on whatever /api/catalog returns) — if that request ever fails for
  // ANY reason (a DB hiccup, a deploy race, a transient 500), the
  // uncaught exception used to propagate straight out of this whole
  // handler, which meant wireEvents() below NEVER RAN — every button on
  // the page silently dead, with no error shown at all. Caught here so a
  // catalog failure degrades ONLY what actually needs the catalog
  // (product picker, Suggest Mix) instead of the entire app.
  try {
    await loadCatalog();
  } catch (err) {
    console.error("Catalog failed to load — product picker / Suggest Mix will be unavailable until this is fixed:", err);
  }
  wireEvents();
  // Positions the toggle's sliding thumb (and every dependent label) for
  // the very first render — without this, a fresh (non-reopened) session
  // never gets a transform on the thumb at all (it only otherwise runs on
  // a toggle click or inside maybeReopenProposal), leaving it sitting at
  // its CSS default position while the logically-active "Monthly" option
  // renders white text with nothing behind it — invisible against the
  // track's own white background. Redundant-but-harmless for a reopened
  // session, which calls this again itself once state.timeUnit is restored.
  _applyTimeUnitLabels();
  await maybeReopenProposal();
});

// --------------------------------------------------------------------------
// "My Proposal History" modal (Step 01) — any logged-in planner's own
// past proposals, always scoped server-side (GET /api/my-proposals never
// takes a `mine` override the way the admin endpoint does). Deliberately
// a much simpler view than the admin console's own Proposals tab (no
// seller/IP/device columns — a planner looking at their OWN history
// doesn't need to be told it's theirs) but reuses its table/pagination
// styling directly (admin.css, loaded on this page too).
// --------------------------------------------------------------------------

function formatDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", year: "2-digit", hour: "numeric", minute: "2-digit" });
  } catch {
    return iso;
  }
}

function openMyProposalsModal() {
  document.getElementById("my-proposals-modal").classList.remove("hidden");
  loadMyProposals();
}

function closeMyProposalsModal() {
  document.getElementById("my-proposals-modal").classList.add("hidden");
}

async function loadMyProposals() {
  const body = document.getElementById("my-proposals-body");
  body.innerHTML = `<tr><td colspan="6" class="admin-empty"><span class="btn-inline-spinner"></span>Loading…</td></tr>`;
  document.getElementById("my-proposals-prev-btn").disabled = true;
  document.getElementById("my-proposals-next-btn").disabled = true;

  const { page, pageSize, search } = state.myProposals;
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize), search });
  try {
    const res = await fetch(`/api/my-proposals?${params}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    state.myProposals.totalPages = data.total_pages || 1;
    state.myProposals.totalCount = data.total_count || 0;
    state.myProposals.page = data.page || page;
    renderMyProposalsTable(data.proposals || []);
  } catch (e) {
    body.innerHTML = `<tr><td colspan="6" class="admin-empty">Failed to load: ${escapeHtml(e.message)}</td></tr>`;
  } finally {
    document.getElementById("my-proposals-page-info").textContent = `Page ${state.myProposals.page} of ${state.myProposals.totalPages}`;
    document.getElementById("my-proposals-prev-btn").disabled = state.myProposals.page <= 1;
    document.getElementById("my-proposals-next-btn").disabled = state.myProposals.page >= state.myProposals.totalPages;
    document.getElementById("my-proposals-count").textContent =
      `${state.myProposals.totalCount} proposal${state.myProposals.totalCount === 1 ? "" : "s"}`;
  }
}

function renderMyProposalsTable(list) {
  const body = document.getElementById("my-proposals-body");
  if (!list.length) {
    body.innerHTML = `<tr><td colspan="6" class="admin-empty">No proposals yet.</td></tr>`;
    return;
  }
  body.innerHTML = list.map(p => `
    <tr>
      <td class="mono">${escapeHtml(formatDate(p.generated_at))}</td>
      <td class="mono">${escapeHtml(p.notion_id || "—")}</td>
      <td>${escapeHtml(p.client_name || "—")}</td>
      <td class="wrap">${escapeHtml(p.proposal_title || p.filename || "—")}</td>
      <td class="mono">${money(p.total_net)}</td>
      <td><a class="reopen-link" href="/?reopen=${encodeURIComponent(p.proposal_id)}" target="_blank" rel="noopener">Reopen ↗</a></td>
    </tr>
  `).join("");
}

// --------------------------------------------------------------------------
// "Search Notion Requests" modal (Step 01) — an alternative to copy-pasting
// from Notion by hand. Degrades gracefully (a friendly inline note, not an
// error) when the integration has no token/database configured server-side
// — see GET /api/notion/search's own "configured: false" response.
//
// Selecting a result does NOT auto-fill the paste textarea (see
// notion_client.py's own module docstring on why field-mapping is a
// follow-up phase) — it fills the Notion ID field (when the result's own
// "ID" property parses as digits) and shows every fetched property in a
// reference panel above the paste box, so a planner can still confirm
// they picked the right request and copy specific values while pasting
// the rest from Notion as before.
// --------------------------------------------------------------------------

const state_notionSearch = { status: "New" };  // module-level, not on the big `state` object — purely transient modal UI state, never sent anywhere or persisted

function openNotionSearchModal() {
  document.getElementById("notion-search-modal").classList.remove("hidden");
  loadNotionSearchResults();
}

function closeNotionSearchModal() {
  document.getElementById("notion-search-modal").classList.add("hidden");
}

async function loadNotionSearchResults() {
  const body = document.getElementById("notion-search-body");
  body.innerHTML = `<tr><td colspan="5" class="admin-empty"><span class="btn-inline-spinner"></span>Loading…</td></tr>`;
  document.getElementById("notion-search-count").textContent = "";
  try {
    const res = await fetch(`/api/notion/search?status=${encodeURIComponent(state_notionSearch.status)}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    if (!data.configured) {
      body.innerHTML = `<tr><td colspan="5" class="admin-empty">Notion search isn't set up yet — ask an admin to configure it.</td></tr>`;
      return;
    }
    renderNotionSearchTable(data.results || []);
  } catch (e) {
    body.innerHTML = `<tr><td colspan="5" class="admin-empty">Failed to load: ${escapeHtml(e.message)}</td></tr>`;
  }
}

// Every Notion database's own property names vary — read generically
// rather than assuming exact keys, so this doesn't break if a column
// gets renamed on the Notion side. Falls back to "—" for anything absent.
function _notionField(result, ...candidateNames) {
  for (const name of candidateNames) {
    if (result[name] != null && result[name] !== "") return result[name];
  }
  return null;
}

function renderNotionSearchTable(list) {
  const body = document.getElementById("notion-search-body");
  document.getElementById("notion-search-count").textContent = `${list.length} request${list.length === 1 ? "" : "s"}`;
  if (!list.length) {
    body.innerHTML = `<tr><td colspan="5" class="admin-empty">No requests at this status.</td></tr>`;
    return;
  }
  body.innerHTML = list.map(r => {
    const name = _notionField(r, "Project Name", "Name") || "(untitled)";
    const id = _notionField(r, "ID");
    const owner = _notionField(r, "Owner");
    const due = _notionField(r, "Due Date");
    return `
    <tr>
      <td>${escapeHtml(name)}</td>
      <td class="mono">${escapeHtml(id != null ? String(id) : "—")}</td>
      <td>${escapeHtml(Array.isArray(owner) ? owner.join(", ") : (owner || "—"))}</td>
      <td class="mono">${escapeHtml(due ? formatDate(due) : "—")}</td>
      <td><button type="button" class="btn-secondary notion-select-btn" data-page-id="${escapeAttr(r.page_id)}">Select</button></td>
    </tr>`;
  }).join("");
  body.querySelectorAll(".notion-select-btn").forEach(btn => {
    btn.addEventListener("click", () => onSelectNotionResult(btn.dataset.pageId));
  });
}

async function onSelectNotionResult(pageId) {
  const textarea = document.getElementById("notion-input");
  // Selecting a result REPLACES the paste box — confirm first if the
  // planner already has real manual work sitting there, same "don't
  // silently overwrite in-progress input" rule as any other destructive
  // action in this app.
  if (textarea.value.trim() && !confirm("This will replace the text currently in the paste box below. Continue?")) {
    return;
  }
  const btn = document.querySelector(`.notion-select-btn[data-page-id="${CSS.escape(pageId)}"]`);
  if (btn) { btn.disabled = true; btn.textContent = "Loading…"; }
  try {
    const res = await fetch(`/api/notion/page/${encodeURIComponent(pageId)}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    applyNotionReference(data.properties || {}, data.body_text || "");
    closeNotionSearchModal();
  } catch (e) {
    alert("Could not load that request: " + e.message);
    if (btn) { btn.disabled = false; btn.textContent = "Select"; }
  }
}

// Extracts just the digits from an "ID" value shaped like "EVC-3003" (or
// a bare number) — matches the notion-id-input's own digits-only format.
function _digitsFromNotionId(raw) {
  if (raw == null) return "";
  return String(raw).replace(/\D/g, "").slice(0, 5);
}

// bodyText: the Notion page's own body content, already in the SAME
// "Label: value" plain-text shape a manual paste has (confirmed by the
// planner — see notion_client.get_page_body_text's own docstring) — fed
// directly into the SAME textarea/Parse flow a copy-paste already uses,
// so parse_notion() needs zero changes to handle either source.
function applyNotionReference(properties, bodyText) {
  const idDigits = _digitsFromNotionId(_notionField(properties, "ID"));
  if (idDigits) document.getElementById("notion-id-input").value = idDigits;

  if (bodyText) document.getElementById("notion-input").value = bodyText;

  const title = _notionField(properties, "Project Name", "Name") || "(untitled)";
  document.getElementById("notion-reference-title").textContent = title;

  const fieldsEl = document.getElementById("notion-reference-fields");
  const entries = Object.entries(properties).filter(([k, v]) => v != null && v !== "" && k !== "Project Name" && k !== "Name");
  fieldsEl.innerHTML = entries.map(([k, v]) => `
    <div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(Array.isArray(v) ? v.join(", ") : String(v))}</dd></div>
  `).join("");

  document.getElementById("notion-reference-panel").classList.remove("hidden");
}

// --------------------------------------------------------------------------
// Reopen an existing proposal (e.g. from the Admin console's "Reopen" link,
// /?reopen={proposal_id}) — pre-fills the wizard instead of starting blank.
// --------------------------------------------------------------------------

async function maybeReopenProposal() {
  const params = new URLSearchParams(window.location.search);
  const reopenId = params.get("reopen");
  if (!reopenId) return;

  // The fetch below + everything that follows it (pre-filling a dozen+
  // pieces of state, rendering line items/tier strips, then jumping to
  // Step 04) can visibly take a few seconds — without this, the page
  // just sits on Step 01's blank paste box the whole time with no
  // indication anything is happening, then jumps straight to Step 04.
  const overlay = document.getElementById("reopen-loading-overlay");
  overlay.classList.remove("hidden");

  try {
    const res = await fetch(`/api/proposal/${encodeURIComponent(reopenId)}/reopen`);
    if (!res.ok) {
      alert("Could not reopen that proposal: " + res.statusText);
      return;
    }
    const data = await res.json();

    state.parsed = data.request || {};
    state.strategyBrief = data.strategy_brief || null;
    // Step 05/07 AI results and the proposal id itself — previously never
    // restored at all (they're only ever set by a fresh /api/generate's
    // showResult()), which silently left "Regenerate emails" and "Upload
    // to Drive" inert after a reopen even though the original run's
    // results were sitting right there in reopen_state.
    state.roadblocks = data.roadblocks || null;
    state.enrichment = data.enrichment || null;
    state.proposalId = reopenId;
    // The exact Step 01 paste, when this proposal was generated after that
    // started being saved — refills the textarea below. Older proposals
    // have nothing here; the box is just left blank, same as today.
    state.rawNotionText = data.raw_notion_text || null;
    // Reopening carries the REAL title from when this proposal was last
    // generated — show it verbatim rather than a fresh live-guess.
    state.finalProposalTitle = data.proposal_title || null;
    // MUST be restored before renderLineItems()/renderMonthlyBreakdown()
    // run below — unlike mbDistributionMode (a preview-only default that
    // always resets to "even"), time_unit determines the FORMAT of the
    // monthly_allocations keys this same payload is about to restore
    // (see reopen_state's own comment in main.py). Absent entirely on a
    // proposal generated before this feature existed — defaults to
    // "month", correct for every such proposal since that was the only
    // granularity that existed then.
    state.timeUnit = data.time_unit || "month";

    // Restore Add-Ons picks (absent entirely on a proposal generated before
    // this feature existed — defaults to none picked, not an error).
    state.addons = {};
    (data.addons || []).forEach(a => { state.addons[a.product_name] = a.amount; });
    renderAddonsModule();

    // Multi-tier restore — every option the proposal actually had, not a
    // hardcoded single "A". The full per-tier data (label/name/geo/dates/
    // line_items/avails_data) has been in reopen_state all along; this was
    // a pure restore-side bug, never a save-side one. Falls back to the
    // flat legacy line_items/avails_data shape for a proposal saved before
    // tiers existed at all.
    const wireTiers = (data.tiers && data.tiers.length)
      ? [...data.tiers].sort((a, b) => a.label.localeCompare(b.label))
      : [{
          label: "A", name: null, geo: null, start_date: null, end_date: null,
          line_items: data.line_items || [], avails_data: data.avails_data || {},
        }];
    const migratedTiers = wireTiers.map(_migrateReopenedTier);
    const [active, ...rest] = migratedTiers;

    state.activeTierLabel = active.label;
    state.activeTierName = active.name;
    state.activeTierGeo = active.geo;
    state.activeTierStartDate = active.startDate;
    state.activeTierEndDate = active.endDate;
    state.lineItems = active.lineItems;
    state.availsData = active.availsData;
    state.activeTierPeriodMergeGroups = active.periodMergeGroups || [];
    state.tiers = rest;
    _applyTimeUnitLabels();

    // Forced export-tab selections, if the planner had overridden any
    // before generating — reuses suggestedTabs' own existing checkbox-sync
    // loop in renderGenerateSummary() rather than adding a second one.
    state.suggestedTabs = data.force_tabs || null;

    fillForm(state.parsed);
    const digits = (state.parsed.notion_id || "").replace(/^EVC-/, "");
    document.getElementById("notion-id-input").value = digits;
    document.getElementById("notion-id-pill").textContent = state.parsed.notion_id || "";
    document.getElementById("notion-input").value = state.rawNotionText || "";
    renderMatchedProducts(state.parsed);

    renderLineItems();
    renderAllTierTabStrips();
    document.getElementById("tier-geo-input").value = state.activeTierGeo || "";
    document.getElementById("tier-start-date-input").value = _toIsoDateString(state.activeTierStartDate);
    document.getElementById("tier-end-date-input").value = _toIsoDateString(state.activeTierEndDate);
    _syncTierOverridePanelOpen();
    // A reopened proposal already has every step's data (it was fully
    // generated once) — let the nav pills jump anywhere immediately
    // instead of only unlocking as the planner re-visits each step.
    state.furthestStep = 8;
    goToStep(4);  // straight to Curate — the paste/review content is already known
  } catch (e) {
    alert("Reopen failed: " + e.message);
  } finally {
    overlay.classList.add("hidden");
  }
}

// Converts one tier from the /api/generate WIRE shape (snake_case,
// line_items/avails_data — what reopen_state stores verbatim) to the
// in-memory SNAPSHOT shape switchTier()/addTier() use (camelCase
// lineItems/availsData/startDate/endDate), and backfills a stable id onto
// any line item saved before ids existed — the same migration the old
// single-tier reopen code did, just applied to every tier now that all of
// them actually get restored instead of only the flattened active one.
function _migrateReopenedTier(t) {
  const availsData = { ...(t.avails_data || {}) };
  const lineItems = (t.line_items || []).map(li => {
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
  return {
    label: t.label || "A",
    name: t.name || null,
    geo: t.geo || null,
    startDate: t.start_date || null,
    endDate: t.end_date || null,
    lineItems,
    availsData,
    periodMergeGroups: t.period_merge_groups || [],
  };
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
  // Footer product count — was a hardcoded "57 products" that had already
  // drifted from the real (larger) catalog size; derived live from the
  // same flat index above so it can't go stale again as products are
  // added/removed.
  const footerCount = document.getElementById("footer-product-count");
  if (footerCount) footerCount.textContent = Object.keys(state.productIndex).length;
  // Populate every product picker dropdown — add-ons are excluded here,
  // they're not "products" a campaign is built around and have their own
  // module (below the line-items table) instead, with no suggested budget.
  _populateProductPicker(document.getElementById("product-picker"));
  _populateProductPicker(document.getElementById("step2-product-picker"));
  renderAddonsModule();
}

function _populateProductPicker(selectEl) {
  if (!selectEl) return;
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
    selectEl.appendChild(group);
  }
}

function wireEvents() {
  document.getElementById("parse-btn").addEventListener("click", onParse);

  // "My Proposal History" modal (Step 01)
  document.getElementById("my-proposals-btn").addEventListener("click", openMyProposalsModal);
  document.getElementById("my-proposals-close-btn").addEventListener("click", closeMyProposalsModal);
  document.getElementById("my-proposals-modal").addEventListener("click", (e) => {
    if (e.target.id === "my-proposals-modal") closeMyProposalsModal();  // backdrop click
  });
  document.getElementById("my-proposals-search").addEventListener("input", _debounce((e) => {
    state.myProposals.search = e.target.value;
    state.myProposals.page = 1;
    loadMyProposals();
  }, 350));
  document.getElementById("my-proposals-prev-btn").addEventListener("click", () => {
    if (state.myProposals.page <= 1) return;
    state.myProposals.page -= 1;
    loadMyProposals();
  });
  document.getElementById("my-proposals-next-btn").addEventListener("click", () => {
    if (state.myProposals.page >= state.myProposals.totalPages) return;
    state.myProposals.page += 1;
    loadMyProposals();
  });

  // "Search Notion Requests" modal (Step 01)
  document.getElementById("notion-search-btn").addEventListener("click", openNotionSearchModal);
  document.getElementById("notion-search-close-btn").addEventListener("click", closeNotionSearchModal);
  document.getElementById("notion-search-modal").addEventListener("click", (e) => {
    if (e.target.id === "notion-search-modal") closeNotionSearchModal();  // backdrop click
  });
  document.querySelectorAll("#notion-search-status-tabs .tier-tab").forEach(btn => {
    btn.addEventListener("click", () => {
      if (state_notionSearch.status === btn.dataset.status) return;
      state_notionSearch.status = btn.dataset.status;
      document.querySelectorAll("#notion-search-status-tabs .tier-tab").forEach(b => b.classList.toggle("active", b === btn));
      loadNotionSearchResults();
    });
  });
  document.getElementById("notion-reference-close-btn").addEventListener("click", () => {
    document.getElementById("notion-reference-panel").classList.add("hidden");
  });

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
      if (state.step === 5) syncAvailsFromGrid();
      goToStep(target);
    });
  });

  // Strategy step
  document.getElementById("strategy-skip-btn").addEventListener("click", () => onNext(4));
  document.getElementById("strategy-confirm-btn").addEventListener("click", () => onNext(4));
  // Plain "start over, no specific feedback" regenerate — distinct from
  // "Refine this plan" below, which requires typed feedback. Same
  // no-confirmation-dialog pattern Roadblocks' own equivalent button
  // already uses.
  document.getElementById("strategy-regenerate-btn").addEventListener("click", () => onStrategyGenerate());
  // Same no-confirmation-dialog pattern as Regenerate right above — the
  // planner can always Regenerate back to the consistent-with-Step-02
  // default afterward, so this isn't a destructive/hard-to-undo action.
  document.getElementById("strategy-new-mix-btn").addEventListener("click", () => onStrategyGenerate(null, "new_mix"));
  document.getElementById("adpresence-run-btn").addEventListener("click", onAdPresenceCheck);
  document.getElementById("reprompt-btn").addEventListener("click", () => {
    document.getElementById("reprompt-area").classList.remove("hidden");
    document.getElementById("reprompt-btn").style.display = "none";
  });
  document.getElementById("reprompt-cancel-btn").addEventListener("click", () => {
    document.getElementById("reprompt-area").classList.add("hidden");
    document.getElementById("reprompt-btn").style.display = "";
  });
  document.getElementById("reprompt-submit-btn").addEventListener("click", onStrategyReprompt);

  // Proposal name bar — editable campaign-name segment.
  document.getElementById("proposal-name-edit-btn").addEventListener("click", onEditProposalNameClick);
  document.getElementById("proposal-name-save-btn").addEventListener("click", onSaveProposalNameEdit);
  document.getElementById("proposal-name-cancel-btn").addEventListener("click", onCancelProposalNameEdit);
  document.getElementById("proposal-name-edit-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") onSaveProposalNameEdit();
    if (e.key === "Escape") onCancelProposalNameEdit();
  });

  // Monthly Breakdown — default-split mode toggle. Wired once here (not
  // re-wired per render, unlike the per-line-item controls in
  // _mbWireLineItemBlocks — this static pair of buttons never gets its
  // innerHTML replaced); renderMonthlyBreakdown() just updates which one
  // shows as .active each time it runs.
  document.querySelectorAll("#mb-mode-tabs .tier-tab").forEach(btn => {
    btn.addEventListener("click", () => {
      if (state.mbDistributionMode === btn.dataset.mode) return;
      state.mbDistributionMode = btn.dataset.mode;
      renderMonthlyBreakdown();
    });
  });

  // Roadblocks step
  document.getElementById("monthly-breakdown-skip-btn").addEventListener("click", () => onNext(7));
  document.getElementById("roadblocks-skip-btn").addEventListener("click", () => onNext(8));
  document.getElementById("roadblocks-regenerate-btn").addEventListener("click", () => onRoadblocksGenerate());

  // Curation
  // Week/Month/Quarter toggle. Wired once (not re-wired per render — see
  // the mb-mode-tabs comment above for why); renderLineItems()/onTimeUnitChange
  // keep .active in sync.
  document.querySelectorAll("#time-unit-toggle .time-unit-slider-option").forEach(btn => {
    btn.addEventListener("click", () => onTimeUnitChange(btn.dataset.unit));
  });
  document.getElementById("add-product-btn").addEventListener("click", onAddProduct);
  document.getElementById("step2-add-product-btn").addEventListener("click", onAddParsedProduct);
  document.getElementById("recommend-btn").addEventListener("click", onRecommend);
  document.getElementById("scale-to-total-btn").addEventListener("click", onScaleToTotal);
  const budgetTarget = document.getElementById("total-budget-target");
  budgetTarget.addEventListener("focus", () => {
    const raw = parseFormattedInput(budgetTarget.value);
    budgetTarget.value = raw === null ? "" : String(raw);
  });
  budgetTarget.addEventListener("blur", () => {
    const raw = parseFormattedInput(budgetTarget.value);
    budgetTarget.value = raw === null ? "" : formatBudgetInputValue(raw);
  });
  budgetTarget.addEventListener("keydown", e => { if (e.key === "Enter") budgetTarget.blur(); });
  document.getElementById("add-tier-btn").addEventListener("click", () => addTier());
  document.getElementById("tier-geo-input").addEventListener("input", (e) => {
    state.activeTierGeo = e.target.value.trim() || null;
  });
  document.getElementById("tier-start-date-input").addEventListener("input", (e) => {
    state.activeTierStartDate = e.target.value.trim() || null;
  });
  document.getElementById("tier-end-date-input").addEventListener("input", (e) => {
    state.activeTierEndDate = e.target.value.trim() || null;
  });
  document.getElementById("curate-agency-fee-input").addEventListener("input", onCurateAgencyFeeInput);
  // On blur (not on every keystroke, which would fight an in-progress
  // "0.15" being typed digit-by-digit): re-display whatever the
  // NORMALIZED value actually is, so a planner who typed "15" sees it
  // become "0.15" once they're done — visible confirmation of the
  // interpretation, not a silent invisible conversion.
  document.getElementById("curate-agency-fee-input").addEventListener("blur", (e) => {
    e.target.value = state.parsed.agency_fee != null ? state.parsed.agency_fee : "";
  });

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
    _resetEmailRepromptScope();
  });
  document.getElementById("email-reprompt-submit-btn").addEventListener("click", onEmailReprompt);

  // Gamma outline — same reprompt UI shape as the emails above, but this
  // is the ONLY AI call anywhere in the Gamma-outline feature (building
  // the outline itself is pure client-side string assembly, no request).
  document.getElementById("gamma-reprompt-btn").addEventListener("click", () => {
    document.getElementById("gamma-reprompt-area").classList.remove("hidden");
    document.getElementById("gamma-reprompt-btn").style.display = "none";
  });
  document.getElementById("gamma-reprompt-cancel-btn").addEventListener("click", () => {
    document.getElementById("gamma-reprompt-area").classList.add("hidden");
    document.getElementById("gamma-reprompt-btn").style.display = "";
    document.getElementById("gamma-reprompt-input").value = "";
  });
  document.getElementById("gamma-reprompt-submit-btn").addEventListener("click", onGammaOutlineReprompt);

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
  state.rawNotionText = null;
  state.suggestedTabs = null;
  state.strategyBrief = null;
  state.roadblocks = null;
  state.lineItems = [];
  state.availsData = {};
  state.tiers = [];
  state.activeTierLabel = "A";
  state.activeTierName = null;
  // activeTierGeo wasn't reset here before (pre-existing gap, same class as
  // the two below) — fixing alongside adding the date fields so "New
  // Proposal" can't leak a prior proposal's tier overrides into a new one.
  state.activeTierGeo = null;
  state.activeTierStartDate = null;
  state.activeTierEndDate = null;
  state.manualCampaignNameOverride = null;
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
  document.getElementById("tier-geo-input").value = "";
  document.getElementById("tier-start-date-input").value = "";
  document.getElementById("tier-end-date-input").value = "";
  _syncTierOverridePanelOpen();
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

// The Geo/Start/End override fields sit inside a collapsed-by-default
// <details> now (decluttering Step 04's header — most proposals never
// set these, they use Step 02's campaign-level values) — but a
// COLLAPSED panel must never SILENTLY hide an override that's already
// active. Called anywhere the 3 fields' values get (re)assigned from
// state, so switching to a tier that already has one auto-expands it,
// and clearing them (a fresh parse) collapses it back.
function _syncTierOverridePanelOpen() {
  const panel = document.getElementById("tier-override-panel");
  if (!panel) return;
  const hasOverride = !!(
    document.getElementById("tier-geo-input").value ||
    document.getElementById("tier-start-date-input").value ||
    document.getElementById("tier-end-date-input").value
  );
  panel.open = hasOverride;
}

function buildProposalNamePreview(parsed) {
  if (!parsed) return "";
  const shortId = (parsed.notion_id || "").trim() || "----";
  // Mirrors ai_enricher.build_proposal_title()'s "{Client Name} - {Order
  // Description}" convention — the client name appears exactly once,
  // never duplicated inside the order-description segment. Pre-Generate,
  // the only possible "order description" is a manual override (no AI
  // enrichment has run yet); with none set, this just shows the client
  // name alone, same as before this convention existed.
  const clientName = _previewTitleCase((parsed.client_name || "").trim());
  const override = (state.manualCampaignNameOverride || "").trim();
  let campaignName;
  if (override && clientName && !override.toLowerCase().startsWith(clientName.toLowerCase())) {
    campaignName = `${clientName} - ${override}`;
  } else if (override) {
    campaignName = override;
  } else {
    campaignName = clientName || "Campaign";
  }
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
  // Any re-render (switching steps/tiers, a fresh parse) closes a
  // still-open edit box rather than leaving a stray input floating
  // around showing a now-possibly-stale value.
  document.getElementById("proposal-name-edit-row").classList.add("hidden");
  document.getElementById("proposal-name-view").classList.remove("hidden");
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

// The campaign-name segment is the ONE AI-guessed/client-name-derived
// part of the naming convention — editable here without touching the
// ID/date/doc-type segments, which stay derived automatically (see
// build_proposal_title on the server, buildProposalNamePreview() here).
function onEditProposalNameClick() {
  const current = state.manualCampaignNameOverride
    || _previewTitleCase(((state.parsed && state.parsed.client_name) || "Campaign").trim());
  document.getElementById("proposal-name-edit-input").value = current;
  document.getElementById("proposal-name-view").classList.add("hidden");
  document.getElementById("proposal-name-edit-row").classList.remove("hidden");
  document.getElementById("proposal-name-edit-input").focus();
}

function onSaveProposalNameEdit() {
  const value = document.getElementById("proposal-name-edit-input").value.trim();
  state.manualCampaignNameOverride = value || null;
  // A previously-generated REAL title can't retroactively change — fall
  // back to the live preview (which DOES pick up the new override
  // immediately) until the planner regenerates; the next /api/generate
  // call also sends this override, so the eventual real title matches.
  state.finalProposalTitle = null;
  updateProposalNameBar();
}

function onCancelProposalNameEdit() {
  updateProposalNameBar();
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
  for (let i = 1; i <= 8; i++) {
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
  if (state.step === 5) syncAvailsFromGrid();

  if (n === 3) {
    goToStep(3);
    // Only auto-generate on the FIRST visit (state.strategyBrief is only
    // ever set on a SUCCESSFUL renderStrategyBrief() call — see that
    // function — so a prior failed attempt still correctly auto-retries
    // here, only a genuine existing brief blocks it). Before this gate,
    // clicking Continue from Step 02 unconditionally regenerated the
    // brief EVERY time, discarding whatever was already there and
    // burning a real API call even when nothing had changed — exactly
    // the "goes back, then it reruns" behavior reported. The explicit
    // Regenerate/Refine buttons (see renderStrategyBrief) are how a
    // planner gets a fresh one on purpose now.
    if (!state.strategyBrief) onStrategyGenerate();
    return;
  }
  if (n === 4) {
    // Pre-populate budget + line items for Curate step
    const budget = state.parsed.monthly_budget || parseBudgetFromRenewal(state.parsed);
    if (budget) {
      document.getElementById("total-budget-target").value = formatBudgetInputValue(Number(budget));
    }
    // Reference/override box — pre-filled from Step 02's parsed value so
    // the planner sees what was inferred without flipping back a step;
    // editing it here writes straight back into state.parsed.agency_fee
    // (see onCurateAgencyFeeInput), the same field Step 02's own input reads.
    document.getElementById("curate-agency-fee-input").value =
      state.parsed.agency_fee != null ? state.parsed.agency_fee : "";
    // Per-tier geo/date override boxes — reflect whichever tier is currently active.
    document.getElementById("tier-geo-input").value = state.activeTierGeo || "";
    document.getElementById("tier-start-date-input").value = _toIsoDateString(state.activeTierStartDate);
    document.getElementById("tier-end-date-input").value = _toIsoDateString(state.activeTierEndDate);
    _syncTierOverridePanelOpen();
    if (state.lineItems.length === 0) {
      state.lineItems = (state.parsed.products_selected || []).map(name => {
        const p = state.productIndex[name];
        return {
          id: newLineItemId(),
          product_name: name,
          // state.timeUnit is always "month" (its default) the first time
          // this pre-fill runs — nothing earlier in the flow can have
          // changed it yet — so no scaling needed here specifically, but
          // _timeUnitMinimumScale() is a no-op (×1) for "month" anyway.
          monthly_budget: p ? (p.minimum_spend || 0) * _timeUnitMinimumScale() : 0,
          months: state.parsed.total_months || 3,
          rate_override: null,
          notes_override: null,
          target_override: null,
          target_secondary: null,
          estimated_cpm_override: null,
          buying_model_override: null,
          is_added_value: false,
          added_value_pct: null,
          // Best-effort default from Step 02's inferred campaign goal —
          // still a fully editable per-line dropdown, this just saves the
          // planner from setting the same thing on every line by hand.
          objective_override: _mapCampaignGoalToObjective(state.parsed.campaign_goal),
          // Step 06's optional Monthly Breakdown — {"YYYY-MM": dollars}.
          // null/empty means this line doesn't use it (see
          // app/services/monthly_allocation.py's docstring: no separate
          // enabled flag, inferred purely from data presence).
          monthly_allocations: null,
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
      // since edited or removed. Capped at 3 additional options — tied to
      // Step 02 only ever parsing 4 tier-target fields (tier_1..tier_4),
      // NOT to the general per-tab option cap (TIER_LABELS, now 10) — a 4th
      // non-blank tier value beyond that has nowhere to go and is skipped.
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
  if (n === 5) renderAvailsGrid();
  if (n === 6) renderMonthlyBreakdown();
  if (n === 7) {
    goToStep(7);
    // Same "only auto-generate once" gate as Step 3 above — state.roadblocks
    // is only ever set on a successful renderRoadblocks() call, so a prior
    // failure still correctly auto-retries; an existing result does not.
    // The always-visible "↺ Regenerate" button is how a planner gets a
    // fresh one on purpose (e.g. after changing the curated mix).
    if (!state.roadblocks) onRoadblocksGenerate();
    return;
  }
  if (n === 8) renderGenerateSummary();
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
    li.monthly_budget = Math.max(share, p ? (p.minimum_spend || 0) * _timeUnitMinimumScale() : 0);
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
  btn.innerHTML = '<span class="btn-inline-spinner"></span>Parsing…';
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
    // Kept verbatim (not re-derived from the DOM later) so it survives a
    // reopen — see reopen_state's raw_notion_text and maybeReopenProposal().
    state.rawNotionText = text;
    state.suggestedTabs = data.suggested_tabs;
    state.lineItems = [];  // reset so step 3 re-populates from fresh parse
    state.availsData = {};
    state.tiers = [];
    state.activeTierLabel = "A";
    state.activeTierName = null;
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
    } else if (el.type === "date") {
      // A native date input silently renders blank for anything that
      // isn't exactly YYYY-MM-DD — this app's dates come from free-text
      // parsing, so they aren't guaranteed to already be that. Normalize
      // rather than let a genuinely-parsed date look like a parse failure.
      el.value = _toIsoDateString(req[f]);
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
    // agency_fee needs the same "15" -> 0.15 normalization
    // onCurateAgencyFeeInput applies — this is the OTHER place a planner
    // can set/override it directly (Step 02's own field), and without
    // this it fed a raw whole-number percent straight into
    // _netToGross/_grossToNet the moment Step 04 rendered, same bug.
    if (f === "agency_fee") {
      state.parsed[f] = _normalizeAgencyFee(el.value);
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

  const pills = matched.map((name, idx) =>
    `<span class="matched-pill">${escapeHtml(name)}<button type="button" class="matched-pill-remove" data-remove-product-idx="${idx}" title="Remove">×</button></span>`
  );

  if (rawPick && matched.length === 0) {
    pills.push(`<span class="matched-pill unmatched">No catalog match for: ${escapeHtml(rawPick.substring(0, 60))}</span>`);
  }

  list.innerHTML = pills.join(" ");
  row.classList.remove("hidden");

  // Editable — removes straight from req.products_selected (== state.parsed
  // at both call sites) so Step 03's brief and Step 04's Curate pre-fill,
  // which both read that same array directly, see the edit immediately.
  list.querySelectorAll("[data-remove-product-idx]").forEach(btn => {
    btn.addEventListener("click", () => {
      req.products_selected.splice(parseInt(btn.dataset.removeProductIdx), 1);
      renderMatchedProducts(req);
    });
  });
}

// Step 02's own "+ Add product…" — mirrors Step 04's onAddProduct(), just
// appending a plain catalog name to products_selected instead of building
// a full line-item object (Step 02 has no budget/rate/etc. yet to attach).
function onAddParsedProduct() {
  const picker = document.getElementById("step2-product-picker");
  const name = picker.value;
  if (!name) return;
  if (!state.parsed) return;
  if (!state.parsed.products_selected) state.parsed.products_selected = [];
  if (!state.parsed.products_selected.includes(name)) {
    state.parsed.products_selected.push(name);
  }
  picker.value = "";
  renderMatchedProducts(state.parsed);
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
  document.getElementById("strategy-regenerate-btn").style.display = "none";
  document.getElementById("strategy-new-mix-btn").style.display = "none";
  document.getElementById("strategy-confirm-btn").style.display = "none";
  document.getElementById("strategy-download-link").classList.add("hidden");
  document.getElementById("strategy-search-note").classList.add("hidden");
  document.getElementById("reprompt-input").value = "";
}

async function onStrategyGenerate(reprompt = null, mode = "consistent") {
  _resetStrategyUI();
  const loadingEl = document.getElementById("strategy-loading");
  const loadingText = document.getElementById("strategy-loading-text");
  loadingEl.classList.remove("hidden");
  loadingText.textContent = mode === "new_mix"
    ? `Researching an overall media mix for ${state.parsed.client_name || "client"}…`
    : `Researching ${state.parsed.client_name || "client"}…`;

  try {
    const res = await fetch("/api/strategy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request: state.parsed, reprompt, mode, ad_presence: _adPresenceToCarry() }),
    });
    const brief = await _readJsonResponse(res);
    loadingEl.classList.add("hidden");

    if (brief.error && !brief.strategy_summary) {
      _showStrategyError(brief.error);
      return;
    }

    renderStrategyBrief(brief);
  } catch (e) {
    loadingEl.classList.add("hidden");
    _showStrategyError("Request failed: " + e.message);
  }
}

// A proxy timeout or crash returns HTML, not JSON — surface the HTTP status instead of "Unexpected token <".
async function _readJsonResponse(res) {
  let data;
  try {
    data = await res.json();
  } catch (_) {
    throw new Error(res.ok ? "the server sent an unreadable response" : `server returned ${res.status} ${res.statusText}`);
  }
  if (!res.ok) throw new Error(data.detail || `server returned ${res.status} ${res.statusText}`);
  return data;
}

function _showStrategyError(message) {
  const errEl = document.getElementById("strategy-error");
  errEl.classList.remove("hidden");
  const keyMissing = /OPENAI_API_KEY|openai package/i.test(message);
  errEl.innerHTML = `<strong>Strategy brief unavailable:</strong> ${escapeHtml(message)}<br>
    <small>${keyMissing ? "Set <code>OPENAI_API_KEY</code> to enable this step. You can" : "Click Regenerate to try again, or"} skip and curate manually.</small>`;
  if (!keyMissing) document.getElementById("strategy-regenerate-btn").style.display = "";
}

// A prior on-demand check is only reused if it was run for the same client name/website.
function _adPresenceToCarry() {
  const ap = state.strategyBrief && state.strategyBrief.ad_presence;
  if (!ap || !_adPresenceHasResults(ap)) return null;
  if (!ap.inputs) return ap;
  const p = state.parsed || {};
  const same = (ap.inputs.client_name || "") === (p.client_name || "").trim()
    && (ap.inputs.client_website || "") === (p.client_website || "").trim();
  return same ? ap : null;
}

async function onStrategyReprompt() {
  const text = document.getElementById("reprompt-input").value.trim();
  if (!text) { alert("Enter your correction first."); return; }
  await onStrategyGenerate(text);
}

function renderStrategyBrief(brief) {
  // A check that finished after this brief was requested still belongs to it.
  let adPresenceFresh = false;
  if (state.strategyBrief && state.strategyBrief !== brief && !_adPresenceHasResults(brief.ad_presence)) {
    const carried = _adPresenceToCarry();
    if (carried) {
      brief.ad_presence = carried;
      adPresenceFresh = true;
    }
  }
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
  renderAdPresence(brief.ad_presence || null, { fresh: adPresenceFresh });
  if (adPresenceFresh) _debouncedRebuildStrategyDoc();

  // Budget note
  const budget = state.parsed.monthly_budget || parseBudgetFromRenewal(state.parsed) || 0;
  const months = state.parsed.total_months || 3;
  const budgetNote = document.getElementById("brief-budget-note");
  budgetNote.textContent = budget
    ? `Based on $${money(budget).replace("$","")}/mo × ${months} mo = ${money(budget * months)} total`
    : "";

  // Tactics cards — each has a "include in brief" checkbox (defaults
  // checked/selected: undefined and selected: true both count as
  // selected, so a brief from before this feature existed, or a fresh
  // one that never explicitly set the flag, starts fully selected
  // rather than empty). Selection is stored directly on each tactic
  // object in state.strategyBrief — see _selectedTactics() for where
  // it's actually consulted (Suggest Mix, Generate) and
  // onTacticSelectionChange() for the downloadable .docx rebuild.
  const tacticsEl = document.getElementById("brief-tactics");
  tacticsEl.innerHTML = "";
  (brief.recommended_tactics || []).forEach((t, idx) => {
    if (t.selected === undefined) t.selected = true;
    const pct = t.suggested_budget_pct || 0;
    const alloc = budget ? Math.round(budget * pct / 100 / 50) * 50 : null;
    const card = document.createElement("div");
    card.className = "tactic-card" + (t.selected ? "" : " tactic-card-deselected");
    card.innerHTML = `
      <div class="tactic-header">
        <label class="tactic-select" title="Include this tactic in the brief (and what feeds Suggest Mix / the final proposal)">
          <input type="checkbox" class="tactic-select-checkbox" data-idx="${idx}" ${t.selected ? "checked" : ""} />
        </label>
        <span class="tactic-family">${escapeHtml(t.product_family)}</span>
        <span class="tactic-pct">${pct}%${alloc ? ` · ~${money(alloc)}/mo` : ""}</span>
      </div>
      <p class="tactic-rationale">${escapeHtml(t.rationale)}</p>
      <p class="tactic-data">📊 ${escapeHtml(t.data_point)} <em class="tactic-citation">(${escapeHtml(t.citation)})</em></p>
      <p class="tactic-advantage">⚡ ${escapeHtml(t.entravision_advantage)}</p>
      <p class="tactic-min-note">ⓘ This % is a ceiling for the whole ${escapeHtml(t.product_family)} tactic — if you curate more than one product under it in Step 04, each one still has its own separate minimum spend, not a shared pool.</p>
    `;
    tacticsEl.appendChild(card);
  });
  tacticsEl.querySelectorAll(".tactic-select-checkbox").forEach(cb => {
    cb.addEventListener("change", (e) => onTacticSelectionChange(parseInt(e.target.dataset.idx, 10), e.target.checked));
  });

  // Key insights
  const insightsList = document.getElementById("brief-insights");
  insightsList.innerHTML = (brief.key_insights || [])
    .map(i => `<li>${escapeHtml(i)}</li>`).join("");

  document.getElementById("strategy-brief").classList.remove("hidden");
  document.getElementById("reprompt-btn").style.display = "";
  document.getElementById("strategy-regenerate-btn").style.display = "";
  document.getElementById("strategy-new-mix-btn").style.display = "";
  document.getElementById("strategy-confirm-btn").style.display = "";

  const dlLink = document.getElementById("strategy-download-link");
  if (brief.doc_token) {
    dlLink.href = `/api/download-strategy/${brief.doc_token}`;
    dlLink.classList.remove("hidden");
  } else {
    dlLink.classList.add("hidden");
  }
}

// Debounces a function — waits `ms` after the LAST call before actually
// running, cancelling any pending run each time it's called again.
function _debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

// The tactics actually feeding downstream (Suggest Mix, the final
// Generate call) — everything EXCEPT one the planner explicitly
// unchecked. Never mutates state.strategyBrief itself, so re-checking a
// box later still has the tactic's original rationale/data intact —
// this is a read-time filter, not a destructive one.
function _selectedTactics() {
  const tactics = (state.strategyBrief && state.strategyBrief.recommended_tactics) || [];
  return tactics.filter(t => t.selected !== false);
}

// What every downstream AI call (Suggest Mix, Roadblocks, Generate)
// should actually send as `strategy_brief` — the confirmed brief, but
// with recommended_tactics narrowed to what the planner has checked.
// null when there's no brief at all (Step 03 was skipped), matching
// every existing call site's own `|| null` fallback.
function _briefWithSelectedTactics() {
  if (!state.strategyBrief) return null;
  return { ...state.strategyBrief, recommended_tactics: _selectedTactics() };
}

function onTacticSelectionChange(idx, checked) {
  const tactics = state.strategyBrief.recommended_tactics;
  if (!tactics || !tactics[idx]) return;
  tactics[idx].selected = checked;
  const card = document.querySelectorAll(".tactic-card")[idx];
  if (card) card.classList.toggle("tactic-card-deselected", !checked);
  _debouncedRebuildStrategyDoc();
}

// Debounced so flipping a few checkboxes in a row doesn't fire a rebuild
// request per click — same reasoning as the admin proposals search box.
const _debouncedRebuildStrategyDoc = _debounce(async () => {
  const brief = state.strategyBrief;
  if (!brief || !brief.doc_token) return;
  try {
    await fetch(`/api/strategy/${brief.doc_token}/rebuild`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        request: state.parsed,
        client_summary: brief.client_summary,
        market_context: brief.market_context,
        objectives_analysis: brief.objectives_analysis,
        strategy_summary: brief.strategy_summary,
        recommended_tactics: _selectedTactics(),
        key_insights: brief.key_insights,
        ad_presence: brief.ad_presence,
      }),
    });
  } catch (e) {
    // Non-fatal — the download link still serves whatever was last
    // successfully built; the planner can just re-toggle to retry.
    console.error("Strategy brief doc rebuild failed:", e);
  }
}, 600);

const AD_PRESENCE_LANG_LABELS = { en: "English", es: "Spanish" };

const AD_PRESENCE_STATUS_LABELS = {
  active: "ACTIVE", not_found: "NOT FOUND", unsupported: "NOT VERIFIABLE",
  error: "CHECK FAILED", not_checked: "NOT CHECKED",
};

function _adPresenceHasResults(ap) {
  return !!(ap && (ap.meta || ap.google || ap.tiktok) && ["meta", "google", "tiktok"].some(k => ap[k] && Object.keys(ap[k]).length));
}

// Older saved briefs predate the `status` field (and TikTok's old "inconclusive" wording).
function _adPresenceStatus(r) {
  if (r.status) return r.status;
  if (!r.checked) return r.note ? "error" : "not_checked";
  if (r.active) return "active";
  return (r.note || "").toLowerCase().includes("inconclusive") ? "unsupported" : "not_found";
}

function _safeHttpUrl(url) {
  return typeof url === "string" && /^https?:\/\//i.test(url) ? url : "";
}

function _adLangChips(langs) {
  return Object.entries(langs || {})
    .sort((a, b) => b[1] - a[1])
    .map(([code, pct]) => `<span class="adpresence-lang-chip">${Math.round(pct * 100)}% ${escapeHtml(AD_PRESENCE_LANG_LABELS[code] || code)}</span>`)
    .join("");
}

function _adPresenceHeadline(key, r, status) {
  if (status !== "active") return r.note || "Not checked.";
  if (key === "google") {
    const count = r.ad_count_estimate_display ? `~${r.ad_count_estimate_display} ads` : "Ads found";
    const advertisers = (r.advertisers || []).slice(0, 3).join(", ");
    return advertisers ? `${count} · ${advertisers}` : count;
  }
  // Only the first page of library results is read, so this is a floor, not the client's total.
  const n = r.high_confidence_count || (r.sample_ads || []).filter(a => a.confidence === "high").length;
  const source = key === "meta" ? "a matching Page" : "a matching advertiser";
  const total = r.result_count_estimate ? ` · ${r.result_count_estimate} for this search` : "";
  return n ? `At least ${n} ad${n === 1 ? "" : "s"} from ${source}${total}` : `Ads from ${source}${total}`;
}

function _adSampleHtml(ad) {
  const body = ad.body || ad.raw_text || "";
  const shortBody = body.length > 280 ? body.slice(0, 280).trimEnd() + "…" : body;
  const link = _safeHttpUrl(ad.library_url);
  const meta = [
    ad.started_running_on ? `Started ${escapeHtml(ad.started_running_on)}` : "",
    ad.language ? escapeHtml(AD_PRESENCE_LANG_LABELS[ad.language] || ad.language) : "",
    ad.destination_domain ? escapeHtml(ad.destination_domain) : "",
    ad.cta ? `CTA: ${escapeHtml(ad.cta)}` : "",
    ad.has_versions ? "multiple versions" : "",
  ].filter(Boolean).join(" · ");
  return `
    <div class="adpresence-ad">
      <div class="adpresence-ad-head">
        <strong>${escapeHtml(ad.page_name || ad.page_name_guess || "Unknown Page")}</strong>
        <span class="adpresence-conf conf-${escapeAttr(ad.confidence || "low")}">${escapeHtml(ad.confidence || "low")} match</span>
      </div>
      ${ad.headline ? `<div class="adpresence-ad-headline">${escapeHtml(ad.headline)}</div>` : ""}
      ${shortBody ? `<div class="adpresence-ad-body">${escapeHtml(shortBody)}</div>` : ""}
      <div class="adpresence-ad-meta">${meta}${link ? `${meta ? " · " : ""}<a href="${escapeHtml(link)}" target="_blank" rel="noopener">Open in Ad Library ↗</a>` : ""}</div>
    </div>`;
}

function _adPresenceDetailsHtml(key, r, status) {
  const parts = [];
  if (key === "meta") {
    const ads = r.sample_ads || [];
    const strong = ads.filter(a => a.confidence === "high");
    const weak = ads.filter(a => a.confidence !== "high");
    if (strong.length) parts.push(strong.map(_adSampleHtml).join(""));
    if (weak.length) {
      parts.push(`<details class="adpresence-weak"><summary>Other advertisers mentioning the client (${weak.length})</summary>${weak.map(_adSampleHtml).join("")}</details>`);
    }
    if ((r.queries_tried || []).length) {
      parts.push(`<div class="adpresence-queries">Searched: ${r.queries_tried.map(q => escapeHtml(q)).join(" · ")}</div>`);
    }
  }
  if (key === "google" && (r.advertisers || []).length) {
    parts.push(`<div class="adpresence-queries">Verified advertisers: ${r.advertisers.map(a => escapeHtml(a)).join(", ")}</div>`);
    parts.push(`<div class="adpresence-queries">Google doesn't expose ad copy on this page — open the Transparency Center to see creatives.</div>`);
  }
  const link = _safeHttpUrl(r.search_url || r.library_url || (r.search_urls || [])[0]);
  if (link) {
    const label = key === "meta" ? "Meta Ad Library" : key === "google" ? "Ads Transparency Center" : "TikTok Ad Library";
    parts.push(`<a class="adpresence-open-link" href="${escapeHtml(link)}" target="_blank" rel="noopener">Open ${label} ↗</a>`);
  }
  return parts.join("");
}

function renderAdPresence(adPresence, { fresh = false } = {}) {
  const el = document.getElementById("brief-ad-presence");
  const statusEl = document.getElementById("brief-ad-presence-status");
  const btn = document.getElementById("adpresence-run-btn");
  if (!el) return;

  if (!_adPresenceHasResults(adPresence)) {
    el.innerHTML = "";
    if (btn) btn.textContent = "Check digital ad presence";
    if (statusEl) {
      statusEl.classList.remove("hidden");
      statusEl.textContent = adPresence && adPresence.error
        ? `The check couldn't run: ${adPresence.error}`
        : "Not checked yet — takes about 10–20 seconds and shows the client's current Meta and Google ads, including sample ad copy.";
    }
    return;
  }

  if (btn) btn.textContent = "↻ Re-check";
  if (statusEl) {
    const when = adPresence.checked_at ? new Date(adPresence.checked_at).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "";
    const took = adPresence.elapsed_seconds ? ` · ${Math.round(adPresence.elapsed_seconds)}s` : "";
    const hint = fresh ? " · Regenerate the brief to fold these findings into the strategy." : "";
    statusEl.classList.toggle("hidden", !(when || hint));
    statusEl.textContent = `${when ? `Checked ${when}` : ""}${took}${hint}`.replace(/^ · /, "");
  }

  const platforms = [["meta", "Meta (FB/IG)"], ["google", "Google Ads"], ["tiktok", "TikTok"]];
  el.innerHTML = platforms.map(([key, label]) => {
    const r = adPresence[key] || {};
    const status = _adPresenceStatus(r);
    const langChips = _adLangChips(r.languages);
    const details = _adPresenceDetailsHtml(key, r, status);
    const statusLabel = key === "tiktok" && status === "unsupported" ? "NOT VERIFIABLE (US)" : AD_PRESENCE_STATUS_LABELS[status] || status.toUpperCase();
    return `
      <div class="adpresence-card">
        <div class="adpresence-card-head">
          <span class="adpresence-platform">${escapeHtml(label)}</span>
          <span class="adpresence-status ${escapeAttr(status)}">${escapeHtml(statusLabel)}</span>
        </div>
        <div class="adpresence-detail">${escapeHtml(_adPresenceHeadline(key, r, status))}</div>
        ${langChips ? `<div class="adpresence-langs">${langChips}</div>` : ""}
        ${_spanishGapCallout(key, r, status)}
        ${details ? `<details class="adpresence-more"><summary>View details</summary><div class="adpresence-more-body">${details}</div></details>` : ""}
      </div>
    `;
  }).join("");
}

// Only a hint from the sampled ads, not proof the client runs no Spanish creative anywhere.
function _spanishGapCallout(key, r, status) {
  if (key !== "meta" || status !== "active") return "";
  const p = state.parsed || {};
  const targetsSpanish = /spanish|hispanic|latin/i.test(`${p.language || ""} ${p.demo || ""} ${p.behavioral || ""}`);
  const langs = r.languages || {};
  if (!targetsSpanish || !Object.keys(langs).length || langs.es) return "";
  return `<div class="adpresence-callout">No Spanish-language copy in the sampled ads — a possible Entravision opening.</div>`;
}

async function onAdPresenceCheck() {
  const brief = state.strategyBrief;
  if (!brief) return;
  const p = state.parsed || {};
  const btn = document.getElementById("adpresence-run-btn");
  const statusEl = document.getElementById("brief-ad-presence-status");
  if (!(p.client_name || "").trim() && !(p.client_website || "").trim()) {
    statusEl.classList.remove("hidden");
    statusEl.textContent = "Add the client name or website in Step 02 first.";
    return;
  }
  btn.disabled = true;
  btn.textContent = "Checking…";
  statusEl.classList.remove("hidden");
  statusEl.textContent = "Checking Meta's Ad Library and Google's Ads Transparency Center…";
  try {
    const res = await fetch("/api/ad-presence", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_name: (p.client_name || "").trim(), client_website: (p.client_website || "").trim() }),
    });
    const result = await _readJsonResponse(res);
    if (!_adPresenceHasResults(result)) throw new Error(result.error || "no results came back");
    // Attach to whichever brief is current: a Regenerate may have finished mid-check.
    const current = state.strategyBrief;
    if (!current) return;
    current.ad_presence = result;
    renderAdPresence(result, { fresh: true });
    _debouncedRebuildStrategyDoc();
  } catch (e) {
    const current = state.strategyBrief;
    renderAdPresence(current ? current.ad_presence || null : null);  // keep earlier results on screen
    statusEl.classList.remove("hidden");
    statusEl.textContent = `The check couldn't run: ${e.message}`;
  } finally {
    btn.disabled = false;
    const current = state.strategyBrief;
    btn.textContent = current && _adPresenceHasResults(current.ad_presence) ? "↻ Re-check" : "Check digital ad presence";
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
        strategy_brief: _briefWithSelectedTactics(),
      }),
    });
    const data = await _readJsonResponse(res);
    loadingEl.classList.add("hidden");

    if (data.error && !data.product_roadblocks?.length) {
      const errEl = document.getElementById("roadblocks-error");
      errEl.classList.remove("hidden");
      errEl.innerHTML = `<strong>Roadblocks check unavailable:</strong> ${escapeHtml(data.error)}<br>
        <small>You can regenerate, or skip this step and continue.</small>`;
      document.getElementById("roadblocks-regenerate-btn").style.display = "";
      return;
    }

    renderRoadblocks(data);
  } catch (e) {
    loadingEl.classList.add("hidden");
    const errEl = document.getElementById("roadblocks-error");
    errEl.classList.remove("hidden");
    errEl.textContent = "Request failed: " + e.message;
    document.getElementById("roadblocks-regenerate-btn").style.display = "";
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
// Tiered budget options (up to 10 — "A".."J")
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
    { label: state.activeTierLabel, name: state.activeTierName, geo: state.activeTierGeo, start_date: state.activeTierStartDate, end_date: state.activeTierEndDate, line_items: state.lineItems, avails_data: state.availsData, period_merge_groups: state.activeTierPeriodMergeGroups },
    ...state.tiers.map(t => ({ label: t.label, name: t.name, geo: t.geo, start_date: t.startDate, end_date: t.endDate, line_items: t.lineItems, avails_data: t.availsData, period_merge_groups: t.periodMergeGroups || [] })),
  ].sort((a, b) => a.label.localeCompare(b.label));
}

// Planner-given display name for a tier, wherever "Option {label}" used to
// be hardcoded — checks the active tier and every snapshot, falls back to
// the original "Option {label}" text when nothing's been set.
function _tierDisplayName(label) {
  if (label === state.activeTierLabel) return state.activeTierName || `Option ${label}`;
  const t = state.tiers.find(x => x.label === label);
  return (t && t.name) || `Option ${label}`;
}

// Per-tier geo override, wherever the flat campaign-level Geo used to be
// the only option — same active/snapshot lookup shape as _tierDisplayName,
// but falls back to the campaign-level state.parsed.geo (never a hardcoded
// placeholder) since that's this field's own pre-existing fallback.
function _effectiveGeo(label) {
  const tierGeo = label === state.activeTierLabel
    ? state.activeTierGeo
    : (state.tiers.find(x => x.label === label) || {}).geo;
  return (tierGeo && tierGeo.trim()) || (state.parsed && state.parsed.geo) || "";
}

// Per-tier date overrides, same active/snapshot lookup shape and
// campaign-level fallback as _effectiveGeo above.
function _effectiveStartDate(label) {
  const tierDate = label === state.activeTierLabel
    ? state.activeTierStartDate
    : (state.tiers.find(x => x.label === label) || {}).startDate;
  return (tierDate && tierDate.trim()) || (state.parsed && state.parsed.start_date) || "";
}
function _effectiveEndDate(label) {
  const tierDate = label === state.activeTierLabel
    ? state.activeTierEndDate
    : (state.tiers.find(x => x.label === label) || {}).endDate;
  return (tierDate && tierDate.trim()) || (state.parsed && state.parsed.end_date) || "";
}

function renameTier(label) {
  const current = _tierDisplayName(label);
  const input = prompt("Name this option (shown to the seller/client instead of \"Option " + label + "\" — leave blank to reset):", current === `Option ${label}` ? "" : current);
  if (input === null) return;  // cancelled
  const name = input.trim() || null;
  if (label === state.activeTierLabel) {
    state.activeTierName = name;
  } else {
    const t = state.tiers.find(x => x.label === label);
    if (t) t.name = name;
  }
  renderAllTierTabStrips();
}

function switchTier(label) {
  if (label === state.activeTierLabel) return;
  const idx = state.tiers.findIndex(t => t.label === label);
  if (idx === -1) return;
  const target = state.tiers[idx];

  // Replace the target's snapshot (about to become active) with a fresh
  // snapshot of the tier we're leaving — one swap, order doesn't matter
  // since every lookup here is by label, not position.
  state.tiers.splice(idx, 1, { label: state.activeTierLabel, name: state.activeTierName, geo: state.activeTierGeo, startDate: state.activeTierStartDate, endDate: state.activeTierEndDate, lineItems: state.lineItems, availsData: state.availsData, periodMergeGroups: state.activeTierPeriodMergeGroups });

  state.activeTierLabel = label;
  state.activeTierName = target.name || null;
  state.activeTierGeo = target.geo || null;
  state.activeTierStartDate = target.startDate || null;
  state.activeTierEndDate = target.endDate || null;
  state.lineItems = target.lineItems;
  state.availsData = target.availsData;
  state.activeTierPeriodMergeGroups = target.periodMergeGroups || [];
  state.rateOverrideOpen.clear();
  state.objectiveOtherOpen.clear();

  renderLineItems();
  renderAvailsGrid();
  // Safe to call regardless of which step is actually showing right now
  // (it only ever writes into Step 06's own DOM nodes) — needed so
  // switching tiers FROM Step 06 itself (via the new tier-tabs-monthly
  // strip) actually shows the newly-active tier's own Monthly Breakdown
  // instead of leaving the PREVIOUS tier's content on screen.
  renderMonthlyBreakdown();
}

// targetBudget: when given, the clone's line-item budgets are rescaled to
// sum to it (used for auto-pre-filling Step 02's Tier #1-4 amounts as
// options); omitted (manual "+ Add Option" click), the clone just keeps
// the source tier's current budgets unchanged, for the planner to adjust.
function addTier(targetBudget) {
  const totalTiers = 1 + state.tiers.length;
  if (totalTiers >= TIER_LABELS.length) return;
  const usedLabels = new Set([state.activeTierLabel, ...state.tiers.map(t => t.label)]);
  const nextLabel = TIER_LABELS.find(l => !usedLabels.has(l));
  if (!nextLabel) return;

  // Snapshot the tier we're leaving active...
  state.tiers.push({ label: state.activeTierLabel, name: state.activeTierName, geo: state.activeTierGeo, startDate: state.activeTierStartDate, endDate: state.activeTierEndDate, lineItems: state.lineItems, availsData: state.availsData, periodMergeGroups: state.activeTierPeriodMergeGroups });

  // ...then make the NEW tier active, starting as a clone of it — "Add
  // Option" copies the current mix so the planner adjusts from there,
  // rather than starting from a blank curation table.
  const idMap = {};
  const clonedItems = state.lineItems.map(li => {
    const newId = newLineItemId();
    idMap[li.id] = newId;
    // Own copy of the allocations: a shared object would make Step 06 edits leak between options.
    return { ...li, id: newId, monthly_allocations: li.monthly_allocations ? { ...li.monthly_allocations } : li.monthly_allocations };
  });
  const clonedAvails = {};
  Object.keys(state.availsData).forEach(oldId => {
    if (idMap[oldId]) clonedAvails[idMap[oldId]] = { ...state.availsData[oldId] };
  });
  if (targetBudget) distributeBudgetProportionally(clonedItems, targetBudget);

  state.activeTierLabel = nextLabel;
  state.activeTierName = null;  // blank — planner renames via the tab strip if they want to
  // Unlike geo (a quirk, not a deliberate choice — see its own state comment),
  // a brand-new option starts with NO date override: silently inheriting the
  // source option's exact flight window is more likely wrong than right for
  // a genuinely new budget option, so this resets rather than carries over.
  state.activeTierStartDate = null;
  state.activeTierEndDate = null;
  state.lineItems = clonedItems;
  state.availsData = clonedAvails;
  // Same "don't silently inherit" reasoning as the date reset above — a
  // merge decision made for the source option's own calendar/minimums
  // isn't necessarily right for a brand-new budget option.
  state.activeTierPeriodMergeGroups = [];
  state.rateOverrideOpen.clear();
  state.objectiveOtherOpen.clear();

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
  renderTierTabStrip("tier-tabs", { removable: true, draggable: true });
  renderTierTabStrip("tier-tabs-avails", { removable: false });
  renderTierTabStrip("tier-tabs-monthly", { removable: false });

  const totalTiers = 1 + state.tiers.length;
  const multiTier = totalTiers > 1;

  const addBtn = document.getElementById("add-tier-btn");
  if (addBtn) addBtn.style.display = totalTiers >= TIER_LABELS.length ? "none" : "";

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
  if (activeLabelEl) activeLabelEl.textContent = _tierDisplayName(state.activeTierLabel);

  // Same reasoning as Avails above — Monthly Breakdown is per-tier too
  // (each line item's own monthly_allocations, carried across a tier
  // swap untouched since switchTier() just swaps which array is active),
  // but until this tab strip existed there was no way to reach any
  // option but whichever was active when the planner happened to land on
  // Step 06 — which is exactly what made it look "only tied to option 1."
  const monthlyWrap = document.getElementById("tier-switcher-monthly-wrap");
  if (monthlyWrap) monthlyWrap.classList.toggle("hidden", !multiTier);
  const monthlyHint = document.getElementById("tier-switcher-hint-monthly");
  if (monthlyHint) monthlyHint.classList.toggle("hidden", !multiTier);
  const activeLabelMonthlyEl = document.getElementById("tier-switcher-active-label-monthly");
  if (activeLabelMonthlyEl) activeLabelMonthlyEl.textContent = _tierDisplayName(state.activeTierLabel);

  // "Copy avails from" dropdown — every OTHER tier, so copying is one click.
  const copySource = document.getElementById("copy-avails-source");
  if (copySource) {
    const otherLabels = state.tiers.map(t => t.label).sort();
    copySource.innerHTML = otherLabels.map(l => `<option value="${l}">${escapeHtml(_tierDisplayName(l))}</option>`).join("");
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
    alert(`No avails to copy — ${_tierDisplayName(sourceLabel)} has nothing entered for products in ${_tierDisplayName(state.activeTierLabel)} (or they're already filled in here).`);
  }
}

function renderTierTabStrip(containerId, opts) {
  const tabsEl = document.getElementById(containerId);
  if (!tabsEl) return;
  const totalTiers = 1 + state.tiers.length;
  const allLabels = [state.activeTierLabel, ...state.tiers.map(t => t.label)].sort();

  tabsEl.innerHTML = allLabels.map(label => `
    <button type="button" class="tier-tab ${label === state.activeTierLabel ? "active" : ""}" data-tier="${label}">
      ${opts.draggable && totalTiers > 1 ? `<span class="tier-tab-drag" title="Drag to reorder">⠿</span>` : ""}
      ${escapeHtml(_tierDisplayName(label))}
      <span class="tier-tab-rename" data-tier-rename="${label}" title="Rename this option">✎</span>
      ${opts.removable && totalTiers > 1 ? `<span class="tier-tab-remove" data-tier-remove="${label}" title="Remove this option">×</span>` : ""}
    </button>
  `).join("");

  tabsEl.querySelectorAll(".tier-tab").forEach(btn => {
    btn.addEventListener("click", (e) => {
      if (e.target.closest("[data-tier-remove]") || e.target.closest("[data-tier-rename]") || e.target.closest(".tier-tab-drag")) return;
      switchTier(btn.dataset.tier);
    });
  });
  tabsEl.querySelectorAll("[data-tier-rename]").forEach(el => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      renameTier(el.dataset.tierRename);
    });
  });
  if (opts.removable) {
    tabsEl.querySelectorAll("[data-tier-remove]").forEach(el => {
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        if (!confirm(`Remove ${_tierDisplayName(el.dataset.tierRemove)}? This can't be undone.`)) return;
        removeTier(el.dataset.tierRemove);
      });
    });
  }
  if (opts.draggable) wireTierTabDrag(tabsEl);
}

// --------------------------------------------------------------------------
// Drag-to-reorder OPTION TABS (Step 04 only — tier-tabs-avails/-monthly
// stay click-only). Same plain-mouse-events pattern as wireLineItemDrag
// (native HTML5 DnD was already rejected for that feature — see its own
// comment). The tab strip is `flex-wrap: wrap`, not a single row (up to 10
// options fit on 2-3 lines on a normal-width screen), so the hit-test
// below checks BOTH x and y against each tab's own rect — an x-only check
// (as if tabs were always one row) would let a wrapped tab on row 2 get
// misidentified as whichever row-1 tab happens to share its column.
// --------------------------------------------------------------------------

function wireTierTabDrag(tabsEl) {
  const allTabs = () => [...tabsEl.querySelectorAll(".tier-tab")];

  const clearDropIndicators = () => {
    allTabs().forEach(t => t.classList.remove("drag-over-left", "drag-over-right"));
  };

  tabsEl.querySelectorAll(".tier-tab-drag").forEach(handle => {
    handle.addEventListener("mousedown", e => {
      if (e.button !== 0) return;  // left-click only
      e.preventDefault();  // don't let the mouse-down start a text selection
      const startTab = handle.closest(".tier-tab");
      if (!startTab) return;
      const fromLabel = startTab.dataset.tier;
      startTab.classList.add("dragging");
      document.body.classList.add("reordering-line-item");  // same grabbing-cursor class the line-item drag already defines

      let dropTarget = null;
      let insertAfter = false;

      const onMouseMove = moveEvent => {
        const overTab = allTabs().find(t => {
          const rect = t.getBoundingClientRect();
          return moveEvent.clientX >= rect.left && moveEvent.clientX <= rect.right &&
                 moveEvent.clientY >= rect.top && moveEvent.clientY <= rect.bottom;
        });
        clearDropIndicators();
        if (overTab && overTab !== startTab) {
          const rect = overTab.getBoundingClientRect();
          insertAfter = moveEvent.clientX > rect.left + rect.width / 2;
          overTab.classList.add(insertAfter ? "drag-over-right" : "drag-over-left");
          dropTarget = overTab;
        } else {
          dropTarget = null;
        }
      };

      const onMouseUp = () => {
        document.removeEventListener("mousemove", onMouseMove);
        document.removeEventListener("mouseup", onMouseUp);
        document.body.classList.remove("reordering-line-item");
        startTab.classList.remove("dragging");
        clearDropIndicators();
        if (dropTarget) moveTier(fromLabel, dropTarget.dataset.tier, insertAfter);
      };

      document.addEventListener("mousemove", onMouseMove);
      document.addEventListener("mouseup", onMouseUp);
    });
  });
}

// Reorders the option tabs. Display order is 100% label-derived, never
// array position (renderTierTabStrip/allTiersForSubmit/reopen-restore all
// just alphabetize by label), so reordering state.tiers alone would have
// zero visible effect — a drag-reorder here means "assign new A/B/C/D
// letters matching the drop position," the same free-letter assignment
// addTier() already does. A tier's own custom .name (set via the ✎ rename
// icon) is untouched either way — only which LETTER it's called changes.
function moveTier(fromLabel, toLabel, insertAfter) {
  if (fromLabel === toLabel) return;
  const originalActiveLabel = state.activeTierLabel;
  const current = [originalActiveLabel, ...state.tiers.map(t => t.label)].sort();
  const fromIdx = current.indexOf(fromLabel);
  const dropOnIdx = current.indexOf(toLabel);
  if (fromIdx === -1 || dropOnIdx === -1) return;

  // Same off-by-one shift math as moveLineItem, applied over labels
  // instead of array indices.
  let target = insertAfter ? dropOnIdx + 1 : dropOnIdx;
  if (fromIdx < target) target -= 1;
  if (target === fromIdx) return;  // dropped back where it started

  const order = current.slice();
  const [movedLabel] = order.splice(fromIdx, 1);
  order.splice(target, 0, movedLabel);

  // `order` is now the OLD labels in their NEW desired position —
  // relabel each slot sequentially to match. originalActiveLabel is
  // captured above (not re-read from state.activeTierLabel mid-loop)
  // since this loop overwrites that same field the moment it reaches the
  // active tier's own slot — comparing against a live-mutating value
  // here would misidentify a later slot once labels start colliding.
  const snapshotByLabel = {};
  state.tiers.forEach(t => { snapshotByLabel[t.label] = t; });
  const newTiers = [];
  order.forEach((oldLabel, i) => {
    const newLabel = TIER_LABELS[i];
    if (oldLabel === originalActiveLabel) {
      state.activeTierLabel = newLabel;
    } else {
      const snap = snapshotByLabel[oldLabel];
      snap.label = newLabel;
      newTiers.push(snap);
    }
  });
  state.tiers = newTiers;

  renderAllTierTabStrips();
}

// --------------------------------------------------------------------------
// Step 4: Curation
// --------------------------------------------------------------------------

function renderLineItems() {
  renderAllTierTabStrips();
  const tbody = document.getElementById("line-items-body");
  tbody.innerHTML = "";
  // Added Value % basis: the tier's real (non-AV) budget — same "% of what's
  // actually being billed" rule the export uses (see proposal_generator.py).
  const tierRealTotal = state.lineItems.reduce((s, li) => s + (li.is_added_value ? 0 : (li.monthly_budget || 0)), 0);
  const shares = _curateSharePercents();
  const paidCount = _paidLineIndices().length;
  state.lineItems.forEach((li, idx) => {
    const p = state.productIndex[li.product_name] || {};
    const tr = document.createElement("tr");
    // Catalog's minimum_spend is a MONTHLY figure; li.monthly_budget is
    // "$ per one state.timeUnit period" — scale before comparing, same
    // rule Step 06 uses (see _mbEffectiveMinimumForPeriod).
    const minSpend = (p.minimum_spend || 0) * _timeUnitMinimumScale();
    // Added Value: a $0 (or below-minimum) budget is deliberate here, not
    // an oversight — don't flag it.
    const belowMin = !li.is_added_value && li.monthly_budget < minSpend;
    const rateOpen = state.rateOverrideOpen.has(idx) || li.rate_override != null || li.estimated_cpm_override != null;
    // The effective buying model — li.buying_model_override (see the
    // Model cell below) takes precedence over the catalog default,
    // exactly the same override-precedence pattern rate_override/
    // estimated_cpm_override already use (_effectiveProduct). Everything
    // downstream in this row (which RATE input shows, the impressions
    // reference) reads THIS, never p.pricing_model directly, so overriding
    // the model actually changes how the row behaves, not just its label.
    const effP = _effectiveProduct(li, p);
    // Fixed-model products (Meta, YouTube, TikTok, LinkedIn, Spotify,
    // Branded Content, ...) have no real per-unit rate to override — their
    // RATE column instead edits the ESTIMATED CPM that drives the "Est. $"
    // impressions calc in Step 05/the export. Kept visually distinct
    // ("Est. $X CPM", not just "$X CPM") so it's never mistaken for a real
    // billing rate the way a bare number would be.
    const isFixedModel = (effP.pricing_model || "").toUpperCase() === "FIXED";
    const effectiveCpm = li.estimated_cpm_override != null ? li.estimated_cpm_override : p.estimated_cpm_for_imps;
    const impsRefText = li.is_added_value ? "" : _mbUnitsRefText(li, p, li.monthly_budget);

    // Row identity for the mouse-based drag-reorder wiring below — no
    // `draggable` attribute needed, this isn't native HTML5 drag-and-drop.
    tr.dataset.idx = idx;
    tr.className = "line-item-row";

    tr.innerHTML = `
      <td class="col-drag"><span class="drag-handle" title="Drag to reorder">⠿</span></td>
      <td class="idx">${idx + 1}</td>
      <td class="product">
        ${escapeHtml(li.product_name)}
        <span class="family-tag">${escapeHtml(p.family || "")}</span>
      </td>
      <td class="model">
        <select class="model-override-select ${li.buying_model_override != null ? "overridden" : ""}"
                data-idx="${idx}" data-buying-model-select
                title="${li.buying_model_override != null ? "Overridden from catalog default (" + (p.pricing_model || "—") + ") — the avails/impressions math and export below now use this instead" : "Buying model — override if this line bills differently than the catalog default"}">
          <option value="" ${li.buying_model_override == null ? "selected" : ""}>${escapeHtml(p.pricing_model || "—")} (catalog)</option>
          <option value="CPM" ${li.buying_model_override === "CPM" ? "selected" : ""}>CPM</option>
          <option value="CPP" ${li.buying_model_override === "CPP" ? "selected" : ""}>CPP</option>
          <option value="Fixed" ${li.buying_model_override === "Fixed" ? "selected" : ""}>Fixed</option>
        </select>
      </td>
      <td class="rate-cell">
        ${rateOpen ? (isFixedModel ? `
          <input type="number" step="1" min="0" class="rate-override-input est-cpm-input"
                 placeholder="${p.estimated_cpm_for_imps != null ? "Catalog: $" + p.estimated_cpm_for_imps : "No catalog estimate"}"
                 value="${li.estimated_cpm_override != null ? li.estimated_cpm_override : ""}"
                 data-idx="${idx}" data-key="estimated_cpm_override" data-rate-input />
          <button class="btn-rate-reset" data-idx="${idx}" data-field="estimated_cpm_override" title="Revert to catalog estimate">×</button>
          <span class="rate-override-badge ${li.estimated_cpm_override != null ? "active" : ""}" data-idx="${idx}" title="${li.estimated_cpm_override != null ? "Overridden — saved" : ""}">✓</span>
        ` : `
          <input type="number" step="1" min="0" class="rate-override-input"
                 placeholder="${formatRate(effP)}"
                 value="${li.rate_override != null ? li.rate_override : ""}"
                 data-idx="${idx}" data-key="rate_override" data-rate-input />
          <button class="btn-rate-reset" data-idx="${idx}" data-field="rate_override" title="Revert to catalog rate">×</button>
          <span class="rate-override-badge ${li.rate_override != null ? "active" : ""}" data-idx="${idx}" title="${li.rate_override != null ? "Overridden — saved" : ""}">✓</span>
        `) : (isFixedModel ? `
          <span class="rate-display est-cpm-display">${effectiveCpm != null ? `Est. $${effectiveCpm} CPM` : "No estimate"}</span>
          <button class="btn-rate-override" data-idx="${idx}" title="Set an estimated CPM for the impressions calc (not a real billing rate)">✎</button>
        ` : `
          <span class="rate-display">${formatRate(effP)}</span>
          <button class="btn-rate-override" data-idx="${idx}" title="Override this rate">✎</button>
        `)}
      </td>
      <td class="min">${money(minSpend)}</td>
      <td class="col-budget">
        <div class="budget-box">
          <div class="budget-net-row">
            <span class="budget-field-label">Net</span>
            <input type="text" inputmode="decimal" value="${formatBudgetInputValue(li.monthly_budget)}"
                   class="budget-net-input ${belowMin ? "below-min" : ""}"
                   ${li.is_added_value ? "disabled" : ""}
                   data-idx="${idx}" data-key="monthly_budget" />
          </div>
          ${(state.parsed.agency_fee > 0 && !li.is_added_value) ? `
            <div class="budget-divider"></div>
            <div class="budget-gross-row">
              <span class="budget-field-label budget-gross-label">Gross</span>
              <input type="text" inputmode="decimal"
                     value="${formatBudgetInputValue(_netToGross(li.monthly_budget || 0, state.parsed.agency_fee))}"
                     class="budget-gross-input"
                     data-idx="${idx}" data-gross-budget-input
                     title="Gross budget for this line — editing recalculates the Net figure above" />
            </div>
          ` : ""}
          ${impsRefText ? `<div class="budget-imps-ref mono" data-imps-ref="${idx}">≈ ${impsRefText}</div>` : `<div class="budget-imps-ref mono hidden" data-imps-ref="${idx}"></div>`}
        </div>
        <label class="av-switch" title="Added Value — locks this line's budget to $0">
          <input type="checkbox" data-idx="${idx}" ${li.is_added_value ? "checked" : ""} data-added-value-toggle />
          <span class="av-switch-track"><span class="av-switch-thumb"></span></span>
          <span class="av-switch-label">Added Value</span>
        </label>
        ${li.is_added_value ? `
          <div class="av-pct-row">
            <input type="number" step="1" min="0" max="100" placeholder="%"
                   value="${li.added_value_pct != null ? li.added_value_pct : ""}"
                   class="av-pct-input" data-idx="${idx}" data-av-pct
                   title="% of the tier's real budget to show as this line's estimated AV value" />
            <span class="av-value-preview" data-av-preview="${idx}">${_avValuePreviewText(li.added_value_pct, tierRealTotal)}</span>
          </div>
        ` : ""}
      </td>
      <td class="col-share">${_shareCellHtml(li, idx, shares[idx], paidCount)}</td>
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
      <td class="col-objective">
        <select data-idx="${idx}" data-objective-select>
          <option value="">— Select —</option>
          ${OBJECTIVE_OPTIONS.map(opt => `<option value="${escapeHtml(opt)}" ${li.objective_override === opt ? "selected" : ""}>${escapeHtml(opt)}</option>`).join("")}
          <option value="__other__" ${_objectiveIsOther(li, idx) ? "selected" : ""}>Other…</option>
        </select>
        ${_objectiveIsOther(li, idx) ? `
          <input type="text" class="objective-other-input" placeholder="Custom objective"
                 value="${escapeHtml(li.objective_override || "")}" data-idx="${idx}" data-key="objective_override" />
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
  tbody.querySelectorAll("input:not([data-secondary-toggle]):not([data-added-value-toggle]):not([data-av-pct]):not([data-gross-budget-input]):not([data-share-input]), textarea").forEach(inp => {
    inp.addEventListener("input", onLineItemEdit);
  });
  // Step 06's split follows a typed budget/months once it's committed (blur/Enter), not per
  // keystroke, so passing through "5" on the way to "5000" can't compound cent rounding.
  tbody.querySelectorAll('input[data-key="monthly_budget"], input[data-key="months"], [data-gross-budget-input]').forEach(inp => {
    inp.addEventListener("change", () => _mbRescaleLine(state.lineItems[parseInt(inp.dataset.idx)]));
  });
  tbody.querySelectorAll("[data-secondary-toggle]").forEach(cb => {
    cb.addEventListener("change", () => onToggleSecondaryTarget(parseInt(cb.dataset.idx)));
  });
  // Objective dropdown: a dedicated handler (not the generic input/textarea
  // wiring below, which doesn't even match <select>) so picking "Other…"
  // can open the free-text input without ever storing the literal
  // "__other__" sentinel as a real objective value.
  tbody.querySelectorAll("[data-objective-select]").forEach(sel => {
    sel.addEventListener("change", () => {
      const idx = parseInt(sel.dataset.idx);
      const li = state.lineItems[idx];
      if (sel.value === "__other__") {
        state.objectiveOtherOpen.add(idx);
      } else {
        state.objectiveOtherOpen.delete(idx);
        li.objective_override = sel.value || null;
      }
      renderLineItems();
    });
  });
  // Buying model override: a dedicated handler (not the generic input
  // wiring above, which doesn't match <select>) since changing it can flip
  // whether the RATE cell shows a real rate or an estimated-CPM input
  // (isFixedModel above), and the impressions reference below it, so a
  // full re-render is needed either way — no cheap DOM patch here.
  tbody.querySelectorAll("[data-buying-model-select]").forEach(sel => {
    sel.addEventListener("change", () => {
      const idx = parseInt(sel.dataset.idx);
      const li = state.lineItems[idx];
      li.buying_model_override = sel.value || null;
      renderLineItems();
      updateTotals();
    });
  });
  // Rate/CPM override: onLineItemEdit already saves it live on every
  // keystroke (via the generic wiring above) — the ✓ badge is a PERSISTENT
  // state indicator (rendered from li.rate_override/estimated_cpm_override
  // above, correct immediately on load — e.g. reopening a proposal with an
  // existing override shows it right away, not just after an edit), not a
  // fade-out flash — it stays visible the whole time an override is
  // active, disappearing only when reverted. A brief pulse on blur is the
  // "you just changed something" moment layered on top of that.
  tbody.querySelectorAll("[data-rate-input]").forEach(inp => {
    inp.addEventListener("blur", () => {
      const idx = parseInt(inp.dataset.idx);
      const badge = tbody.querySelector(`.rate-override-badge[data-idx="${idx}"]`);
      if (!badge) return;
      const li = state.lineItems[idx];
      const hasOverride = li && (li.rate_override != null || li.estimated_cpm_override != null);
      badge.classList.toggle("active", hasOverride);
      badge.title = hasOverride ? "Overridden — saved" : "";
      badge.classList.remove("pulse");
      void badge.offsetWidth;  // restart the CSS animation even if it was already showing
      badge.classList.add("pulse");
    });
  });
  // AV %: a lightweight dedicated handler (not the generic one, and not a
  // full renderLineItems() re-render) so the preview updates live as the
  // planner types without losing focus/cursor position mid-edit — same
  // reasoning the generic handler already uses for every other text field.
  tbody.querySelectorAll("[data-av-pct]").forEach(inp => {
    inp.addEventListener("input", () => {
      const idx = parseInt(inp.dataset.idx);
      const li = state.lineItems[idx];
      li.added_value_pct = inp.value === "" ? null : parseFloat(inp.value);
      _refreshAvValuePreviews();
    });
  });
  // Gross budget: a dedicated handler (excluded from the generic wiring
  // above the same way rate/CPM inputs are, via data-gross-budget-input)
  // since typing a GROSS number must convert back to NET before it's
  // stored — li.monthly_budget stays the canonical NET value everywhere
  // else in the app and the export, only the displayed input differs.
  tbody.querySelectorAll("[data-gross-budget-input]").forEach(inp => {
    inp.addEventListener("input", () => {
      const idx = parseInt(inp.dataset.idx);
      const li = state.lineItems[idx];
      const fee = state.parsed.agency_fee || 0;
      const gross = parseFormattedInput(inp.value) ?? 0;
      _mbStampBaseline(li);
      li.monthly_budget = _grossToNet(gross, fee);
      const netInput = inp.closest("td").querySelector('input[data-key="monthly_budget"]');
      if (netInput) netInput.value = formatBudgetInputValue(li.monthly_budget);
      updateTotals();
      _curateRefreshImpsRef(idx);
      _refreshAvValuePreviews();
      _curateRefreshShares();
    });
  });
  // % of total: applied on change (blur/Enter) against the budgets as they were on focus,
  // so re-editing never compounds rounding drift.
  tbody.querySelectorAll("[data-share-input]").forEach(inp => {
    inp.addEventListener("focus", () => {
      state._shareSnapshot = state.lineItems.map(li => li.monthly_budget || 0);
      inp.select();
    });
    inp.addEventListener("keydown", e => { if (e.key === "Enter") inp.blur(); });
    inp.addEventListener("change", () => {
      const pct = parseFloat(String(inp.value).replace(/[^0-9.\-]/g, ""));
      if (Number.isFinite(pct)) _applySharePct(parseInt(inp.dataset.idx), pct, state._shareSnapshot);
      state._shareSnapshot = null;
      _renderLineItemsKeepingFocus();
    });
    inp.addEventListener("blur", () => { state._shareSnapshot = null; });
  });
  // Comma-formatted display layer for the Net/Gross budget inputs above —
  // each one's own "input" listener already parses/stores/syncs on every
  // keystroke; this just strips commas on focus (raw digits are easier to
  // edit) and reformats with commas on blur, same pattern the avails
  // max_imps/max_spend fields already use.
  tbody.querySelectorAll('input[data-key="monthly_budget"], [data-gross-budget-input]').forEach(inp => {
    inp.addEventListener("focus", () => {
      const raw = parseFormattedInput(inp.value);
      inp.value = raw === null ? "" : String(raw);
    });
    inp.addEventListener("blur", () => {
      const idx = parseInt(inp.dataset.idx);
      const li = state.lineItems[idx];
      if (!li) return;
      const isGross = inp.hasAttribute("data-gross-budget-input");
      const value = isGross ? _netToGross(li.monthly_budget || 0, state.parsed.agency_fee || 0) : li.monthly_budget;
      inp.value = formatBudgetInputValue(value);
    });
  });
  tbody.querySelectorAll("[data-added-value-toggle]").forEach(cb => {
    cb.addEventListener("change", () => {
      const idx = parseInt(cb.dataset.idx);
      const li = state.lineItems[idx];
      li.is_added_value = cb.checked;
      if (cb.checked) li.monthly_budget = 0;  // an Added Value line is $0 by definition, not "$0 or whatever's left over"
      renderLineItems();  // refreshes the budget field's value/disabled state, the below-min highlight, and totals
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
      state.objectiveOtherOpen.clear();
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
// Drag-to-reorder line items (Step 04). Plain mouse events (mousedown on
// the grip handle -> mousemove tracks the cursor and shows a drop
// indicator -> mouseup commits the move), NOT the native HTML5 Drag and
// Drop API. The native API was tried first and looked right in the DOM,
// but a real drag gesture never actually completed one for the planner
// reporting this — native HTML5 DnD depends on the browser's own OS-level
// drag-gesture detection, which is exactly the kind of thing that's
// finicky across trackpads/browsers and (confirmed directly) doesn't
// reliably fire from a synthetic drag either, so it's inherently harder to
// even verify. Listening on plain mouse events sidesteps all of that —
// it's just "where is the cursor, what am I over," the same mechanism
// most drag-reorder libraries actually use under the hood.
// --------------------------------------------------------------------------

function wireLineItemDrag(tbody) {
  const allRows = () => [...tbody.querySelectorAll("tr.line-item-row")];

  const clearDropIndicators = () => {
    allRows().forEach(r => r.classList.remove("drag-over-top", "drag-over-bottom"));
  };

  tbody.querySelectorAll(".drag-handle").forEach(handle => {
    handle.addEventListener("mousedown", e => {
      if (e.button !== 0) return;  // left-click only
      e.preventDefault();  // don't let the mouse-down start a text selection
      const startRow = handle.closest("tr.line-item-row");
      if (!startRow) return;
      const fromIdx = parseInt(startRow.dataset.idx);
      startRow.classList.add("dragging");
      document.body.classList.add("reordering-line-item");

      let dropTarget = null;
      let insertAfter = false;

      const onMouseMove = moveEvent => {
        const overRow = allRows().find(r => {
          const rect = r.getBoundingClientRect();
          return moveEvent.clientY >= rect.top && moveEvent.clientY <= rect.bottom;
        });
        clearDropIndicators();
        if (overRow) {
          const rect = overRow.getBoundingClientRect();
          insertAfter = moveEvent.clientY > rect.top + rect.height / 2;
          overRow.classList.add(insertAfter ? "drag-over-bottom" : "drag-over-top");
          dropTarget = overRow;
        } else {
          dropTarget = null;
        }
      };

      const onMouseUp = () => {
        document.removeEventListener("mousemove", onMouseMove);
        document.removeEventListener("mouseup", onMouseUp);
        document.body.classList.remove("reordering-line-item");
        startRow.classList.remove("dragging");
        clearDropIndicators();
        if (dropTarget) {
          const dropOnIdx = parseInt(dropTarget.dataset.idx);
          if (!isNaN(dropOnIdx)) moveLineItem(fromIdx, dropOnIdx, insertAfter);
        }
      };

      document.addEventListener("mousemove", onMouseMove);
      document.addEventListener("mouseup", onMouseUp);
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
  state.objectiveOtherOpen.clear();
  renderLineItems();
}

function onLineItemEdit(e) {
  const idx = parseInt(e.target.dataset.idx);
  const key = e.target.dataset.key;
  let v = e.target.value;
  if (key === "monthly_budget") {
    // A comma-formatted text input now (see formatBudgetInputValue/
    // parseFormattedInput below) — parseFloat alone would stop at the
    // first comma ("35,000" -> 35), same reasoning as the avails max_imps/
    // max_spend fields already handle this way.
    v = parseFormattedInput(v) ?? 0;
  } else if (e.target.type === "number") {
    // rate_override / estimated_cpm_override are optional — an emptied
    // field means "no override, fall back to the catalog default", not 0.
    // Required numeric fields (months) fall back to 0 so the payload sent
    // to the backend always stays a valid number.
    const isOptionalOverride = key === "rate_override" || key === "estimated_cpm_override";
    v = v === "" ? (isOptionalOverride ? null : 0) : parseFloat(v);
  }
  if (key === "monthly_budget" || key === "months") _mbStampBaseline(state.lineItems[idx]);
  state.lineItems[idx][key] = v;
  updateTotals();
  if (key === "monthly_budget") _curateRefreshShares();
  if (key === "monthly_budget") {
    const li = state.lineItems[idx];
    const p = state.productIndex[li.product_name] || {};
    const minSpend = (p.minimum_spend || 0) * _timeUnitMinimumScale();
    e.target.classList.toggle("below-min", !li.is_added_value && (v || 0) < minSpend);
    // Keep this row's Gross budget input (if shown) in sync with the Net
    // value that was just typed — the two stay mirrored regardless of
    // which one the planner is actually editing.
    const grossInput = e.target.closest("td")?.querySelector("[data-gross-budget-input]");
    if (grossInput) {
      const fee = state.parsed.agency_fee || 0;
      grossInput.value = formatBudgetInputValue(_netToGross(v || 0, fee));
    }
    // Any AV line's estimated value is a % of every OTHER line's budget —
    // editing this one shifts that basis for all of them.
    _refreshAvValuePreviews();
  }
  if (key === "monthly_budget" || key === "rate_override" || key === "estimated_cpm_override") {
    _curateRefreshImpsRef(idx);
  }
}

// "≈ 333,462 imps" / "≈ 116 pts" under a line's Net budget — cheap DOM
// patch (not a full renderLineItems()) so typing doesn't lose focus,
// mirrors _avValuePreviewText's same reasoning. Reuses _mbUnitsRefText,
// the exact same reference-text function Step 06's Monthly Breakdown
// already shows, so the two never drift into separately-maintained copies.
function _curateRefreshImpsRef(idx) {
  const li = state.lineItems[idx];
  if (!li) return;
  const el = document.querySelector(`#line-items-body [data-imps-ref="${idx}"]`);
  if (!el) return;
  const p = state.productIndex[li.product_name] || {};
  const text = li.is_added_value ? "" : _mbUnitsRefText(li, p, li.monthly_budget);
  el.textContent = text ? `≈ ${text}` : "";
  el.classList.toggle("hidden", !text);
}

// --------------------------------------------------------------------------
// Step 04: % of total + scale-to-total. Added Value lines are always excluded
// (locked at $0, never scaled); the math lives in curate-math.js.
// --------------------------------------------------------------------------

function _paidLineIndices() {
  return state.lineItems.map((li, i) => (li.is_added_value ? -1 : i)).filter(i => i >= 0);
}

// Aligned to state.lineItems: a one-decimal share for paid lines (summing to 100.0), null otherwise.
function _curateSharePercents() {
  const paid = _paidLineIndices();
  const shares = CurateMath.sharePercents(paid.map(i => state.lineItems[i].monthly_budget || 0));
  const out = state.lineItems.map(() => null);
  paid.forEach((lineIdx, j) => { out[lineIdx] = shares[j]; });
  return out;
}

// Editable once there's a total to divide and at least one other paid line to rebalance against.
function _shareInputState(share, paidCount) {
  if (share === null || share === undefined) return { editable: false, title: "Enter budgets first — the % needs a plan total to divide." };
  if (paidCount < 2) return { editable: false, title: "The only paid line is always 100% of the plan." };
  return { editable: true, title: "Edit to rebalance: the other lines adjust proportionally so the plan stays at 100%." };
}

function _shareCellHtml(li, idx, share, paidCount) {
  if (li.is_added_value) return `<span class="share-na" title="Added Value lines aren't part of the paid total">AV</span>`;
  const { editable, title } = _shareInputState(share, paidCount);
  return `<span class="share-wrap"><input type="text" inputmode="decimal" class="share-input" data-idx="${idx}" data-share-input
    value="${share === null ? "" : share.toFixed(1)}" placeholder="—" ${editable ? "" : "disabled"} title="${escapeHtml(title)}" /><span class="share-suffix">%</span></span>`;
}

// Cheap patch while a Net/Gross value is being typed (a full re-render would steal focus).
function _curateRefreshShares() {
  const shares = _curateSharePercents();
  const paidCount = _paidLineIndices().length;
  document.querySelectorAll("#line-items-body [data-share-input]").forEach(inp => {
    if (inp === document.activeElement) return;
    const share = shares[parseInt(inp.dataset.idx)];
    const { editable, title } = _shareInputState(share, paidCount);
    inp.value = share === null || share === undefined ? "" : share.toFixed(1);
    inp.disabled = !editable;
    inp.title = title;
  });
}

// A change handler fires while focus is already moving (Tab or a click on the next cell), so an
// immediate re-render would destroy the cell being moved to. Wait for focus to land, re-render,
// then focus the same cell in the rebuilt table.
function _renderLineItemsKeepingFocus() {
  setTimeout(() => {
    const el = document.activeElement;
    const inTable = el && el.closest && el.closest("#line-items-body") && el.dataset && el.dataset.idx !== undefined;
    const marker = !inTable ? null : el.dataset.key !== undefined
      ? `[data-key="${el.dataset.key}"]`
      : Array.from(el.attributes).map(a => a.name).filter(n => n.startsWith("data-") && n !== "data-idx").map(n => `[${n}]`)[0];
    renderLineItems();
    const next = marker && document.querySelector(`#line-items-body [data-idx="${el.dataset.idx}"]${marker}`);
    if (next && !next.disabled) {
      next.focus();
      if (next.tagName === "INPUT" && next.type === "text") next.select();
    }
  }, 0);
}

// Step 06's rescale needs the total an allocation was built against; a reopened proposal never restores it.
function _mbStampBaseline(li) {
  if (li && li.monthly_allocations && Object.keys(li.monthly_allocations).length && li._mbBaseline === undefined) {
    li._mbBaseline = (li.monthly_budget || 0) * (li.months || 1);
  }
}

// Writes new budgets and immediately rescales any Monthly Breakdown so each period keeps its %,
// even if the planner never reopens Step 06 before generating.
function _commitBudgets(lineIndices, budgets) {
  lineIndices.forEach((lineIdx, j) => {
    const li = state.lineItems[lineIdx];
    _mbStampBaseline(li);
    li.monthly_budget = budgets[j];
    _mbRescaleLine(li);
  });
}

// Same rescale Step 06 applies on render, applied now: a reopened proposal can jump from here
// straight to Generate without Step 06 ever re-rendering.
function _mbRescaleLine(li) {
  if (li && li.monthly_allocations && Object.keys(li.monthly_allocations).length) {
    li.monthly_allocations = _mbRescaleForBudgetChange(li);
  }
}

function _applySharePct(lineIdx, pct, snapshot) {
  const paid = _paidLineIndices();
  const k = paid.indexOf(lineIdx);
  if (k < 0 || paid.length < 2) return;
  const budgets = paid.map(i => (snapshot && snapshot[i] !== undefined ? snapshot[i] : state.lineItems[i].monthly_budget) || 0);
  _commitBudgets(paid, CurateMath.setSharePct(budgets, k, pct));
}

function onScaleToTotal() {
  const input = document.getElementById("total-budget-target");
  const target = parseFormattedInput(input.value);
  const unit = _mbUnitAdjective().toLowerCase();
  if (!target || target <= 0) {
    alert(`Enter a total ${unit} budget first.`);
    input.focus();
    return;
  }
  const paid = _paidLineIndices();
  if (!paid.length) {
    alert("Add at least one paid (non-Added-Value) line first.");
    return;
  }
  const budgets = paid.map(i => state.lineItems[i].monthly_budget || 0);
  if (!(budgets.reduce((a, b) => a + b, 0) > 0)
      && !confirm(`None of the lines has a budget yet, so there's no mix to scale from. Split ${money(target)} evenly across the ${paid.length} paid line(s)?`)) {
    return;
  }
  _commitBudgets(paid, CurateMath.scaleToTotal(budgets, target));
  renderLineItems();
}

// Added Value % preview — "≈ $150 (5% of $3,000)" — mirrors the export's
// own calc (tier's real, non-AV budget total × pct) so what the planner
// sees here matches what lands in the Excel note. Cheap DOM patch instead
// of a full re-render so typing in either field doesn't lose focus.
function _avValuePreviewText(pct, tierRealTotal) {
  if (!pct) return "";
  const value = tierRealTotal * (pct / 100);
  return `≈ ${money(value)} (${pct}% of ${money(tierRealTotal)})`;
}

function _refreshAvValuePreviews() {
  const tierRealTotal = state.lineItems.reduce((s, li) => s + (li.is_added_value ? 0 : (li.monthly_budget || 0)), 0);
  document.querySelectorAll("[data-av-preview]").forEach(span => {
    const idx = parseInt(span.dataset.avPreview);
    const li = state.lineItems[idx];
    if (li) span.textContent = _avValuePreviewText(li.added_value_pct, tierRealTotal);
  });
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

// Every static "Month"/"Monthly"/"Months" label this toggle governs, in
// one place — called on toggle change AND once at page init/reopen so a
// non-default state.timeUnit (a reopened proposal) shows correctly from
// the start. Deliberately does NOT touch any $ or count NUMBER (see
// onTimeUnitChange's own comment on why those are left for the planner
// to manually review rather than auto-converted).
function _applyTimeUnitLabels() {
  const noun = _mbUnitNoun();
  const nounPlural = _mbUnitNounPlural();
  const adjective = _mbUnitAdjective();

  const sliderOptions = Array.from(document.querySelectorAll("#time-unit-toggle .time-unit-slider-option"));
  const activeIdx = sliderOptions.findIndex(btn => btn.dataset.unit === state.timeUnit);
  sliderOptions.forEach((btn, i) => btn.classList.toggle("active", i === activeIdx));
  const thumb = document.getElementById("time-unit-slider-thumb");
  // Percentages here are relative to the THUMB's own width (one segment),
  // not the track's — translateX(100%) moves it exactly one thumb-width
  // right, landing it on the middle segment regardless of the track's
  // actual pixel width. Falls back to the "month" position (index 1) if
  // state.timeUnit somehow doesn't match any option.
  if (thumb) thumb.style.transform = `translateX(${(activeIdx === -1 ? 1 : activeIdx) * 100}%)`;

  const setText = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };
  setText("budget-target-label", `Total ${adjective.toLowerCase()} budget`);
  setText("monthly-total-label", `${adjective} total`);
  setText("monthly-total-gross-label", `${adjective} total (Gross)`);
  setText("col-budget-header", `${adjective} $`);
  setText("col-months-header", nounPlural);
  setText("mb-step-title", `${adjective} breakdown`);
  setText("mb-step-lede", `Split each line item's budget across the ${nounPlural.toLowerCase()} your flight actually touches — useful for phased campaigns or seasonal weighting. Skip this if a flat ${adjective.toLowerCase()} figure is all you need; nothing else changes if you do.`);
  setText("mb-step-name", `${adjective} Breakdown`);
  setText("mb-no-dates-text", `${adjective} Breakdown needs a campaign flight to divide into ${nounPlural.toLowerCase()}. Set Start/End dates back in Step 02 (or a per-option override in Step 04), then come back here.`);
  setText("mb-mode-even-btn", `Even across ${nounPlural.toLowerCase()}`);
  setText("nav-step-6-label", ` ${adjective}`);
  const navStep6 = document.getElementById("nav-step-6");
  if (navStep6) navStep6.title = adjective;
}

// Step 04's Week/Month/Quarter toggle. Deliberately does NOT auto-convert
// any existing $ or count number when the unit changes (e.g. rescale a
// curated "$2,000/month" line into "$461/week") — that's real billing
// data, and a silent automatic conversion is exactly the kind of thing
// that could ship a wrong number into a client-facing proposal if this
// logic ever had a subtle bug. Instead the raw numbers stay exactly as
// curated and the planner reviews/adjusts them under the new labels,
// same as any other curation field. What DOES get cleared: every option's
// Step 06 Monthly Breakdown allocations and merge groups, since their
// period KEYS (e.g. "2026-09" for month, "W1-2026-09-01" for week) are
// tied to the OLD granularity and become meaningless under the new one.
function onTimeUnitChange(newUnit) {
  if (newUnit === state.timeUnit || !(newUnit in _MB_UNIT_ADJECTIVE)) return;

  const allTiers = allTiersForSubmit();
  const hasAllocations = allTiers.some(t => (t.line_items || []).some(li => li.monthly_allocations && Object.keys(li.monthly_allocations).length));
  const hasMerges = allTiers.some(t => (t.period_merge_groups || []).length);
  if (hasAllocations || hasMerges) {
    const ok = confirm(
      `Switching to ${_MB_UNIT_ADJECTIVE[newUnit]} will clear every budget option's Step 06 breakdown and combined periods — ` +
      `the old ${_mbUnitNoun().toLowerCase()}-based numbers won't carry over. Curated budgets and product mix are untouched either way. Continue?`
    );
    if (!ok) return;
  }

  state.timeUnit = newUnit;

  const clearLine = (li) => { li.monthly_allocations = null; delete li._mbBaseline; };
  state.lineItems.forEach(clearLine);
  state.tiers.forEach(t => { (t.lineItems || []).forEach(clearLine); t.periodMergeGroups = []; });
  state.activeTierPeriodMergeGroups = [];

  _applyTimeUnitLabels();
  renderLineItems();
  updateTotals();
  renderMonthlyBreakdown();
}

function updateTotals() {
  const monthly = state.lineItems.reduce((s, li) => s + (li.monthly_budget || 0), 0);
  const flight = state.lineItems.reduce((s, li) => s + (li.monthly_budget || 0) * (li.months || 1), 0);
  document.getElementById("monthly-total").textContent = money(monthly);
  document.getElementById("flight-total").textContent = money(flight);

  // Gross totals — shown alongside Net whenever an agency fee is set (per
  // the same request.agency_fee > 0 gate the Step 07 summary already uses),
  // computed with the exact formula the export uses so these never drift
  // apart. No agency fee: leave the display exactly as it's always been.
  const fee = state.parsed.agency_fee || 0;
  const grossWrap = document.getElementById("totals-gross-wrap");
  if (grossWrap) {
    grossWrap.classList.toggle("hidden", !(fee > 0));
    if (fee > 0) {
      document.getElementById("monthly-total-gross").textContent = money(_netToGross(monthly, fee));
      document.getElementById("flight-total-gross").textContent = money(_netToGross(flight, fee));
    }
  }
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
    monthly_budget: (p.minimum_spend || 0) * _timeUnitMinimumScale(),
    months: state.parsed?.total_months || 3,
    rate_override: null,
    notes_override: null,
    target_override: null,
    target_secondary: null,
    estimated_cpm_override: null,
    buying_model_override: null,
    is_added_value: false,
    added_value_pct: null,
    objective_override: _mapCampaignGoalToObjective(state.parsed?.campaign_goal),
    monthly_allocations: null,
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
  const copy = {
    ...original,
    id: newLineItemId(),
    monthly_allocations: original.monthly_allocations ? { ...original.monthly_allocations } : original.monthly_allocations,
  };
  // Carry over any avails already entered for the original line, so
  // duplicating a filled-in row for a targeting variant doesn't lose them.
  if (state.availsData[original.id]) {
    state.availsData[copy.id] = { ...state.availsData[original.id] };
  }
  state.lineItems.splice(idx + 1, 0, copy);
  state.rateOverrideOpen.clear();  // indices shift — avoid pointing at the wrong row
  state.objectiveOtherOpen.clear();
  renderLineItems();
}

async function onRecommend() {
  const budget = parseFormattedInput(document.getElementById("total-budget-target").value);
  if (!budget || budget <= 0) {
    alert(`Enter a target ${_mbUnitAdjective().toLowerCase()} budget first.`);
    return;
  }
  const btn = document.getElementById("recommend-btn");
  btn.disabled = true;
  const originalLabel = btn.innerHTML;
  btn.innerHTML = '<span class="btn-inline-spinner"></span>Suggesting…';
  try {
    const res = await fetch("/api/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        request: state.parsed,
        monthly_budget: budget,
        strategy_brief: _briefWithSelectedTactics(),
        time_unit: state.timeUnit,
      }),
    });
    if (!res.ok) {
      alert("Recommend failed: " + res.statusText);
      return;
    }
    const data = await res.json();
    // Spread FIRST, id override SECOND — the server's own LineItem.id is
    // always null (recommend_line_items() never sets one), so the other
    // order (id first, `...li` after) let li.id:null clobber the freshly
    // generated id right back to null on every single recommended line.
    // That silently broke per-row identity everywhere line items are
    // looked up by id instead of array index — most visibly Step 06
    // Monthly Breakdown's _mbFindLineItem(), where every row resolved to
    // the very first one regardless of which row was actually edited.
    // Matches the same spread order onDuplicateLineItem already uses.
    state.lineItems = data.line_items.map(li => ({ ...li, id: newLineItemId() }));
    state.availsData = {};  // previous avails were keyed to the old line items' ids
    // Stale indices from before this replacement shouldn't leave an
    // unrelated row's rate-override editor or "Other…" objective box
    // spontaneously expanded — every other function that wholesale-
    // replaces state.lineItems already clears these.
    state.rateOverrideOpen.clear();
    state.objectiveOtherOpen.clear();
    renderLineItems();
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalLabel;
  }
}

// --------------------------------------------------------------------------
// Step 5: Avails
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
// Step 05's own reminder of what this line is actually targeting — mirrors
// notion_parser.compose_target_fallback() (the same DEMO | BEHAVIORAL |
// CONTEXTUAL composition the Excel TARGET column falls back to) so the
// planner sees the SAME value here that'll actually land in the export,
// not just the raw override text with no context when it's blank.
function _effectiveTargetText(li) {
  const req = state.parsed || {};
  let primary = li.target_override;
  if (!primary) {
    const parts = [req.demo, req.behavioral, req.contextual ? `Contextual: ${req.contextual}` : ""].filter(Boolean);
    primary = parts.length ? parts.join(" | ") : "TBD";
  }
  return li.target_secondary != null && li.target_secondary !== ""
    ? `${primary} (+ Secondary: ${li.target_secondary})`
    : primary;
}

function _effectiveProduct(li, p) {
  // Builds a shallow-cloned "virtual product" with whichever per-line
  // Step 04 overrides apply baked in, so every avails/SOV calc site just
  // reads p.rate/p.estimated_cpm_for_imps normally instead of duplicating
  // override-resolution logic at each call site. Both overrides can apply
  // at once in principle (though in practice a line is either a real
  // CPM/CPP product with a rate_override, or a Fixed/estimated-CPM
  // product with an estimated_cpm_override, never both meaningfully).
  if (!li) return p;
  let eff = p;
  if (li.rate_override != null) {
    eff = { ...eff, rate: li.rate_override };
  }
  if (li.estimated_cpm_override != null) {
    eff = { ...eff, estimated_cpm_for_imps: li.estimated_cpm_override };
  }
  if (li.buying_model_override != null) {
    eff = { ...eff, pricing_model: li.buying_model_override };
  }
  return eff;
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
    // Impressions already a REAL, independently-known number (typed
    // directly, or from an ad platform's own delivery estimate) — Uniques
    // just joined it, so Frequency is now the derived/shown side, same
    // priority the max_imps branch below already uses. Overwriting the
    // real Impressions with uniques×default-frequency here was the actual
    // bug: entering both real numbers never surfaced what frequency they
    // imply, which is the whole point — a sanity check the planner can
    // eyeball ("does 5.5 look right, or does this combination look sus?").
    if (uniques != null && imps != null) {
      entry.frequency = Math.round((imps / uniques) * 10) / 10;
    } else if (uniques != null && freq != null) {
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

// entry.max_spend/max_imps are always stated against the catalog's own
// MONTHLY avails ceiling ("Max Recommended Monthly Imps/Spend" — a
// standard monthly inventory-forecasting window, deliberately NOT
// relabeled by the Week/Month/Quarter toggle, see that toggle's own
// docs). li.monthly_budget is "$ per one state.timeUnit period" — convert
// it to a monthly-EQUIVALENT rate before comparing, or SOV% would read
// wildly low in Weekly mode (a week's $ is naturally ~1/4 of a month's)
// or wildly high in Quarterly mode, with no change in real pacing.
// Deliberately simple round factors (×4 / ÷3), not the more precise
// 12/52 used for minimum-spend scaling elsewhere — explicit planner
// preference for this specific comparison.
const _SOV_MONTHLY_EQUIVALENT_SCALE = { week: 4, month: 1, quarter: 1 / 3 };
function _sovMonthlyEquivalentBudget(budget) {
  return budget * (_SOV_MONTHLY_EQUIVALENT_SCALE[state.timeUnit] ?? 1);
}

function computeSovPct(li, p, entry) {
  // Free-form has nothing to calculate this FROM (no real imps/spend
  // numbers) — a planner-declared value stands in directly, still driving
  // the same badge/conditional-formatting pipeline as a computed one.
  if (entry.sov_pct_freeform != null) return entry.sov_pct_freeform;
  if (!li || !li.monthly_budget) return null;
  const monthlyEquivalentBudget = _sovMonthlyEquivalentBudget(li.monthly_budget);
  if (entry.max_spend) {
    return monthlyEquivalentBudget / entry.max_spend * 100;
  }
  if (entry.max_imps) {
    const spendResult = calcMaxSpendFromImps(_effectiveProduct(li, p), entry.max_imps);
    if (spendResult && spendResult.value) {
      return monthlyEquivalentBudget / spendResult.value * 100;
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
// Same comma-grouped-text-input approach as the three formatters above,
// for Step 04's own Net/Gross budget inputs — cents only shown when
// non-zero (a Gross-derived value like 2045.51 keeps its real cents; a
// round Net budget like 35000 shows "35,000", not "35,000.00").
function formatBudgetInputValue(n) {
  if (n === null || n === undefined || isNaN(n)) return "";
  const hasCents = Math.round((n % 1) * 100) !== 0;
  return n.toLocaleString("en-US", { minimumFractionDigits: hasCents ? 2 : 0, maximumFractionDigits: 2 });
}
function parseFormattedInput(s) {
  if (!s) return null;
  const digits = s.replace(/[^0-9.]/g, "");
  if (!digits) return null;
  const n = parseFloat(digits);
  return isNaN(n) ? null : n;
}

// --------------------------------------------------------------------------
// Step 6: Monthly Breakdown (optional) — distributes each (non-Added-Value)
// line item's Curate-step total across the calendar months its flight
// actually touches. Mirrors app/services/monthly_allocation.py (see that
// module's own docstring for the full design rationale — dollars are the
// source of truth, percentage is always derived, no separate enabled
// flag) so the live client-side math matches what /api/generate validates
// authoritatively before letting a plan with unbalanced allocations
// actually export. Nothing here is persisted until Generate is clicked —
// this is all just editing state.lineItems[i].monthly_allocations in place.
// --------------------------------------------------------------------------

const _MB_CENT = 0.005;

function _mbParseDate(raw) {
  if (!raw) return null;
  const s = raw.trim();
  let m = s.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (m) return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
  m = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{2,4})$/);
  if (m) {
    let year = +m[3];
    if (year < 100) year += 2000;
    return new Date(Date.UTC(year, +m[1] - 1, +m[2]));
  }
  return null;
}

function _mbMonthKey(d) { return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`; }

// Coerces any of the loosely-formatted date strings this app already
// accepts (parsed from a free-text Notion paste, so not guaranteed
// ISO — could be "9/15/2026" etc.) into the strict YYYY-MM-DD a native
// <input type="date"> requires to actually show the value instead of
// silently rendering blank. Reuses _mbParseDate's own lenient parsing
// (already proven against this app's real date formats) rather than a
// second, separate parser. Returns "" (never null/undefined) for
// anything unparseable, so a date <input>'s .value assignment is always
// a valid, safe string.
function _toIsoDateString(raw) {
  const d = _mbParseDate(raw);
  if (!d) return "";
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(d.getUTCDate()).padStart(2, "0")}`;
}
function _mbMonthLabel(d) {
  return d.toLocaleDateString("en-US", { month: "long", year: "numeric", timeZone: "UTC" });
}
function _mbLastDayOfMonth(d) { return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0)); }
function _mbDaysBetweenInclusive(a, b) { return Math.round((b - a) / 86400000) + 1; }

// 'Sep 28 – Oct 4, 2026' / 'Sep 1 – 30, 2026' / 'Dec 15, 2026 – Jan 4,
// 2027' — mirrors monthly_allocation.py's _date_range_label() exactly.
// Shown alongside every period's own label in Step 06 so a planner can
// see exactly which real dates a period covers, at any granularity.
function _mbDateRangeLabel(start, end) {
  const fmt = (d) => d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  if (start.getUTCFullYear() === end.getUTCFullYear() && start.getUTCMonth() === end.getUTCMonth()) {
    return `${fmt(start)} – ${end.getUTCDate()}, ${start.getUTCFullYear()}`;
  }
  if (start.getUTCFullYear() === end.getUTCFullYear()) {
    return `${fmt(start)} – ${fmt(end)}, ${start.getUTCFullYear()}`;
  }
  return `${fmt(start)}, ${start.getUTCFullYear()} – ${fmt(end)}, ${end.getUTCFullYear()}`;
}

// Every calendar month overlapping [start, end] — mirrors
// monthly_allocation.py's months_between() exactly, including active_days
// per month for day-proration.
function _mbMonthsBetween(start, end) {
  if (end < start) { const t = start; start = end; end = t; }
  const months = [];
  let cursor = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), 1));
  while (cursor <= end) {
    const monthEnd = _mbLastDayOfMonth(cursor);
    const activeStart = cursor > start ? cursor : start;
    const activeEnd = monthEnd < end ? monthEnd : end;
    months.push({
      key: _mbMonthKey(cursor),
      label: _mbMonthLabel(cursor),
      date_range_label: _mbDateRangeLabel(activeStart, activeEnd),
      start: activeStart,
      end: activeEnd,
      days_in_month: _mbDaysBetweenInclusive(cursor, monthEnd),
      active_days: _mbDaysBetweenInclusive(activeStart, activeEnd),
      period_count: 1,
    });
    cursor = new Date(Date.UTC(cursor.getUTCMonth() === 11 ? cursor.getUTCFullYear() + 1 : cursor.getUTCFullYear(), (cursor.getUTCMonth() + 1) % 12, 1));
  }
  return months;
}

// Every 7-day period from [start, end], anchored to the campaign's OWN
// start date (not ISO Mon-Sun weeks) — mirrors monthly_allocation.py's
// weeks_between() exactly. The last week may be shorter than 7 days.
function _mbWeeksBetween(start, end) {
  if (end < start) { const t = start; start = end; end = t; }
  const weeks = [];
  let cursor = start;
  let idx = 1;
  while (cursor <= end) {
    const weekEnd = new Date(Math.min(+(new Date(cursor.getTime() + 6 * 86400000)), +end));
    weeks.push({
      key: `W${idx}-${cursor.toISOString().slice(0, 10)}`,
      label: `Week ${idx}`,
      date_range_label: _mbDateRangeLabel(cursor, weekEnd),
      start: cursor,
      end: weekEnd,
      days_in_month: _mbDaysBetweenInclusive(cursor, weekEnd),
      active_days: _mbDaysBetweenInclusive(cursor, weekEnd),
      period_count: 1,
    });
    cursor = new Date(cursor.getTime() + 7 * 86400000);
    idx += 1;
  }
  return weeks;
}

// Groups _mbMonthsBetween()'s own months into chunks of 3, from the
// campaign's own start month — mirrors monthly_allocation.py's
// quarters_between() exactly ("3-month lines" from the campaign's own
// start, not aligned to standard calendar quarters).
function _mbQuartersBetween(start, end) {
  const months = _mbMonthsBetween(start, end);
  const quarters = [];
  for (let i = 0; i < months.length; i += 3) {
    const chunk = months.slice(i, i + 3);
    const first = chunk[0], last = chunk[chunk.length - 1];
    let label;
    if (chunk.length === 1) {
      label = first.label;
    } else {
      const fmtAbbrev = (d) => d.toLocaleDateString("en-US", { month: "short", timeZone: "UTC" });
      label = first.start.getUTCFullYear() === last.end.getUTCFullYear()
        ? `${fmtAbbrev(first.start)}–${fmtAbbrev(last.end)} ${last.end.getUTCFullYear()}`
        : `${fmtAbbrev(first.start)} ${first.start.getUTCFullYear()}–${fmtAbbrev(last.end)} ${last.end.getUTCFullYear()}`;
    }
    quarters.push({
      key: chunk.map(m => m.key).join("+"),
      label,
      date_range_label: _mbDateRangeLabel(first.start, last.end),
      start: first.start,
      end: last.end,
      days_in_month: chunk.reduce((s, m) => s + m.days_in_month, 0),
      active_days: chunk.reduce((s, m) => s + m.active_days, 0),
      period_count: 1,  // one quarter = one granularity unit, regardless of how many real months compose it (see minimum-scaling)
    });
  }
  return quarters;
}

// THE one place a caller asks for "the periods this flight touches"
// without hardcoding which granularity that means — mirrors
// monthly_allocation.py's periods_between() dispatcher exactly.
function _mbPeriodsBetween(start, end, granularity) {
  if (granularity === "week") return _mbWeeksBetween(start, end);
  if (granularity === "quarter") return _mbQuartersBetween(start, end);
  return _mbMonthsBetween(start, end);
}

// Combines specific ADJACENT periods (by key) into one bucket — mirrors
// monthly_allocation.py's apply_period_merges() exactly, including the
// "ignore a group that isn't actually contiguous in this periods list"
// safety check. See that function's own docstring for the full rationale.
function _mbApplyPeriodMerges(periods, mergeGroups) {
  if (!mergeGroups || !mergeGroups.length) return periods.slice();
  const keyToIdx = {};
  periods.forEach((p, i) => { keyToIdx[p.key] = i; });
  const validGroups = [];
  mergeGroups.forEach(group => {
    if (!group || group.length < 2) return;
    const idxs = group.map(k => keyToIdx[k]).filter(i => i !== undefined);
    if (idxs.length !== group.length) return;
    idxs.sort((a, b) => a - b);
    for (let i = 1; i < idxs.length; i++) {
      if (idxs[i] !== idxs[i - 1] + 1) return;  // not contiguous — ignore rather than misrepresent the date range
    }
    validGroups.push(idxs);
  });

  const mergedIdxToGroup = {};
  validGroups.forEach(idxs => idxs.forEach(i => { mergedIdxToGroup[i] = idxs; }));

  const result = [];
  const consumed = new Set();
  periods.forEach((p, i) => {
    if (consumed.has(i)) return;
    const group = mergedIdxToGroup[i];
    if (!group) { result.push(p); return; }
    const chunk = group.map(j => periods[j]);
    group.forEach(j => consumed.add(j));
    const first = chunk[0], last = chunk[chunk.length - 1];
    result.push({
      key: chunk.map(m => m.key).join("+"),
      label: _mbCombinedPeriodLabel(first, last),
      date_range_label: (first.start && last.end) ? _mbDateRangeLabel(first.start, last.end) : `${first.date_range_label} – ${last.date_range_label}`,
      start: first.start,
      end: last.end,
      days_in_month: chunk.reduce((s, m) => s + m.days_in_month, 0),
      active_days: chunk.reduce((s, m) => s + m.active_days, 0),
      period_count: chunk.reduce((s, m) => s + (m.period_count || 1), 0),
    });
  });
  return result;
}

// 'September–October 2026' for two merged MONTHS (matches the planner's
// own phrasing); falls back to a generic '{label}–{label}' join for any
// other granularity — mirrors monthly_allocation.py's _combined_label().
function _mbCombinedPeriodLabel(first, last) {
  const firstWords = first.label.split(" ");
  const lastWords = last.label.split(" ");
  const isMonthStyle = firstWords.length === 2 && lastWords.length === 2 && /^\d+$/.test(firstWords[1]) && /^\d+$/.test(lastWords[1]);
  if (isMonthStyle && firstWords[1] === lastWords[1]) return `${firstWords[0]}–${last.label}`;
  return `${first.label}–${last.label}`;
}

// catalog minimum_spend is a MONTHLY figure — scales it to "one unit of
// state.timeUnit" the same way monthly_allocation.py's
// _GRANULARITY_MINIMUM_SCALE does. Shared by Step 04's own flat
// below-minimum check (li.monthly_budget is "$ per one {unit} period",
// same as everywhere else in this app — see the module-level note by
// state.timeUnit) AND _mbEffectiveMinimumForPeriod below (which further
// multiplies by a specific period's period_count, >1 only for a merged
// Step 06 bucket).
const _MB_GRANULARITY_MIN_SCALE = { week: 12 / 52, month: 1, quarter: 3 };
function _timeUnitMinimumScale() { return _MB_GRANULARITY_MIN_SCALE[state.timeUnit] ?? 1; }

// Deliberately NOT further prorated by the period's own active_days —
// see monthly_allocation.py's _effective_minimum_for_period docstring for
// why a partial period still gets the FULL per-granularity minimum
// (that's what makes merging necessary rather than redundant).
function _mbEffectiveMinimumForPeriod(minimumSpend, period) {
  return (minimumSpend || 0) * _timeUnitMinimumScale() * (period.period_count || 1);
}

// Granularity-aware display text — the single source every "Month"/
// "Monthly"/"# of months" string in Step 04/05/06 reads from, so the
// toggle actually relabels everywhere rather than just changing the math.
const _MB_UNIT_NOUN = { week: "Week", month: "Month", quarter: "Quarter" };
const _MB_UNIT_NOUN_PLURAL = { week: "Weeks", month: "Months", quarter: "Quarters" };
const _MB_UNIT_ADJECTIVE = { week: "Weekly", month: "Monthly", quarter: "Quarterly" };
function _mbUnitNoun() { return _MB_UNIT_NOUN[state.timeUnit] || "Month"; }
function _mbUnitNounPlural() { return _MB_UNIT_NOUN_PLURAL[state.timeUnit] || "Months"; }
function _mbUnitAdjective() { return _MB_UNIT_ADJECTIVE[state.timeUnit] || "Monthly"; }

// Day-prorated split — mirrors monthly_allocation.py's prorated_allocation()
// exactly, including "last month absorbs the rounding remainder" so dollars
// always sum to EXACTLY totalBudget.
function _mbProratedDefaultAllocation(totalBudget, months) {
  if (!months.length) return {};
  const totalActiveDays = months.reduce((s, m) => s + m.active_days, 0) || 1;
  const allocations = {};
  let running = 0;
  months.slice(0, -1).forEach(m => {
    const share = Math.round(totalBudget * (m.active_days / totalActiveDays) * 100) / 100;
    allocations[m.key] = share;
    running += share;
  });
  allocations[months[months.length - 1].key] = Math.round((totalBudget - running) * 100) / 100;
  return allocations;
}

// Even split — mirrors monthly_allocation.py's even_allocation() exactly,
// same "last month absorbs the rounding remainder" rule as the prorated
// version above, so the two are interchangeable everywhere a default gets
// computed.
function _mbEvenDefaultAllocation(totalBudget, months) {
  if (!months.length) return {};
  const share = Math.round(totalBudget / months.length * 100) / 100;
  const allocations = {};
  let running = 0;
  months.slice(0, -1).forEach(m => {
    allocations[m.key] = share;
    running += share;
  });
  allocations[months[months.length - 1].key] = Math.round((totalBudget - running) * 100) / 100;
  return allocations;
}

// THE one place a "default" allocation gets computed from a total — every
// existing call site (checkbox-check, reset button, a new line auto-
// joining, the plan-summary's not-yet-customized preview) calls this one
// dispatcher rather than either concrete implementation directly, so
// state.mbDistributionMode is the single source of truth for what
// "default" currently means, with zero other call sites needing to know
// or care which mode is active.
function _mbDefaultAllocation(totalBudget, months) {
  return state.mbDistributionMode === "prorated"
    ? _mbProratedDefaultAllocation(totalBudget, months)
    : _mbEvenDefaultAllocation(totalBudget, months);
}

// Mirrors reconcile_allocation() exactly — the SAME balanced/remaining
// definition the server uses, so a client-side "balanced ✓" can never
// disagree with /api/generate's own gate.
function _mbReconcile(totalBudget, allocations) {
  // Round the target to cents too, not just the allocated sum — a
  // sub-cent totalBudget (e.g. from a Gross->Net conversion or a
  // percentage split upstream) previously survived into this raw
  // subtraction, where binary float representation error could push an
  // otherwise-exact match just past _MB_CENT (e.g. 1738.675 - 1738.68 ===
  // -0.005000000000109139, not -0.005) — a real 100%-allocated period
  // then displayed a nonsensical "Over-allocated by 0.0% / $0" red badge.
  totalBudget = Math.round((totalBudget || 0) * 100) / 100;
  const allocated = Math.round(Object.values(allocations).reduce((s, v) => s + (v || 0), 0) * 100) / 100;
  const remaining = Math.round((totalBudget - allocated) * 100) / 100;
  const allocatedPct = totalBudget ? (allocated / totalBudget * 100) : 0;
  return {
    allocated, remaining,
    allocated_pct: allocatedPct,
    remaining_pct: 100 - allocatedPct,
    balanced: Math.abs(remaining) <= _MB_CENT,
    over_allocated: remaining < -_MB_CENT,
  };
}

// Effective calendar months for the CURRENTLY ACTIVE tier — its own
// start/end override if set, else the campaign-level Step 02 dates
// (exactly _effectiveStartDate/_effectiveEndDate's own fallback).
function _mbEffectiveMonths() {
  const startRaw = _effectiveStartDate(state.activeTierLabel);
  const endRaw = _effectiveEndDate(state.activeTierLabel);
  const start = _mbParseDate(startRaw);
  const end = _mbParseDate(endRaw);
  if (!start || !end) return null;
  const basePeriods = _mbPeriodsBetween(start, end, state.timeUnit);
  return _mbApplyPeriodMerges(basePeriods, state.activeTierPeriodMergeGroups);
}

// The BASE (pre-merge) period list for the active tier — needed by the
// merge-controls UI, which offers "combine with next" on the real
// underlying periods, not whatever's already been merged.
function _mbEffectiveMonthsUnmerged() {
  const startRaw = _effectiveStartDate(state.activeTierLabel);
  const endRaw = _effectiveEndDate(state.activeTierLabel);
  const start = _mbParseDate(startRaw);
  const end = _mbParseDate(endRaw);
  if (!start || !end) return null;
  return _mbPeriodsBetween(start, end, state.timeUnit);
}

// Line-item budget changed SINCE THIS ALLOCATION WAS LAST (RE)BUILT —
// rescale every month's DOLLAR figure so its PERCENTAGE stays exactly
// what the planner set, per the explicit "percentages remain constant,
// dollars recalculate" preference.
//
// Deliberately does NOT infer "budget changed" from the allocation simply
// not summing to the current total — that's ALSO exactly what a
// planner's still-mid-edit, genuinely-unbalanced state looks like, and
// silently "fixing" that on every render (this function used to run on
// every renderMonthlyBreakdown() call, including one fired by every
// keystroke) would erase the very validation state the planner needs to
// see. li._mbBaseline instead records the total this allocation was
// last deliberately built against (set by _mbSetAllocation below,
// touched ONLY there) — a mismatch against li._mbBaseline means the
// underlying monthly_budget/months genuinely changed elsewhere (Step 04)
// since; the allocation's OWN internal sum is irrelevant to that question.
function _mbRescaleForBudgetChange(li) {
  const total = li.monthly_budget * li.months;
  if (!li.monthly_allocations || li._mbBaseline === undefined || Math.abs(li._mbBaseline - total) <= _MB_CENT) {
    return li.monthly_allocations;
  }
  const keys = Object.keys(li.monthly_allocations);
  if (!(li._mbBaseline > _MB_CENT)) {
    // Built against $0, so there's no split to keep. Rescaling from zero would put the whole
    // new budget in the last period; spread it evenly over the same periods instead.
    li._mbBaseline = total;
    return _mbEvenDefaultAllocation(total, keys.map(key => ({ key })));
  }
  const priorBaseline = li._mbBaseline;
  const rescaled = {};
  let running = 0;
  keys.slice(0, -1).forEach(k => {
    const share = Math.round(total * (li.monthly_allocations[k] / priorBaseline) * 100) / 100;
    rescaled[k] = share;
    running += share;
  });
  rescaled[keys[keys.length - 1]] = Math.round((total - running) * 100) / 100;
  li._mbBaseline = total;
  return rescaled;
}

// The ONE place monthly_allocations gets (re)built wholesale from a total
// (as opposed to a single month being hand-edited) — always stamps
// _mbBaseline so _mbRescaleForBudgetChange above can later tell "budget
// moved since" apart from "planner hasn't balanced this yet".
function _mbSetAllocation(li, allocations) {
  li.monthly_allocations = allocations;
  li._mbBaseline = li.monthly_budget * li.months;
}

// Campaign dates, the time-unit toggle, OR the active tier's period-merge
// groups changed since this allocation was set — preserve every dollar
// that still applies (never silently destroy a planner's figure), drop
// periods no longer in the flight, and leave a brand-new period at $0 —
// any resulting imbalance surfaces through the normal validation state
// rather than being silently auto-fixed.
//
// A NEW period's key is looked up by SUMMING whatever old allocation(s)
// its own `key.split("+")` covers — for an unchanged period (no "+") this
// is just its own old value (identical to the old month-only behavior);
// for a period newly formed by merging (e.g. "2026-09+2026-10"), this
// sums the two old separate values, so merging PRESERVES the combined
// total exactly rather than resetting it. Un-merging (or switching time
// units entirely) can't recover a since-collapsed split the same way —
// there's no old "2026-09" key left once it was already merged into
// "2026-09+2026-10" — so a freshly-split period legitimately starts at
// $0, same as any other brand-new period; the planner re-splits manually.
function _mbReconcileMonthsForDateChange(li, months) {
  if (!li.monthly_allocations) return li.monthly_allocations;
  const old = li.monthly_allocations;
  const next = {};
  months.forEach(m => {
    // An EXACT match first — covers "this period's key is unchanged from
    // before", including an already-merged period staying merged the
    // same way on a re-render (old[m.key] holds the combined value
    // directly; decomposing "A+B" and looking up old["A"]/old["B"]
    // separately would miss it, since old never had those split back out
    // once merged — that was the actual bug this exact-match check
    // fixes). Only falls through to split-and-sum for a period that's
    // NEWLY combining previously-separate old entries.
    if (Object.prototype.hasOwnProperty.call(old, m.key)) {
      next[m.key] = old[m.key];
      return;
    }
    const baseKeys = m.key.split("+");
    next[m.key] = Math.round(baseKeys.reduce((s, k) => s + (old[k] || 0), 0) * 100) / 100;
  });
  return next;
}

// This line's monthly split for the PLAN-LEVEL summary bar — its own
// customized allocation if it has one, else the same day-prorated default
// shown but never persisted, purely so the plan-level total is always a
// complete picture rather than silently excluding lines nobody's
// customized yet.
function _mbEffectiveDistribution(li, months) {
  if (li.monthly_allocations && Object.keys(li.monthly_allocations).length) return li.monthly_allocations;
  return _mbDefaultAllocation(li.monthly_budget * li.months, months);
}

function renderMonthlyBreakdown() {
  const months = _mbEffectiveMonths();
  const emptyState = document.getElementById("mb-no-dates");
  const content = document.getElementById("mb-content");
  if (!months || !months.length) {
    emptyState.classList.remove("hidden");
    content.classList.add("hidden");
    _mbSetContinueEnabled(true);  // nothing to validate — never block on a missing-dates state
    return;
  }
  emptyState.classList.add("hidden");
  content.classList.remove("hidden");

  document.querySelectorAll("#mb-mode-tabs .tier-tab").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.mode === state.mbDistributionMode);
  });

  const eligibleLines = state.lineItems.filter(li => !li.is_added_value);
  const anyLineActive = eligibleLines.some(li => li.monthly_allocations && Object.keys(li.monthly_allocations).length);

  eligibleLines.forEach(li => {
    if (li.monthly_allocations && Object.keys(li.monthly_allocations).length) {
      li.monthly_allocations = _mbReconcileMonthsForDateChange(li, months);
      if (li._mbBaseline === undefined) {
        // First time this render loop has seen this line's data (e.g.
        // just reopened a past proposal, or a manually-injected/legacy
        // allocation) — seed the baseline at whatever total it has RIGHT
        // NOW rather than treating "no baseline yet" as "budget changed,
        // rescale immediately." A genuinely unbalanced reopened plan
        // still surfaces as unbalanced (see _mbLineItemBlockHtml's own
        // reconcile check) — this only controls whether THIS function
        // silently rewrites its numbers on the very first render.
        li._mbBaseline = li.monthly_budget * li.months;
      } else {
        li.monthly_allocations = _mbRescaleForBudgetChange(li);
      }
    } else if (anyLineActive && li._mbBaseline === undefined) {
      // "Line item added" (genuinely never seen by this render loop before
      // — _mbBaseline is only ever undefined the first time) while the
      // feature is already in use elsewhere on this tier — join it
      // automatically rather than leaving the plan-level total silently
      // incomplete for this line. The _mbBaseline check is what stops
      // this from also re-triggering for a line the planner explicitly
      // UNCHECKED (monthly_allocations is empty either way, but an
      // unchecked line keeps its baseline — see the checkbox handler).
      _mbSetAllocation(li, _mbDefaultAllocation(li.monthly_budget * li.months, months));
    }
  });

  document.getElementById("mb-line-items").innerHTML = eligibleLines.map(li => _mbLineItemBlockHtml(li, months)).join("")
    || `<p class="mb-empty-hint">No line items yet — add products in Step 04 first.</p>`;
  _mbWireLineItemBlocks(months);
  _mbRenderPlanSummary(months);
  _mbUpdateContinueState(months);
}

// "For reference" — impressions (or, for a CPP/rating-point product,
// points) that this month's $ figure would buy. Reuses calcMaxImpsFromSpend()
// exactly as Step 05's Avails grid does (through _effectiveProduct, so a
// Step 04 rate/estimated-CPM override is respected here too) — never a
// second, separately-maintained conversion formula. Returns "" when the
// product has no rate/estimated-CPM to convert with at all (a pure
// custom-quote Fixed product) — nothing to show, not a guessed number.
function _mbUnitsRefText(li, product, dollars) {
  if (!product || !dollars) return "";
  const eff = _effectiveProduct(li, product);
  const result = calcMaxImpsFromSpend(eff, dollars);
  if (!result) return "";
  const formatted = formatImpsDisplay(result.value, result.estimated);
  return (eff.pricing_model || "").toUpperCase() === "CPP" ? `${formatted} pts` : `${formatted} imps`;
}

function _mbLineItemBlockHtml(li, months) {
  const product = state.productIndex[li.product_name];
  const total = li.monthly_budget * li.months;
  const enabled = !!(li.monthly_allocations && Object.keys(li.monthly_allocations).length);
  const allocations = enabled ? li.monthly_allocations : {};
  const r = _mbReconcile(total, allocations);
  const minSpend = product ? (product.minimum_spend || 0) : 0;

  const rows = months.map(m => {
    const dollars = allocations[m.key] || 0;
    const pct = total ? (dollars / total * 100) : 0;
    const effectiveMin = _mbEffectiveMinimumForPeriod(minSpend, m);
    const belowMin = enabled && effectiveMin > 0 && dollars + _MB_CENT < effectiveMin;
    const unitsRef = enabled ? _mbUnitsRefText(li, product, dollars) : "";
    return `
      <tr data-month="${m.key}" class="${belowMin ? "mb-row-warn" : ""}">
        <td class="mono mb-period-cell"><span class="mb-period-label">${escapeHtml(m.label)}</span><span class="mb-period-range">${escapeHtml(m.date_range_label || "")}</span></td>
        <td><input type="number" step="0.01" min="0" max="100" class="mb-pct-input" data-line="${li.id}" data-month="${m.key}" value="${enabled ? Math.round(pct * 100) / 100 : ""}" ${enabled ? "" : "disabled"} /></td>
        <td><input type="number" step="1" min="0" class="mb-dollar-input" data-line="${li.id}" data-month="${m.key}" value="${enabled ? dollars.toFixed(2) : ""}" ${enabled ? "" : "disabled"} /></td>
        <td class="mb-units-ref mono">${escapeHtml(unitsRef)}</td>
        <td class="mb-validation">${belowMin ? `⚠ Below min ($${effectiveMin.toLocaleString(undefined, { maximumFractionDigits: 0 })})` : (enabled ? "✓" : "")}</td>
      </tr>`;
  }).join("");

  const statusClass = !enabled ? "mb-status-off" : (r.balanced ? "mb-status-ok" : (r.over_allocated ? "mb-status-over" : "mb-status-under"));
  const statusText = !enabled ? `Not using ${_mbUnitAdjective()} Breakdown`
    : r.balanced ? `Allocated: 100% / ${money(total)}`
    : r.over_allocated ? `Over-allocated by ${Math.abs(r.remaining_pct).toFixed(1)}% / ${money(Math.abs(r.remaining))}`
    : `Allocated: ${r.allocated_pct.toFixed(1)}% / ${money(r.allocated)} — Remaining: ${r.remaining_pct.toFixed(1)}% / ${money(r.remaining)}`;

  return `
    <details class="mb-line-block" ${enabled ? "open" : ""} data-line-id="${li.id}">
      <summary>
        <span class="mb-line-name">${escapeHtml(product ? product.name : li.product_name)}</span>
        <span class="mb-line-total">Total: ${money(total)}</span>
        <label class="mb-enable-toggle" onclick="event.stopPropagation()">
          <input type="checkbox" class="mb-enable-checkbox" data-line="${li.id}" ${enabled ? "checked" : ""} />
          Use ${_mbUnitAdjective()} Breakdown
        </label>
        <span class="mb-status-badge ${statusClass}">${escapeHtml(statusText)}</span>
      </summary>
      <div class="mb-line-body">
        <table class="mb-table">
          <thead><tr><th>${escapeHtml(_mbUnitNoun())}</th><th>%</th><th>$</th><th>Reference</th><th>Status</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
        <button type="button" class="btn-secondary mb-reset-btn" data-line="${li.id}" ${enabled ? "" : "disabled"}>↺ Reset to ${state.mbDistributionMode === "prorated" ? "day-prorated" : "even"} default</button>
      </div>
    </details>`;
}

function _mbFindLineItem(id) { return state.lineItems.find(li => String(li.id) === String(id)); }

function _mbWireLineItemBlocks(months) {
  document.querySelectorAll(".mb-enable-checkbox").forEach(cb => {
    cb.addEventListener("change", (e) => {
      const li = _mbFindLineItem(e.target.dataset.line);
      if (!li) return;
      if (e.target.checked) {
        _mbSetAllocation(li, _mbDefaultAllocation(li.monthly_budget * li.months, months));
      } else {
        // Deliberately keep li._mbBaseline (don't delete it) — it's the
        // ONLY signal that distinguishes "this line was already through
        // Monthly Breakdown and got explicitly turned off" from "this
        // line has never been touched at all". Deleting it used to make
        // an unchecked line indistinguishable from a brand-new one, so
        // renderMonthlyBreakdown()'s "auto-join a new line while the
        // feature's already active elsewhere" convenience immediately
        // re-checked it on the very next render — the exact "checkbox
        // isn't unselectable" bug this fixes.
        li.monthly_allocations = null;
      }
      renderMonthlyBreakdown();
    });
  });
  document.querySelectorAll(".mb-reset-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const li = _mbFindLineItem(btn.dataset.line);
      if (!li) return;
      _mbSetAllocation(li, _mbDefaultAllocation(li.monthly_budget * li.months, months));
      renderMonthlyBreakdown();
    });
  });
  document.querySelectorAll(".mb-pct-input").forEach(inp => {
    inp.addEventListener("input", (e) => {
      const li = _mbFindLineItem(e.target.dataset.line);
      if (!li || !li.monthly_allocations) return;
      const total = li.monthly_budget * li.months;
      const pct = parseFloat(e.target.value);
      li.monthly_allocations[e.target.dataset.month] = isNaN(pct) ? 0 : Math.round(total * pct / 100 * 100) / 100;
      _mbLiveUpdateAfterEdit(li, months, e.target);
    });
    inp.addEventListener("blur", () => renderMonthlyBreakdown());
  });
  document.querySelectorAll(".mb-dollar-input").forEach(inp => {
    inp.addEventListener("input", (e) => {
      const li = _mbFindLineItem(e.target.dataset.line);
      if (!li || !li.monthly_allocations) return;
      const dollars = parseFloat(e.target.value);
      li.monthly_allocations[e.target.dataset.month] = isNaN(dollars) ? 0 : Math.round(dollars * 100) / 100;
      _mbLiveUpdateAfterEdit(li, months, e.target);
    });
    inp.addEventListener("blur", () => renderMonthlyBreakdown());
  });
}

// Non-destructive live update after a single %/$ keystroke in Monthly
// Breakdown — patches only the paired field in this row, this row's own
// status cell, this line's status badge, and the plan-level summary, all
// via direct DOM writes. Deliberately never touches the input actually
// being typed in and never rebuilds the table (that's what the full
// renderMonthlyBreakdown() does by replacing #mb-line-items' innerHTML —
// which destroys and recreates every <input> in it, dropping focus after
// every single keystroke and forcing a re-click to keep typing: the exact
// bug this fixes). blur on either input still triggers the full render
// (wired above), so rounding/disabled-state always catch up once the
// planner leaves the field.
function _mbLiveUpdateAfterEdit(li, months, editedInput) {
  const total = li.monthly_budget * li.months;
  const monthKey = editedInput.dataset.month;
  const dollars = li.monthly_allocations[monthKey] || 0;
  const pct = total ? (dollars / total * 100) : 0;

  const row = editedInput.closest("tr");
  if (row) {
    const pctInput = row.querySelector(".mb-pct-input");
    const dollarInput = row.querySelector(".mb-dollar-input");
    if (pctInput && pctInput !== editedInput) pctInput.value = Math.round(pct * 100) / 100;
    if (dollarInput && dollarInput !== editedInput) dollarInput.value = dollars.toFixed(2);

    const product = state.productIndex[li.product_name];
    const minSpend = product ? (product.minimum_spend || 0) : 0;
    const period = months.find(m => m.key === monthKey);
    const effectiveMin = period ? _mbEffectiveMinimumForPeriod(minSpend, period) : minSpend;
    const belowMin = effectiveMin > 0 && dollars + _MB_CENT < effectiveMin;
    row.classList.toggle("mb-row-warn", belowMin);
    const validationCell = row.querySelector(".mb-validation");
    if (validationCell) validationCell.textContent = belowMin ? `⚠ Below min ($${effectiveMin.toLocaleString(undefined, { maximumFractionDigits: 0 })})` : "✓";
    const unitsRefCell = row.querySelector(".mb-units-ref");
    if (unitsRefCell) unitsRefCell.textContent = _mbUnitsRefText(li, product, dollars);
  }

  const block = editedInput.closest(".mb-line-block");
  const r = _mbReconcile(total, li.monthly_allocations);
  const badge = block ? block.querySelector(".mb-status-badge") : null;
  if (badge) {
    const statusClass = r.balanced ? "mb-status-ok" : (r.over_allocated ? "mb-status-over" : "mb-status-under");
    const statusText = r.balanced ? `Allocated: 100% / ${money(total)}`
      : r.over_allocated ? `Over-allocated by ${Math.abs(r.remaining_pct).toFixed(1)}% / ${money(Math.abs(r.remaining))}`
      : `Allocated: ${r.allocated_pct.toFixed(1)}% / ${money(r.allocated)} — Remaining: ${r.remaining_pct.toFixed(1)}% / ${money(r.remaining)}`;
    badge.className = `mb-status-badge ${statusClass}`;
    badge.textContent = statusText;
  }

  _mbRenderPlanSummary(months);
  _mbUpdateContinueState(months);
}

function _mbRenderPlanSummary(months) {
  const eligibleLines = state.lineItems.filter(li => !li.is_added_value);
  const totals = {};
  months.forEach(m => { totals[m.key] = 0; });
  eligibleLines.forEach(li => {
    const dist = _mbEffectiveDistribution(li, months);
    months.forEach(m => { totals[m.key] += dist[m.key] || 0; });
  });
  const grandTotal = months.reduce((s, m) => s + totals[m.key], 0);
  const cells = months.map((m, i) => {
    const isMerged = m.key.includes("+");
    // "Combine with next" only makes sense before the LAST cell, and
    // ties this option's whole period list together — see _mbMergePeriodWithNext.
    const mergeBtn = !isMerged && i < months.length - 1
      ? `<button type="button" class="mb-merge-btn" data-merge-key="${escapeHtml(m.key)}" title="Combine this ${_mbUnitNoun().toLowerCase()} with the next one — useful when a partial ${_mbUnitNoun().toLowerCase()} is too small to clear a product's minimum on its own">⛓ Combine with next</button>`
      : "";
    const unmergeBtn = isMerged
      ? `<button type="button" class="mb-unmerge-btn" data-unmerge-key="${escapeHtml(m.key)}" title="Split this combined ${_mbUnitNoun().toLowerCase()} back into its separate parts">✕ Split apart</button>`
      : "";
    return `
    <div class="mb-summary-cell ${isMerged ? "mb-summary-cell-merged" : ""}">
      <div class="mb-summary-month">${escapeHtml(m.label)}</div>
      <div class="mb-summary-range">${escapeHtml(m.date_range_label || "")}</div>
      <div class="mb-summary-amount">${money(totals[m.key])}</div>
      ${mergeBtn}${unmergeBtn}
    </div>`;
  }).join("");
  document.getElementById("mb-plan-summary").innerHTML = `
    <div class="mb-summary-label">Total plan spend by ${_mbUnitNoun().toLowerCase()} <small>(${state.mbDistributionMode === "prorated" ? "day-prorated" : "even-split"} estimate for any line not yet customized)</small></div>
    <div class="mb-summary-row">${cells}
      <div class="mb-summary-cell mb-summary-cell-total">
        <div class="mb-summary-month">Total</div>
        <div class="mb-summary-amount">${money(grandTotal)}</div>
      </div>
    </div>`;
  _mbWireMergeControls();
}

function _mbWireMergeControls() {
  document.querySelectorAll(".mb-merge-btn").forEach(btn => {
    btn.addEventListener("click", () => _mbMergePeriodWithNext(btn.dataset.mergeKey));
  });
  document.querySelectorAll(".mb-unmerge-btn").forEach(btn => {
    btn.addEventListener("click", () => _mbUnmergePeriod(btn.dataset.unmergeKey));
  });
}

// Combines the (already-possibly-merged) period `periodKey` with whichever
// period immediately follows it in the CURRENT effective (post-merge)
// list, into one bigger group — chaining this repeatedly builds up an
// N-way merge (e.g. "combine with next" twice chains 3 base periods into
// one). Tier-wide, not per-line (see state.activeTierPeriodMergeGroups'
// own comment) — every line's allocation is reconciled against the new
// merged key on the next renderMonthlyBreakdown() (see
// _mbReconcileMonthsForDateChange, which sums old values into the new
// combined key so this never silently drops a planner's numbers).
function _mbMergePeriodWithNext(periodKey) {
  const current = _mbEffectiveMonths();
  if (!current) return;
  const idx = current.findIndex(m => m.key === periodKey);
  if (idx === -1 || idx >= current.length - 1) return;
  const thisKeys = current[idx].key.split("+");
  const nextKeys = current[idx + 1].key.split("+");
  // Drop any existing group(s) that exactly match either side being
  // joined — they're being subsumed into the new, bigger group below,
  // and apply_period_merges only supports one group per base period.
  const combinedKey = current[idx].key;
  const nextKey = current[idx + 1].key;
  const remaining = (state.activeTierPeriodMergeGroups || []).filter(g => g.join("+") !== combinedKey && g.join("+") !== nextKey);
  remaining.push([...thisKeys, ...nextKeys]);
  state.activeTierPeriodMergeGroups = remaining;
  renderMonthlyBreakdown();
}

// Fully splits a merged period back into its individual base periods
// (not a partial un-chain — see _mbReconcileMonthsForDateChange's own
// comment on why a freshly-split period starts at $0 rather than trying
// to recover a since-collapsed split).
function _mbUnmergePeriod(periodKey) {
  state.activeTierPeriodMergeGroups = (state.activeTierPeriodMergeGroups || []).filter(g => g.join("+") !== periodKey);
  renderMonthlyBreakdown();
}

// Blocks "Continue" (never "Skip") while ANY enabled line item is
// unbalanced — the hard "must balance to be considered complete"
// requirement; /api/generate re-validates this authoritatively regardless
// as a backstop for anyone who skips past an unbalanced state anyway.
function _mbUpdateContinueState(months) {
  const anyUnbalanced = state.lineItems.some(li => {
    if (li.is_added_value || !li.monthly_allocations || !Object.keys(li.monthly_allocations).length) return false;
    return !_mbReconcile(li.monthly_budget * li.months, li.monthly_allocations).balanced;
  });
  _mbSetContinueEnabled(!anyUnbalanced);
}

function _mbSetContinueEnabled(enabled) {
  const btn = document.getElementById("monthly-breakdown-continue-btn");
  if (!btn) return;
  btn.disabled = !enabled;
  btn.title = enabled ? "" : "One or more line items' monthly allocations don't balance to 100% / the full budget yet.";
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
    // Step 05 renders, even ones the planner never touches, which would
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
      <p class="avails-targeting-reminder">🎯 Target: ${escapeHtml(_effectiveTargetText(li))} &nbsp;·&nbsp; 📍 Geo: ${escapeHtml(_effectiveGeo(state.activeTierLabel) || "TBD")} &nbsp;·&nbsp; 📅 ${escapeHtml(_effectiveStartDate(state.activeTierLabel) || "TBD")} – ${escapeHtml(_effectiveEndDate(state.activeTierLabel) || "TBD")}</p>
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
        ` : `
        <label>SOV %<small> (no imps/spend to calculate it from in free-form — enter your own estimate)</small>
          <input type="text" inputmode="decimal" placeholder="—" class="sov-freeform-input"
                 value="${existing.sov_pct_freeform != null ? existing.sov_pct_freeform + "%" : ""}"
                 data-lid="${escapeAttr(li.id)}" data-key="sov_pct_freeform" />
        </label>
        `}
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

  // Free-form SOV %: the one free-form field that ISN'T free text — it
  // always keeps "%" formatting and drives the same traffic-light
  // conditional formatting as the numeric-mode calculated SOV, just
  // planner-declared instead of computed (there's nothing to compute it
  // FROM in free-form — no real imps/spend numbers to divide).
  grid.querySelectorAll('input[data-key="sov_pct_freeform"]').forEach(inp => {
    inp.addEventListener("focus", () => {
      const n = parseFormattedInput(inp.value);
      inp.value = n === null ? "" : String(n);
    });
    inp.addEventListener("blur", () => {
      const lid = inp.dataset.lid;
      state.availsData[lid] = state.availsData[lid] || {};
      let n = parseFormattedInput(inp.value);
      if (n !== null) n = Math.max(0, Math.min(100, Math.round(n * 10) / 10));
      state.availsData[lid].sov_pct_freeform = n;
      inp.value = n === null ? "" : `${n}%`;
      applySovDisplay(lid, n);
    });
  });

  // On focus: strip formatting so the raw number is easy to edit. Excludes
  // the free-form checkbox (not a value-bearing text field — its `.value`
  // is meaningless, always "on" per the checkbox default) and the "_text"
  // free-form inputs above (running parseFormattedInput on "50 to 100"
  // would strip the letters and glue the digits together into "50100").
  grid.querySelectorAll('input:not([data-freeform-toggle]):not([data-key$="_text"]):not([data-key="sov_pct_freeform"])').forEach(inp => {
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
  grid.querySelectorAll('input:not([data-freeform-toggle]):not([data-key$="_text"]):not([data-key="sov_pct_freeform"])').forEach(inp => {
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
  const tierLabel = multiTier ? ` (${_tierDisplayName(state.activeTierLabel)})` : "";

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
      availsWarningEl.innerHTML = `<strong>⚠ No avails entered for ${emptyTiers.map(t => escapeHtml(_tierDisplayName(t.label))).join(", ")}</strong> — other options have avails, so ${emptyTiers.length === 1 ? "this one" : "these"} will export without any. Go back to Step 05, switch to that tab, and enter avails (or use "Copy avails from") if that's not intentional.`;
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
      return `${escapeHtml(_tierDisplayName(t.label))}: ${money(m)}/mo · ${(t.line_items || []).length} products`;
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
    strategy_brief: _briefWithSelectedTactics(),
    roadblocks: state.roadblocks || null,
    raw_notion_text: state.rawNotionText || null,
    addons: Object.entries(state.addons).map(([product_name, amount]) => ({ product_name, amount })),
    // Step 06's plan-wide default-split choice — only matters for the
    // export's own fallback estimate on a line that was never individually
    // customized (build_monthly_breakdown_tab's per-month total, and the
    // inline total row next to "TOTAL DIGITAL MONTHLY"); a line WITH its
    // own monthly_allocations already carries real numbers regardless.
    monthly_distribution_mode: state.mbDistributionMode,
    // Step 04's Week/Month/Quarter toggle — drives which period
    // granularity the server validates monthly_allocations against and
    // which one the export's Monthly Breakdown columns/labels use.
    time_unit: state.timeUnit,
    // Planner override for the campaign-name segment of the naming
    // convention (see the proposal-name-bar's Edit button) — null unless
    // explicitly set, in which case it wins over the AI's own guess.
    campaign_name_override: state.manualCampaignNameOverride,
  };
  const btn = document.getElementById("generate-btn");
  btn.disabled = true;
  btn.textContent = "Generating + Intelligence Pack…";
  const loadingEl = document.getElementById("generate-loading");
  const loadingText = document.getElementById("generate-loading-text");
  // Rotates through the REAL stages /api/generate goes through server-side
  // (in this order — see main.py's own numbered comments), not a fake
  // percentage bar: there's no live progress channel from the server (one
  // request/response, no SSE/websocket), so this is a best-effort "here's
  // roughly what's happening" cue rather than a literal synced progress
  // meter. Still much better than a static button label for a call that
  // routinely takes 10-30+ seconds.
  const GENERATE_STAGES = [
    "Calling AI enrichment (campaign name, emails, blurbs)…",
    "Building Excel workbook and formulas…",
    "Assembling PowerPoint decks (if included)…",
    "Almost there — packaging your download…",
  ];
  let stageIdx = 0;
  loadingText.textContent = GENERATE_STAGES[0];
  loadingEl.classList.remove("hidden");
  const stageTimer = setInterval(() => {
    stageIdx = Math.min(stageIdx + 1, GENERATE_STAGES.length - 1);
    loadingText.textContent = GENERATE_STAGES[stageIdx];
  }, 4000);
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
    clearInterval(stageTimer);
    loadingEl.classList.add("hidden");
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
    if (tiers.length > 1) lines.push(`${_tierDisplayName(t.label)}:`);
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

// Refines the Gamma outline via the ONE AI call this whole feature makes
// — building the outline itself (above) is pure client-side string
// assembly with zero request, so this only runs when the planner
// explicitly clicks "Refine".
async function onGammaOutlineReprompt() {
  const text = document.getElementById("gamma-reprompt-input").value.trim();
  if (!text) { alert("Enter what you'd like to change first."); return; }

  const btn = document.getElementById("gamma-reprompt-submit-btn");
  btn.disabled = true;
  btn.textContent = "Regenerating…";

  try {
    const res = await fetch("/api/refine-gamma-outline", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        outline: document.getElementById("gamma-outline-body").textContent,
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
    document.getElementById("gamma-outline-body").textContent = data.outline || document.getElementById("gamma-outline-body").textContent;

    document.getElementById("gamma-reprompt-area").classList.add("hidden");
    document.getElementById("gamma-reprompt-btn").style.display = "";
    document.getElementById("gamma-reprompt-input").value = "";
  } catch (e) {
    alert("Reprompt failed: " + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "↺ Regenerate Outline";
  }
}

// --------------------------------------------------------------------------
// Step 7: seller-email mailto link — "the email that the planner sends to
// the seller," as a ready mailto: link instead of copy/paste only. Ports
// the CC-assembly logic of the planning team's existing Notion mailto
// formula (base team CC + market-based CCs + per-request CCs + product/
// renewal-based CCs, deduped) onto this app's own data:
//   - Market + per-market CCs are now admin-editable (Markets tab) instead
//     of hardcoded in the formula — fetched from the server since that's
//     where the admin config lives.
//   - The old Tag T1? checkbox branch is now a plain spend rule (planner
//     confirmed, replacing the Notion-side manual tag): T1 CCs are added
//     ON TOP of the regular market CCs the moment ANY tier's own monthly
//     spend crosses the admin-set threshold (starts at $10k) — not instead
//     of the market list, and a proposal with one big tier and one small
//     one still escalates.
//   - "Renewal" detection uses the actual parsed Request Type field
//     instead of string-matching "renewal" in the project name — more
//     reliable, same intent (renewals CC an extra couple of people).
//   - The body is this app's own real, AI-written internal email (already
//     addressed to the AE by name) rather than the Notion formula's fixed
//     "*INSERT OBJECTIVE HERE*" placeholder template — strictly better
//     content, already sitting right above this button once enrichment
//     succeeds. If enrichment didn't produce a body, the link stays hidden
//     rather than opening an empty draft.
// --------------------------------------------------------------------------

const _EMAIL_RE = /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g;

function _dedupeEmails(list) {
  const seen = new Set();
  return list.map(e => (e || "").trim()).filter(e => {
    if (!e) return false;
    const k = e.toLowerCase();
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

async function buildInternalEmailMailtoLink() {
  const linkEl = document.getElementById("internal-email-mailto-link");
  const hintEl = document.getElementById("internal-email-mailto-hint");
  if (!linkEl) return;

  const req = state.parsed || {};
  const enrichment = state.enrichment || {};
  const body = enrichment.internal_email_body || "";

  if (!body) {
    // Nothing real to send (AI enrichment failed/skipped) — hide rather
    // than open an empty draft; the Copy button covers manual fallback.
    linkEl.classList.add("hidden");
    hintEl.classList.add("hidden");
    return;
  }

  const to = (req.salesperson_email || "").trim();
  // The persistent, copyable proposal title IS the subject line now — not
  // the AI's own subject guess — so the emailed subject always matches
  // what's shown at the top of the app, rather than varying with each AI
  // generation. Falls back to the AI subject only in the unlikely case the
  // title itself isn't set yet (shouldn't normally happen once a proposal
  // has been generated).
  const subject = state.finalProposalTitle || enrichment.internal_email_subject || "";

  let ccList = [];
  try {
    const res = await fetch(`/api/market-ccs?market=${encodeURIComponent(req.salesperson_market || "")}`);
    if (res.ok) {
      const marketData = await res.json();
      ccList = ccList.concat(marketData.ccs || []);

      // T1 escalation: added on top of the regular market CCs (not instead
      // of) the moment ANY tier's own spend crosses the threshold — a mix
      // of a big Option A and a small Option B still escalates.
      const threshold = marketData.t1_spend_threshold || 10000;
      const tierTotals = allTiersForSubmit().map(t =>
        (t.line_items || []).reduce((sum, li) => sum + (li.monthly_budget || 0), 0)
      );
      if (tierTotals.some(total => total >= threshold)) {
        ccList = ccList.concat(marketData.t1_ccs || []);
      }
    }
  } catch (e) {
    // Market-CC lookup failing shouldn't block sending the email at all.
  }

  ccList = ccList.concat((req.ccs || "").match(_EMAIL_RE) || []);

  const products = req.products_selected || [];
  if (products.some(p => /\bCTV\b|\bOTT\b/i.test(p))) ccList.push("joel.alcaraz@entravision.com");
  if (products.some(p => /tik\s*tok|meta|facebook|instagram/i.test(p))) ccList.push("amelia.arce@entravision.com");
  if ((req.request_type || "").toLowerCase().includes("renewal")) {
    ccList.push("amelia.arce@entravision.com", "joel.alcaraz@entravision.com");
  }

  ccList = _dedupeEmails(ccList);

  const mailto = `mailto:${encodeURIComponent(to)}` +
    `?subject=${encodeURIComponent(subject)}` +
    (ccList.length ? `&cc=${encodeURIComponent(ccList.join(","))}` : "") +
    `&body=${encodeURIComponent(body)}`;

  linkEl.href = mailto;
  linkEl.classList.remove("hidden");
  // Long AI-written emails can exceed what some clients (older Outlook
  // especially) reliably accept in a mailto: URL — just a courtesy heads
  // up, not a hard limit check.
  hintEl.classList.toggle("hidden", body.length < 1200);
}

async function showResult(data) {
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

  const pptxNetLink = document.getElementById("download-pptx-net-link");
  pptxNetLink.classList.toggle("hidden", !data.has_pptx_net);
  if (data.has_pptx_net) pptxNetLink.href = `/api/download-pptx-net/${data.proposal_id}`;
  const pptxGrossLink = document.getElementById("download-pptx-gross-link");
  pptxGrossLink.classList.toggle("hidden", !data.has_pptx_gross);
  if (data.has_pptx_gross) pptxGrossLink.href = `/api/download-pptx-gross/${data.proposal_id}`;

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

  await buildInternalEmailMailtoLink();

  result.scrollIntoView({ behavior: "smooth", block: "start" });
}

// --------------------------------------------------------------------------
// Step 7: reprompt the emails based on the planner's final review
// --------------------------------------------------------------------------

function _resetEmailRepromptScope() {
  const bothRadio = document.querySelector('input[name="email-reprompt-scope"][value="both"]');
  if (bothRadio) bothRadio.checked = true;
}

async function onEmailReprompt() {
  const text = document.getElementById("email-reprompt-input").value.trim();
  if (!text) { alert("Enter what you'd like to change first."); return; }
  if (!state.proposalId || !state.enrichment) return;
  const scopeInput = document.querySelector('input[name="email-reprompt-scope"]:checked');
  const scope = scopeInput ? scopeInput.value : "both";

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
        scope,
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
    _resetEmailRepromptScope();

    // The mailto link's subject/body are frozen at the moment they were
    // built — refresh it now so "Open in Email" reflects the just-revised text.
    await buildInternalEmailMailtoLink();
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
  status.innerHTML = '<span class="btn-inline-spinner"></span>Uploading to Drive…';
  // Everything below is awaited inside a try/catch on purpose — without it,
  // any failure (a network hiccup, a non-JSON error response, a timeout)
  // left the status text frozen on "Uploading to Drive…" forever with no
  // feedback at all, which is exactly the "stuck loading" symptom reported.
  try {
    const res = await fetch("/api/drive/upload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        proposal_id: state.proposalId,
        seller_email: state.parsed.salesperson_email || "",
      }),
    });
    if (!res.ok) throw new Error(`Server returned ${res.status}`);
    const data = await res.json();
    if (data.uploaded) {
      status.classList.add("ok");
      status.innerHTML = `✓ Uploaded — <a href="${data.shareable_link}" target="_blank" rel="noopener">Open in Drive</a>`;
    } else if (data.auth_url) {
      // Need OAuth authorization — opened as a real popup (not a plain
      // target="_blank" link) so the opener side can detect when it closes
      // and automatically retry the upload, instead of leaving the planner
      // to remember to click Upload again themselves.
      status.classList.add("warn");
      status.innerHTML = `Drive not authorized. <a href="#" id="drive-auth-link">Click here to authorize Google Drive</a> — it will retry automatically once you approve.`;
      document.getElementById("drive-auth-link").addEventListener("click", (e) => {
        e.preventDefault();
        openDriveAuthPopup(data.auth_url);
      });
    } else {
      status.classList.add("warn");
      status.textContent = "⚠ " + (data.reason || "Upload not configured");
    }
  } catch (e) {
    status.classList.add("warn");
    status.textContent = "⚠ Upload failed: " + e.message + " — try again.";
  }
}

// Google's OAuth consent/redirect pages send a strict
// Cross-Origin-Opener-Policy header that severs `window.opener` for the
// rest of that tab's life (a well-documented popup-flow gotcha, unrelated
// to this app's own code) — so the callback page's postMessage-to-opener
// can't be relied on to fire. Polling from the OPENER side instead (reading
// `popup.closed` on the handle *we* hold) sidesteps that entirely, since it
// never depends on the popup introspecting us back.
function openDriveAuthPopup(authUrl) {
  const popup = window.open(authUrl, "drive_auth", "width=520,height=680");
  if (!popup) {
    // Popup blocked by the browser — fall back to a plain new tab the
    // planner drives themselves, same as this app's previous behavior.
    window.open(authUrl, "_blank");
    return;
  }
  const status = document.getElementById("drive-status");
  const startedAt = Date.now();
  const giveUpAfterMs = 3 * 60 * 1000; // stop polling if it's left open unattended
  const poll = setInterval(() => {
    if (popup.closed) {
      clearInterval(poll);
      status.textContent = "Authorized — retrying upload…";
      onDriveUpload();
    } else if (Date.now() - startedAt > giveUpAfterMs) {
      clearInterval(poll);
    }
  }, 1000);
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
