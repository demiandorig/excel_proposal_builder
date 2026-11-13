/* ------------------------------------------------------------------------
 * Split view — two wizard steps on screen at once (budgets in Step 04 next to the avails in Step 05, for example), each one the REAL step section.
 *
 * Loaded after app.js and reads it by name, the way constanza.js does (`state`, the render functions). app.js calls three hooks, all guarded by
 * `typeof ... === "function"` so a page without this file works as it always did:
 *   _splitAfterGoToStep(n)   goToStep() has just moved the wizard to step n: keep the two panes in step with it
 *   _splitPillClick(n)       a step pill was clicked: a step that is already on screen only becomes the active one
 *   _splitNotify(step)       renderLineItems / renderAvailsGrid / renderMonthlyBreakdown ran: step's numbers may have changed
 *
 * Nothing is moved or cloned. The two sections stay where they are in the page (ids unique, getElementById keeps working, listeners, focus and scroll
 * stay put) and are laid out side by side by a few classes on <main class="canvas"> and on the sections (split-view.css); turning Split view off takes the
 * classes away again. Steps are always shown in step order (the lower one on the left).
 *
 * What is live. An edit in a pane (typing, a choice, a button that redraws the step) tells the OTHER pane to draw itself again through the same
 * render function it would run if the planner navigated there: no calculation is repeated here. READS says which step reads what:
 *   Step 04 follows 02 (dates, agency fee, budget) and 06 (a committed month figure changes a line's budget)
 *   Step 05 follows 02, 04, 06 (budgets, targets, dates, lines, options, billing period) and the Constanza cards in it with it
 *   Step 06 follows 02 and 04 (but not each keystroke of a budget: see flush())
 *   Step 08 follows 02, 04, 05, 06
 *   the Constanza dock (when open for a line) follows 02, 04, 06 through Constanza.refreshLine()
 * Steps 03 and 07 (AI results) are only shown side by side: nothing the planner edits elsewhere changes them.
 * A pane that starts the edit is never drawn again for it, and a pane that holds the keyboard is left alone until the keyboard leaves it.
 * ------------------------------------------------------------------------ */
(function () {
  "use strict";

  const MIN_WIDTH = 1100;                       // narrower than this and two steps do not fit: single view
  const PREF_KEY = "epb.splitView";             // { on, second } in localStorage: a per-browser preference, never part of the proposal
  const FLUSH_MS = 150;                         // an edit is passed on once typing pauses this long
  const READS = { 4: [2, 6], 5: [2, 4, 6], 6: [2, 4], 8: [2, 4, 5, 6] };
  const NAMES = { 2: "Review", 3: "Strategy", 4: "Curate", 5: "Avails", 6: "Monthly", 7: "Roadblocks", 8: "Generate" };
  const BY_NUMBER = (a, b) => a - b;

  const S = {
    on: false,                // the planner's choice (the toggle), remembered
    secondPref: "auto",       // "auto" (the neighbouring step) or the number of the step the planner picked to sit alongside
    mounted: false,           // two panes are on screen right now
    auto: true,               // the other pane is the neighbouring step and moves with the wizard; false: it stays where the planner put it
    pair: [0, 0],             // the two steps on screen, lower first
    active: 0,                // the one of them Back / Continue act on (always equal to state.step while mounted; goToStep has already overwritten that when a hook runs)
    shown: new Set(),
    pending: [],              // [{ source, typing }] edits not yet passed on
    timer: null,
    refreshing: false,        // a pane is being drawn again by us: its render hooks must not start another round
    deferred: new Map(),      // step -> Set of sources: a pane that held the keyboard when it should have been drawn again
  };

  const $ = (id) => document.getElementById(id);
  const section = (n) => $(`step-${n}`);
  const canvas = () => document.querySelector("main.canvas");
  const wide = () => (window.matchMedia ? window.matchMedia(`(min-width: ${MIN_WIDTH}px)`).matches : window.innerWidth >= MIN_WIDTH);
  const started = () => !!state.parsed && state.step >= 2;

  function stepName(n) {
    const label = n === 6 ? $("nav-step-6-label") : document.querySelector(`.step[data-step="${n}"] .step-label`);
    return (label && label.textContent.trim()) || NAMES[n] || `Step ${n}`;
  }
  const stepLabel = (n) => `Step ${String(n).padStart(2, "0")} · ${stepName(n)}`;

  // ---- which steps can sit on screen (pure where it can be: tests call these through SplitView._pure) -------------------------------------
  // A step is shown only once it could be reached by the pills (n <= furthest), or it is the next step of the active one and its page can be drawn from the
  // proposal as it stands (Avails, Monthly, Generate once there are lines; Step 03 / 04 / 07 do work of their own on a first visit, so they wait for Continue).
  function eligible(n, active, furthest, hasLines) {
    if (!(n >= 2 && n <= 8) || n === active) return false;
    if (n <= furthest) return true;
    return n === active + 1 && n >= 5 && n !== 7 && hasLines;
  }

  // The pair [lower, higher] for a wizard standing on `active`: the step the planner picked to sit alongside, else the next step, else the previous one.
  function pickPair(active, second, ok) {
    if (typeof second === "number" && ok(second)) return { pair: [active, second].sort(BY_NUMBER), auto: false };
    if (ok(active + 1)) return { pair: [active, active + 1], auto: true };
    if (ok(active - 1)) return { pair: [active - 1, active], auto: true };
    return null;
  }

  // Where the panes go when the wizard moves to step n. A step already on screen only becomes the active one. Otherwise, with a step the planner picked
  // to sit alongside, it stays and the pane that was active takes n. With the neighbouring step on automatic there is no answer here (null): the pair
  // is made again around n, which goes on the left with its next step beside it, as when Split view is turned on.
  function route(pair, active, auto, n) {
    if (pair[0] === n || pair[1] === n) return pair.slice();
    if (auto) return null;
    const kept = pair[0] === active ? pair[1] : pair[0];
    return [kept, n].sort(BY_NUMBER);
  }

  const okFor = (active) => (n) => eligible(n, active, state.furthestStep, !!(state.lineItems && state.lineItems.length));
  const candidates = (active) => [2, 3, 4, 5, 6, 7, 8].filter(okFor(active));

  // ---- the preference ----------------------------------------------------------------------------------------------------------------------
  function loadPref() {
    try {
      const raw = JSON.parse(localStorage.getItem(PREF_KEY) || "null");
      if (raw && typeof raw === "object") {
        S.on = raw.on === true;
        S.secondPref = Number.isInteger(raw.second) && raw.second >= 2 && raw.second <= 8 ? raw.second : "auto";
      }
    } catch (e) { /* storage blocked or unreadable: the defaults stand */ }
  }
  function savePref() {
    try { localStorage.setItem(PREF_KEY, JSON.stringify({ on: S.on, second: S.secondPref })); } catch (e) { /* the choice just is not remembered */ }
  }

  // ---- laying the panes out ----------------------------------------------------------------------------------------------------------------
  // The page lists Step 06 before Step 05 in the DOM. Tab order follows the DOM, so for that one pair Step 06 is moved behind Step 05 while Split view is on
  // (and back in front when it goes off). Only Step 06 moves, so the pane the planner is working in keeps its scroll position.
  function fixDomOrder(fiveFirst) {
    const five = section(5), six = section(6);
    if (!five || !six || five.parentNode !== six.parentNode) return;
    const sixFirst = !!(five.compareDocumentPosition(six) & Node.DOCUMENT_POSITION_PRECEDING);
    if (fiveFirst && sixFirst) five.after(six);
    else if (!fiveFirst && !sixFirst) five.before(six);
  }

  function paintPills() {
    for (let i = 1; i <= 8; i++) {
      const pill = document.querySelector(`.step[data-step="${i}"]`);
      if (!pill) continue;
      pill.classList.toggle("active", i === state.step);
      pill.classList.toggle("done", i < state.furthestStep);
      pill.classList.toggle("clickable", i <= state.furthestStep && i !== state.step);
      pill.classList.toggle("split-shown", S.mounted && S.pair.includes(i) && i !== state.step);
    }
  }

  function paintPanes() {
    for (let i = 1; i <= 8; i++) {
      const sec = section(i);
      if (!sec) continue;
      const here = S.mounted && S.pair.includes(i);
      sec.classList.toggle("hidden", S.mounted ? !here : i !== state.step);
      sec.classList.toggle("split-pane", here);
      sec.classList.toggle("split-pane-left", here && i === S.pair[0]);
      sec.classList.toggle("split-pane-right", here && i === S.pair[1]);
      sec.classList.toggle("split-pane-active", here && i === state.step);
      if (here) {
        sec.setAttribute("role", "region");
        sec.setAttribute("aria-label", `Step ${i}: ${stepName(i)}`);
      } else {
        sec.removeAttribute("role");
        sec.removeAttribute("aria-label");
      }
    }
  }

  function measure() {
    const c = canvas();
    if (!c || !S.mounted) return;
    document.documentElement.style.setProperty("--split-top", `${Math.round(c.getBoundingClientRect().top + window.scrollY)}px`);
  }

  // The toggle, the "show alongside" list and the hint under them.
  let selectKey = "";
  function chrome() {
    const bar = $("split-bar");
    if (!bar) return;
    const show = started();
    bar.classList.toggle("hidden", !show);
    if (!show) return;
    const fits = wide();
    const pairable = fits && candidates(state.step).length > 0;
    const btn = $("split-toggle"), hint = $("split-hint"), wrap = $("split-second-wrap"), sel = $("split-second-select");
    const why = !fits ? "Split view needs a window at least 1100 pixels wide." : (!pairable && !S.mounted ? "Open another step first — Continue once, or pick one in the step bar." : "");
    btn.disabled = !(S.mounted || pairable);
    btn.setAttribute("aria-pressed", String(S.mounted));
    btn.title = why || (S.mounted ? "Back to one step at a time" : "Show a second step next to this one");
    hint.textContent = why;
    wrap.classList.toggle("hidden", !S.mounted);
    if (!S.mounted) { selectKey = ""; return; }
    const list = candidates(state.step);
    const key = list.join(",") + "|" + state.step + "|" + list.map(stepName).join(",");
    if (key !== selectKey) {
      selectKey = key;
      sel.textContent = "";
      const auto = document.createElement("option");
      auto.value = "auto";
      auto.textContent = "Neighbouring step (automatic)";
      sel.appendChild(auto);
      list.forEach((n) => {
        const o = document.createElement("option");
        o.value = String(n);
        o.textContent = stepLabel(n);
        sel.appendChild(o);
      });
    }
    const other = S.pair[0] === state.step ? S.pair[1] : S.pair[0];
    sel.value = S.auto ? "auto" : String(other);
  }

  // Draws step n from the proposal as it stands, by the same functions navigating to it would run. Never draws the pane that started an edit (callers
  // decide), and puts the pane's scroll back where it was.
  function redraw(step, sources) {
    const sec = section(step);
    const top = sec ? sec.scrollTop : 0;
    S.refreshing = true;
    try {
      _resyncAllTierMonths();          // dates may have changed since the lines were last drawn (Step 02, an option's own dates)
      if (step === 4) {
        const fee = $("curate-agency-fee-input");
        if (fee && document.activeElement !== fee) fee.value = state.parsed.agency_fee != null ? state.parsed.agency_fee : "";
        if (sources && sources.includes(2)) {
          // what Continue does on the way into Step 04: the budget typed in Step 02 is the plan's total box
          const budget = state.parsed.monthly_budget || parseBudgetFromRenewal(state.parsed);
          const box = $("total-budget-target");
          if (budget && box && document.activeElement !== box) box.value = formatBudgetInputValue(Number(budget));
        }
        if (state.lineItems.length) renderLineItems();
      } else if (step === 5) {
        renderAvailsGrid();
      } else if (step === 6) {
        renderMonthlyBreakdown();
      } else if (step === 8) {
        renderGenerateSummary();
      }
    } catch (err) {
      console.error(`[split view] step ${step} could not be redrawn`, err);
    } finally {
      S.refreshing = false;
      if (sec) sec.scrollTop = top;
    }
  }

  function hasFocus(step) {
    const sec = section(step), el = document.activeElement;
    return !!(sec && el && el !== document.body && sec.contains(el));
  }

  // ---- mounting ---------------------------------------------------------------------------------------------------------------------------
  function teardown() {
    if (!S.mounted) return;
    S.mounted = false;
    clearTimeout(S.timer);
    S.timer = null;
    S.pending = [];
    S.deferred.clear();
    S.shown = new Set();
    S.active = 0;
    canvas().classList.remove("split-on");
    document.body.classList.remove("split-view-on");
    document.documentElement.style.removeProperty("--split-top");
    if (!document.documentElement.getAttribute("style")) document.documentElement.removeAttribute("style");     // (no empty style="" left behind)
    fixDomOrder(false);
    paintPanes();
    paintPills();
  }

  function apply() {
    const want = S.on && wide() && started();
    if (!want) { teardown(); chrome(); return; }
    if (!S.mounted || !S.pair.includes(state.step)) {
      // (a pair made again while Split view is on keeps following the wizard: only the first mount reads the step the planner picked last time)
      const picked = pickPair(state.step, S.mounted && S.auto ? "auto" : S.secondPref, okFor(state.step));
      if (!picked) { teardown(); chrome(); return; }
      S.pair = picked.pair;
      S.auto = picked.auto;
    }
    const wasMounted = S.mounted;
    S.mounted = true;
    S.active = state.step;
    // showing a step counts as reaching it (Continue would get there with one click anyway), so its pill works and the steps before it read as done
    state.furthestStep = Math.max(state.furthestStep, S.pair[1]);
    canvas().classList.add("split-on");
    document.body.classList.add("split-view-on");
    fixDomOrder(S.pair[0] === 5 && S.pair[1] === 6);
    paintPanes();
    if (!wasMounted || S.pair.some((n) => !S.shown.has(n))) {
      // a step that has just come on screen is drawn from the proposal as it is now (it may never have been drawn, or not for a while)
      S.pair.filter((n) => !S.shown.has(n) && n !== state.step).forEach((n) => redraw(n, []));
      if (!wasMounted && state.step === 2) { state.finalProposalTitle = null; updateProposalNameBar(); }   // as arriving on Step 02 does: the name may change here
    }
    S.shown = new Set(S.pair);
    paintPills();
    chrome();
    measure();
  }

  // ---- hooks called by app.js --------------------------------------------------------------------------------------------------------------
  window._splitAfterGoToStep = function (n) {
    if (S.mounted && S.on && n >= 2) S.pair = route(S.pair, S.active, S.auto, n) || [0, 0];
    apply();
  };

  window._splitPillClick = function (target) {
    if (!S.mounted || !S.pair.includes(target)) return false;
    activate(target);
    return true;
  };

  window._splitNotify = function (source, typing) {
    if (!S.mounted || S.refreshing) return;
    S.pending.push({ source: source == null ? state.step : source, typing: !!typing });
    clearTimeout(S.timer);
    S.timer = setTimeout(flush, FLUSH_MS);
  };

  // The other pane (and the dock) follow what has been edited.
  function flush() {
    S.timer = null;
    const batch = S.pending;
    S.pending = [];
    if (!S.mounted || !batch.length) return;
    S.pair.forEach((step) => {
      const reads = READS[step];
      if (!reads) return;
      const hits = batch.filter((b) => reads.includes(b.source) && b.source !== step);
      if (!hits.length) return;
      // Step 06 re-divides a line's months whenever it is drawn and a budget is changed from outside; drawing it at every pause in typing would do that for each
      // half-typed number (5, 50, 500 ...) and let cents drift. A typed budget reaches it when it is committed (the field's change event).
      if (step === 6 && hits.every((b) => b.typing)) return;
      const sources = hits.map((b) => b.source);
      // an estimate on its way into a Step 05 card keeps its "pulling" state: the pane is drawn once the answer is in
      if (step === 5 && typeof _constanzaBusy === "object" && Object.values(_constanzaBusy).some(Boolean)) {
        S.pending.push(...hits);
        clearTimeout(S.timer);
        S.timer = setTimeout(flush, FLUSH_MS * 3);
        return;
      }
      if (hasFocus(step)) {
        const held = S.deferred.get(step) || new Set();
        sources.forEach((s) => held.add(s));
        S.deferred.set(step, held);
        return;
      }
      redraw(step, sources);
    });
    if (batch.some((b) => b.source === 2 || b.source === 4 || b.source === 6)) refreshDock();
  }

  // The Constanza dock, when it is open for a line, judges its estimate against the line as it is now.
  function refreshDock() {
    try { if (window.Constanza && typeof Constanza.refreshLine === "function") Constanza.refreshLine(); } catch (e) { /* the dock is its own business */ }
  }

  // ---- the active pane ---------------------------------------------------------------------------------------------------------------------
  // Clicking in a pane (or tabbing into it) makes its step the one Back / Continue / Save Draft act on. goToStep() is NOT called: it draws Step 04
  // again and scrolls, which would rebuild the very row that was clicked.
  function activate(step) {
    if (!S.mounted || state.step === step || !S.pair.includes(step)) return;
    state.step = S.active = step;
    paintPanes();
    paintPills();
    chrome();
  }

  function edited(step, e, typing) {
    if (!S.mounted) return;
    // Step 06's figures only reach a budget when committed (blur), and that redraws Step 06, which tells the others itself: its keystrokes mean nothing yet.
    if (step === 6 && typing) return;
    // Step 02's fields are only copied into the proposal when the planner leaves the step; with another step beside it they must be there at once.
    if (step === 2 && e.target.closest && e.target.closest("[data-field]")) {
      if (!state.parsed) return;
      syncFormToParsed();
    }
    window._splitNotify(step, typing);
  }

  function leftPane(step, e) {
    const held = S.deferred.get(step);
    if (!S.mounted || !held) return;
    const sec = section(step);
    if (e.relatedTarget && sec.contains(e.relatedTarget)) return;
    setTimeout(() => {                  // after the field's own blur handler has stored what was typed
      if (!S.mounted || S.deferred.get(step) !== held || hasFocus(step)) return;
      S.deferred.delete(step);
      redraw(step, [...held]);
    }, 80);
  }

  // ---- wiring -----------------------------------------------------------------------------------------------------------------------------
  function init() {
    loadPref();
    for (let n = 2; n <= 8; n++) {
      const sec = section(n);
      if (!sec) continue;
      sec.addEventListener("pointerdown", () => activate(n), true);
      sec.addEventListener("focusin", () => activate(n));
      sec.addEventListener("input", (e) => edited(n, e, true));
      sec.addEventListener("change", (e) => edited(n, e, false));
      sec.addEventListener("focusout", (e) => leftPane(n, e));
    }
    $("split-toggle").addEventListener("click", () => {
      S.on = !S.mounted;
      savePref();
      apply();
    });
    $("split-second-select").addEventListener("change", (e) => {
      const v = e.target.value;
      const active = state.step;
      if (v === "auto") {
        S.secondPref = "auto";
        const picked = pickPair(active, "auto", okFor(active));
        if (picked) { S.pair = picked.pair; S.auto = true; }
      } else {
        S.secondPref = parseInt(v, 10);
        S.auto = false;
        S.pair = [active, S.secondPref].sort(BY_NUMBER);
      }
      savePref();
      apply();
    });
    const query = window.matchMedia ? window.matchMedia(`(min-width: ${MIN_WIDTH}px)`) : null;
    if (query) (query.addEventListener ? query.addEventListener("change", apply) : query.addListener(apply));
    window.addEventListener("resize", measure);
    if (typeof ResizeObserver === "function") {
      const ro = new ResizeObserver(measure);
      [document.querySelector(".masthead"), $("proposal-name-bar"), $("split-bar")].forEach((el) => el && ro.observe(el));
    }
    chrome();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();

  window.SplitView = {
    isOn: () => S.mounted,
    pair: () => S.pair.slice(),
    _state: S,
    _pure: { eligible, pickPair, route },
  };
})();
