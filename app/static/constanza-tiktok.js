/* ============================================================================
 * Constanza x TikTok - the TikTok panel of the avails dock.
 *
 * Choosing the TikTok ads product swaps the dock's Entravision audience block for the targeting TikTok Ads Manager offers when
 * you set up an ad group - age, gender, languages, interests & behaviors, spending power, household income, device, placement and
 * campaign objective - and the result card shows what TikTok's own audience-size estimator answers: how many people match, as a
 * range and TikTok's own verdict on how narrow that is. Impressions are the audience x 5.5 a month (the planning factor the server
 * applies); est. uniques is the audience itself.
 *
 * Loaded after constanza.js, which owns the dock: this file only registers a forecast method with it (registerMethod). The server
 * side is app/services/constanza_tiktok.py (/api/constanza/tiktok/*, and /api/constanza/estimate for the numbers).
 * ========================================================================== */
(function () {
  "use strict";
  if (!window.Constanza || typeof window.Constanza.registerMethod !== "function") return;

  window.Constanza.registerMethod("tiktok", function (ctx) {
    const { S, $, esc, num, compact, api, markStale, errorText } = ctx;

    // ---------------------------------------------------------------- what TikTok can target (the lists that come from TikTok are fetched on demand)
    const KINDS = {
      general_interest: "Interest", additional_interest: "Interest keyword", purchase_intention: "Purchase intent",
      video_interaction: "Video interaction", creator_interaction: "Creator interaction", hashtag_interaction: "Hashtag",
    };
    const KIND_ORDER = Object.keys(KINDS);
    const BROWSE_TABS = [["general_interest", "Interests"], ["purchase_intention", "Purchase intent"], ["video_interaction", "Video"], ["creator_interaction", "Creators"], ["hashtag_interaction", "Hashtags"]];
    const FALLBACK = {
      age_groups: [["AGE_18_24", "18–24"], ["AGE_25_34", "25–34"], ["AGE_35_44", "35–44"], ["AGE_45_54", "45–54"], ["AGE_55_100", "55+"]],
      objectives: [["reach", "Reach"], ["video_views", "Video views"], ["traffic", "Traffic"]],
      household_income: [["TOP5", "Top 5% of ZIP codes"], ["TOP10", "Top 10%"], ["TOP10_25", "Top 10–25%"], ["TOP25_50", "Top 25–50%"]],
      network_types: [["WIFI", "Wi-Fi"], ["4G", "4G"], ["5G", "5G"], ["3G", "3G"], ["2G", "2G"]],
      operating_systems: [["IOS", "iOS"], ["ANDROID", "Android"]],
      placements: [["PLACEMENT_TIKTOK", "TikTok"], ["PLACEMENT_PANGLE", "Pangle (TikTok’s partner apps)"]],
    };
    const STAGES = { 1: "Too narrow", 2: "Narrow", 3: "Balanced", 4: "Fairly broad" };
    const TYPE_LABEL = { COUNTRY: "country", PROVINCE: "state", DMA: "TV market", CITY: "county / city", DISTRICT: "city", ZIP_CODE: "ZIP" };

    // ---------------------------------------------------------------- state
    const fresh = () => ({
      age: [], gender: "", languages: [], picks: [], spending: "", income: [], os: "", networks: [], placements: ["PLACEMENT_TIKTOK"],
      objective: "reach", videoPeriod: 15,
    });
    const T = Object.assign(fresh(), { status: null, statusAt: 0, catalog: {}, languagesAll: null, searchSeq: 0, hits: [], active: -1, langHits: [], langActive: -1, tab: "general_interest" });
    let searchTimer = null;

    const options = (key) => ((T.status && T.status.options && T.status.options[key]) ? T.status.options[key].map((o) => [o.id, o.label]) : FALLBACK[key]);
    const labelOf = (key, id) => { const hit = options(key).find((o) => o[0] === id); return hit ? hit[1] : id; };
    const toggled = (arr, v) => (arr.includes(v) ? arr.filter((x) => x !== v) : arr.concat(v));
    const kindIds = (kind) => T.picks.filter((p) => p.kind === kind).map((p) => p.id);
    const interactionPicked = () => T.picks.some((p) => ["video_interaction", "creator_interaction", "hashtag_interaction"].includes(p.kind));

    // ---------------------------------------------------------------- the request
    function targeting() {
      return {
        gender: T.gender || null, age_groups: T.age.slice().sort(), languages: T.languages.map((l) => l.code),
        interest_category_ids: kindIds("general_interest"), interest_keyword_ids: kindIds("additional_interest"),
        purchase_intention_ids: kindIds("purchase_intention"), video_interaction_ids: kindIds("video_interaction"),
        creator_interaction_ids: kindIds("creator_interaction"), hashtag_ids: kindIds("hashtag_interaction"),
        video_period: T.videoPeriod, spending_power: T.spending || null, household_income: T.income.slice().sort(),
        operating_system: T.os || null, network_types: T.networks.slice().sort(), placements: T.placements.slice().sort(), objective: T.objective,
      };
    }

    // The plain-language lines of what was asked (also what the result card and the copied summary say).
    function summaryParts() {
      const parts = [];
      if (T.gender) parts.push(T.gender === "male" ? "Men" : "Women");
      if (T.age.length) parts.push("Ages " + T.age.slice().sort().map((a) => labelOf("age_groups", a)).join(", "));
      if (T.languages.length) parts.push("Languages: " + T.languages.map((l) => l.name).join(", "));
      KIND_ORDER.forEach((kind) => {
        const names = T.picks.filter((p) => p.kind === kind).map((p) => p.name);
        if (names.length) parts.push(`${KINDS[kind]}${names.length > 1 ? "s" : ""}: ${names.join("; ")}`);
      });
      if (T.picks.some((p) => p.kind === "video_interaction")) parts.push("Video look-back: " + (T.videoPeriod ? `${T.videoPeriod} days` : "any time"));
      if (T.spending === "HIGH") parts.push("High spending power");
      if (T.income.length) parts.push("Household income: " + T.income.slice().sort().map((i) => labelOf("household_income", i)).join(", "));
      if (T.os) parts.push(labelOf("operating_systems", T.os));
      if (T.networks.length) parts.push("Connection: " + T.networks.slice().sort().map((n) => labelOf("network_types", n)).join(", "));
      return parts;
    }

    function selectedCount() {
      return (T.gender ? 1 : 0) + (T.age.length ? 1 : 0) + (T.languages.length ? 1 : 0) + T.picks.length;
    }
    function moreCount() {
      return (T.spending ? 1 : 0) + (T.income.length ? 1 : 0) + (T.os ? 1 : 0) + (T.networks.length ? 1 : 0) + (T.placements.length > 1 ? 1 : 0) + (T.objective !== "reach" ? 1 : 0);
    }

    // ---------------------------------------------------------------- the panel
    function formHtml() {
      return `
<div class="cz-tt" id="cz-tt">
  <div id="cz-tt-status" class="cz-tt-status"></div>
  <details class="cz-details" id="cz-tt-aud" open>
    <summary><span>TikTok audience</span><span class="cz-count hidden" id="cz-tt-count">0</span><span class="cz-hint">age · gender · interests</span></summary>
    <div class="cz-field">
      <span class="cz-label"><span>Age</span><span class="cz-hint">18+ · TikTok leaves out under-18s</span></span>
      <div class="cz-seg" id="cz-tt-age" role="group" aria-label="Age brackets"></div>
    </div>
    <div class="cz-field">
      <span class="cz-label"><span>Gender</span></span>
      <div class="cz-seg" id="cz-tt-gender" role="group" aria-label="Gender">
        <button type="button" data-tt="gender" data-v="" aria-pressed="true">All</button>
        <button type="button" data-tt="gender" data-v="male" aria-pressed="false">Men</button>
        <button type="button" data-tt="gender" data-v="female" aria-pressed="false">Women</button>
      </div>
    </div>
    <div class="cz-field">
      <label class="cz-label" for="cz-tt-lang-q"><span>Languages</span><span class="cz-hint" id="cz-tt-lang-hint">any language</span></label>
      <input type="search" class="cz-input" id="cz-tt-lang-q" placeholder="Add a language — Spanish, English…" autocomplete="off"
             role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="cz-tt-lang-results" aria-haspopup="listbox" />
      <div class="cz-interest-results hidden" id="cz-tt-lang-results" role="listbox" aria-label="Matching languages"></div>
      <div class="cz-chips" id="cz-tt-lang-chips"></div>
    </div>
    <div class="cz-field" style="margin-bottom:0">
      <label class="cz-label" for="cz-tt-q"><span>Interests &amp; behaviors</span><span class="cz-hint" id="cz-tt-pick-hint"></span></label>
      <input type="search" class="cz-input" id="cz-tt-q" placeholder="Search — beauty, soccer, SUVs, #cooking…" autocomplete="off"
             role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="cz-tt-results" aria-haspopup="listbox" />
      <div class="cz-interest-results hidden" id="cz-tt-results" role="listbox" aria-label="Matching interests and behaviors"></div>
      <div class="cz-chips" id="cz-tt-chips"></div>
      <div id="cz-tt-pick-note"></div>
      <details class="cz-browse" id="cz-tt-browse">
        <summary>Browse every category TikTok offers</summary>
        <div class="cz-seg" id="cz-tt-tabs" role="group" aria-label="Kind of category">${BROWSE_TABS.map(([k, l]) => `<button type="button" data-tt="tab" data-v="${k}" aria-pressed="${k === "general_interest"}">${esc(l)}</button>`).join("")}</div>
        <input type="search" class="cz-input" id="cz-tt-filter" placeholder="Filter this list…" autocomplete="off" aria-label="Filter this list" style="margin-top:8px" />
        <div id="cz-tt-browse-body"><p class="cz-note">Loading…</p></div>
      </details>
    </div>
  </details>
  <details class="cz-details" id="cz-tt-more">
    <summary><span>More targeting</span><span class="cz-count hidden" id="cz-tt-more-count">0</span><span class="cz-hint">spending · income · device · placement</span></summary>
    <div class="cz-field">
      <span class="cz-label"><span>Spending power</span></span>
      <div class="cz-seg" id="cz-tt-spend" role="group" aria-label="Spending power">
        <button type="button" data-tt="spending" data-v="" aria-pressed="true">All</button>
        <button type="button" data-tt="spending" data-v="HIGH" aria-pressed="false">High spenders</button>
      </div>
    </div>
    <div class="cz-field">
      <span class="cz-label"><span>Household income</span><span class="cz-hint">U.S. · share of ZIP codes</span></span>
      <div class="cz-seg" id="cz-tt-income" role="group" aria-label="Household income"></div>
    </div>
    <div class="cz-field">
      <span class="cz-label"><span>Device</span></span>
      <div class="cz-seg" id="cz-tt-os" role="group" aria-label="Operating system"></div>
      <div class="cz-seg" id="cz-tt-net" role="group" aria-label="Connection type"></div>
    </div>
    <div class="cz-field">
      <span class="cz-label"><span>Placement</span></span>
      <div class="cz-seg" id="cz-tt-place" role="group" aria-label="Placements"></div>
      <p class="cz-note" id="cz-tt-place-note"></p>
    </div>
    <div class="cz-field" style="margin-bottom:0">
      <span class="cz-label"><span>Campaign objective</span><span class="cz-hint">decides which targeting TikTok offers</span></span>
      <div class="cz-seg" id="cz-tt-obj" role="group" aria-label="Campaign objective"></div>
      <div id="cz-tt-period" class="hidden">
        <span class="cz-label" style="margin-top:12px"><span>Video interactions look back</span></span>
        <div class="cz-seg" role="group" aria-label="Look-back period">
          <button type="button" data-tt="period" data-v="15" aria-pressed="true">15 days</button>
          <button type="button" data-tt="period" data-v="7" aria-pressed="false">7 days</button>
          <button type="button" data-tt="period" data-v="0" aria-pressed="false">Any time</button>
        </div>
      </div>
    </div>
  </details>
</div>`;
    }

    const seg = (attr, items, isOn, extra) => items.map(([id, label]) => `<button type="button" data-tt="${attr}" data-v="${esc(id)}" aria-pressed="${isOn(id)}"${extra ? " " + extra(id) : ""}>${esc(label)}</button>`).join("");

    function renderStatus() {
      const el = $("cz-tt-status");
      if (!el) return;
      const s = T.status;
      if (!s) { el.className = "cz-tt-status"; el.innerHTML = `<span class="cz-note">Checking the TikTok connection…</span>`; return; }
      if (s.error) { el.className = "cz-tt-status cz-tt-off"; el.innerHTML = `<strong>Couldn’t reach the app’s TikTok settings.</strong> ${esc(s.error)}`; return; }
      if (!s.configured) {
        el.className = "cz-tt-status cz-tt-off";
        el.innerHTML = `<strong>TikTok isn’t connected yet.</strong> An admin needs to add ${s.missing.map((m) => `<code>${esc(m)}</code>`).join(" and ")} to the app’s secrets, then test it in Admin → Forecast. You can set the audience up now; the estimate needs the connection.`;
        return;
      }
      el.className = "cz-tt-status cz-tt-on";
      el.innerHTML = `<span class="cz-dot"></span> Connected to TikTok · ad account ${esc(s.advertiser || "")} · impressions = audience × ${esc(String(s.impressions_per_person))} a month`;
    }

    // The buttons of a group are built once (and again only when TikTok's menus arrive); after that a change only flips their state in
    // place, so the keyboard focus stays on the button that was pressed.
    let builtFor = "";
    function buildChoices() {
      const sig = JSON.stringify([options("age_groups"), options("household_income"), options("operating_systems"), options("network_types"), options("placements"), options("objectives")]);
      if (sig === builtFor) return;
      builtFor = sig;
      const off = () => false;
      $("cz-tt-age").innerHTML = seg("age", options("age_groups"), off);
      $("cz-tt-income").innerHTML = seg("income", options("household_income"), off);
      $("cz-tt-os").innerHTML = seg("os", [["", "Any device"]].concat(options("operating_systems")), off);
      $("cz-tt-net").innerHTML = seg("network", options("network_types"), off);
      $("cz-tt-place").innerHTML = seg("place", options("placements"), off);
      $("cz-tt-obj").innerHTML = seg("objective", options("objectives"), off);
    }

    const press = (el, on) => el.querySelectorAll("button[data-v]").forEach((b) => b.setAttribute("aria-pressed", String(on(b.dataset.v))));

    function syncChoices() {
      press($("cz-tt-age"), (v) => T.age.includes(v));
      press($("cz-tt-gender"), (v) => v === (T.gender || ""));
      press($("cz-tt-spend"), (v) => v === (T.spending || ""));
      press($("cz-tt-income"), (v) => T.income.includes(v));
      press($("cz-tt-os"), (v) => v === (T.os || ""));
      press($("cz-tt-net"), (v) => T.networks.includes(v));
      press($("cz-tt-place"), (v) => T.placements.includes(v));
      press($("cz-tt-obj"), (v) => T.objective === v);
      press($("cz-tt-period"), (v) => Number(v) === T.videoPeriod);
      const locked = interactionPicked();
      $("cz-tt-place").querySelectorAll('button[data-v="PLACEMENT_PANGLE"]').forEach((b) => { b.disabled = locked; });
      $("cz-tt-place-note").textContent = locked ? "Video, creator and hashtag interactions work only when TikTok is the only placement." : "TikTok alone is what the Tiktok Ads product runs on; add Pangle to see the audience including TikTok’s partner apps.";
      $("cz-tt-period").classList.toggle("hidden", !T.picks.some((p) => p.kind === "video_interaction"));
    }

    // The Browse chips show which categories are picked: flipped in place too, whichever way a pick was added or removed.
    function syncBrowse() {
      $("cz-tt-browse-body").querySelectorAll('button[data-tt="browse-add"]').forEach((b) => {
        const on = T.picks.some((p) => p.kind === T.tab && p.id === b.dataset.id);
        b.setAttribute("aria-pressed", String(on));
        b.classList.toggle("cz-ok", on);
      });
    }

    function renderPicks() {
      $("cz-tt-chips").innerHTML = T.picks.map((p, i) =>
        `<span class="cz-chip cz-ok" title="${esc(p.path || KINDS[p.kind])}">${esc(p.name)} <small>${esc(KINDS[p.kind])}</small><button type="button" class="cz-x" data-tt="rm-pick" data-i="${i}" aria-label="Remove ${esc(p.name)}">×</button></span>`).join("");
      $("cz-tt-pick-hint").textContent = T.picks.length ? `${T.picks.length} chosen` : "";
      const notes = [];
      if (T.picks.some((p) => p.kind === "purchase_intention") && T.objective !== "traffic") {
        notes.push(`<p class="cz-note cz-warn">Purchase intent isn’t available for a ${esc(labelOf("objectives", T.objective))} campaign. <button type="button" class="cz-link" data-tt="fix-objective">Use the Traffic objective</button></p>`);
      }
      if (T.picks.some((p) => p.kind === "purchase_intention") && T.picks.some((p) => p.kind === "additional_interest")) {
        notes.push(`<p class="cz-note cz-warn">Purchase intent and interest keywords can’t be used together — TikTok treats them as conflicting. Remove one.</p>`);
      }
      if (T.picks.filter((p) => p.kind === "general_interest").length > 1) notes.push(`<p class="cz-note">Several interests widen the audience: TikTok reaches people who match <em>any</em> of them.</p>`);
      $("cz-tt-pick-note").innerHTML = notes.join("");
    }

    function renderLanguages() {
      $("cz-tt-lang-chips").innerHTML = T.languages.map((l, i) =>
        `<span class="cz-chip cz-ok">${esc(l.name)} <small>${esc(l.code)}</small><button type="button" class="cz-x" data-tt="rm-lang" data-i="${i}" aria-label="Remove ${esc(l.name)}">×</button></span>`).join("");
      $("cz-tt-lang-hint").textContent = T.languages.length ? `${T.languages.length} chosen` : "any language";
    }

    function updateCounts() {
      [["cz-tt-count", selectedCount()], ["cz-tt-more-count", moreCount()]].forEach(([id, n]) => {
        const el = $(id);
        if (!el) return;
        el.textContent = n;
        el.classList.toggle("hidden", !n);
      });
    }

    function renderAll() {
      renderStatus(); buildChoices(); syncChoices(); renderPicks(); renderLanguages(); updateCounts();
    }

    function changed() {
      // an interaction (video, creator, hashtag) works only when TikTok is the ONLY placement: whatever picked it, the lock holds
      if (interactionPicked() && !(T.placements.length === 1 && T.placements[0] === "PLACEMENT_TIKTOK")) T.placements = ["PLACEMENT_TIKTOK"];
      syncChoices(); renderPicks(); updateCounts(); syncBrowse();
      markStale();
    }

    // ---------------------------------------------------------------- the connection
    async function loadStatus(force) {
      if (!force && T.status && !T.status.error && Date.now() - T.statusAt < 30000) return;
      try {
        const r = await api("/api/constanza/tiktok/status");
        T.status = r.ok ? r.data : { error: errorText(r.data, "The status couldn’t be read."), options: null };
      } catch (e) {
        T.status = { error: String(e), options: null };
      }
      T.statusAt = Date.now();
      renderAll();
    }

    // ---------------------------------------------------------------- the categories: search and browse
    // The state change alone (no page): false when the pick is already there or the kind is full.
    function pushPick(kind, item) {
      if (T.picks.some((p) => p.kind === kind && p.id === String(item.id))) return false;
      if (kindIds(kind).length >= 50) return false;
      T.picks.push({ kind, id: String(item.id), name: item.name, path: item.path || "" });
      return true;
    }

    function addPick(kind, item) {
      if (!pushPick(kind, item)) {
        if (kindIds(kind).length >= 50) $("cz-tt-pick-note").innerHTML = `<p class="cz-note cz-warn">At most 50 of one kind.</p>`;
        return;
      }
      $("cz-tt-q").value = "";
      hideHits();
      changed();
    }

    function hideHits() {
      $("cz-tt-results").classList.add("hidden");
      $("cz-tt-q").setAttribute("aria-expanded", "false");
      $("cz-tt-q").removeAttribute("aria-activedescendant");
      T.hits = []; T.active = -1;
    }

    function markHit() {
      $("cz-tt-results").querySelectorAll(".cz-interest-item").forEach((el, i) => el.classList.toggle("cz-active", i === T.active));
      const input = $("cz-tt-q");
      if (T.active >= 0 && T.hits[T.active]) input.setAttribute("aria-activedescendant", `cz-tt-hit-${T.active}`); else input.removeAttribute("aria-activedescendant");
    }

    async function searchPicks() {
      const q = $("cz-tt-q").value.trim();
      if (q.length < 2) { hideHits(); return; }
      const seq = ++T.searchSeq;
      let r;
      try { r = await api(`/api/constanza/tiktok/search?q=${encodeURIComponent(q)}`); } catch (e) { r = { ok: false, data: {} }; }
      if (seq !== T.searchSeq || $("cz-tt-q").value.trim() !== q) return;          // a newer keystroke superseded this answer
      const box = $("cz-tt-results");
      box.classList.remove("hidden");
      $("cz-tt-q").setAttribute("aria-expanded", "true");
      if (!r.ok) {
        T.hits = []; T.active = -1;
        box.innerHTML = `<div class="cz-empty">${esc(errorText(r.data, "The category search isn’t available right now."))}</div>`;
        return;
      }
      const found = r.data.results || {};
      T.hits = [];
      KIND_ORDER.forEach((kind) => (found[kind] || []).slice(0, 6).forEach((item) => T.hits.push({ kind, item })));        // six of each kind: all of them show
      T.active = T.hits.length ? 0 : -1;
      box.innerHTML = T.hits.length
        ? T.hits.map((h, i) => `<div class="cz-interest-item ${i === T.active ? "cz-active" : ""}" role="option" id="cz-tt-hit-${i}" data-i="${i}"><span>${esc(h.item.name)}${h.item.path ? ` <span class="cz-grp">${esc(h.item.path)}</span>` : ""}</span><span class="cz-rate">${esc(KINDS[h.kind])}</span></div>`).join("")
        : `<div class="cz-empty">Nothing in TikTok’s categories matches “${esc(q)}”. Try a simpler word, or browse the list below.</div>`;
      markHit();
    }

    async function loadCatalog(kind) {
      if (T.catalog[kind]) return T.catalog[kind];
      let r;
      try { r = await api(`/api/constanza/tiktok/targeting?kind=${encodeURIComponent(kind)}`); } catch (e) { r = { ok: false, data: {} }; }
      if (!r.ok) return { error: errorText(r.data, "TikTok’s list isn’t available right now.") };
      const items = r.data.items || [];
      if (items.length) T.catalog[kind] = items;                    // a failure, or an empty answer, is not remembered: the next open tries again
      return items;
    }

    async function renderBrowse() {
      const body = $("cz-tt-browse-body");
      const kind = T.tab;
      body.innerHTML = `<p class="cz-note">Loading…</p>`;
      const items = await loadCatalog(kind);
      if (kind !== T.tab) return;
      if (items.error) { body.innerHTML = `<p class="cz-note cz-warn">${esc(items.error)}</p>`; return; }
      const words = $("cz-tt-filter").value.trim().toLowerCase().split(/\s+/).filter(Boolean);
      const match = (i) => !words.length || words.every((w) => `${i.name} ${i.path || ""}`.toLowerCase().includes(w));
      const shown = items.filter(match);
      const cap = words.length ? 120 : 80;
      const chips = shown.slice(0, cap).map((i) => {
        const on = T.picks.some((p) => p.kind === kind && p.id === String(i.id));
        return `<button type="button" class="cz-chip ${on ? "cz-ok" : ""}" data-tt="browse-add" data-id="${esc(i.id)}" title="${esc(i.path || "")}" aria-pressed="${on}">${esc(i.name)}${i.path ? ` <small>${esc(i.path.split(" › ")[0])}</small>` : ""}</button>`;
      }).join("");
      body.innerHTML = shown.length
        ? `<div class="cz-chips">${chips}</div>${shown.length > cap ? `<p class="cz-note">${shown.length - cap} more — type in the filter to narrow the list.</p>` : ""}`
        : `<p class="cz-note">Nothing matches that filter.</p>`;
    }

    // ---------------------------------------------------------------- languages
    function hideLangHits() {
      $("cz-tt-lang-results").classList.add("hidden");
      $("cz-tt-lang-q").setAttribute("aria-expanded", "false");
      $("cz-tt-lang-q").removeAttribute("aria-activedescendant");
      T.langHits = []; T.langActive = -1;
    }

    function markLang() {
      $("cz-tt-lang-results").querySelectorAll(".cz-interest-item").forEach((el, i) => el.classList.toggle("cz-active", i === T.langActive));
      const input = $("cz-tt-lang-q");
      if (T.langActive >= 0 && T.langHits[T.langActive]) input.setAttribute("aria-activedescendant", `cz-tt-lang-${T.langActive}`); else input.removeAttribute("aria-activedescendant");
    }

    async function searchLanguages() {
      const q = $("cz-tt-lang-q").value.trim().toLowerCase();
      if (!T.languagesAll) {
        let r;
        try { r = await api("/api/constanza/tiktok/targeting?kind=languages"); } catch (e) { r = { ok: false, data: {} }; }
        if (!r.ok) {
          const box = $("cz-tt-lang-results");
          box.classList.remove("hidden");
          $("cz-tt-lang-q").setAttribute("aria-expanded", "true");
          box.innerHTML = `<div class="cz-empty">${esc(errorText(r.data, "TikTok’s language list isn’t available right now."))}</div>`;
          T.langHits = []; T.langActive = -1;
          return;
        }
        T.languagesAll = r.data.items || [];
      }
      if ($("cz-tt-lang-q").value.trim().toLowerCase() !== q) return;
      T.langHits = T.languagesAll.filter((l) => !T.languages.some((x) => x.code === l.code) && (!q || l.name.toLowerCase().includes(q) || l.code.toLowerCase() === q)).slice(0, 12);
      T.langActive = T.langHits.length ? 0 : -1;
      const box = $("cz-tt-lang-results");
      box.classList.remove("hidden");
      $("cz-tt-lang-q").setAttribute("aria-expanded", "true");
      box.innerHTML = T.langHits.length
        ? T.langHits.map((l, i) => `<div class="cz-interest-item ${i === T.langActive ? "cz-active" : ""}" role="option" id="cz-tt-lang-${i}" data-i="${i}"><span>${esc(l.name)}</span><span class="cz-rate">${esc(l.code)}</span></div>`).join("")
        : `<div class="cz-empty">No language matches “${esc(q)}”.</div>`;
      markLang();
    }

    function addLanguage(l) {
      if (T.languages.some((x) => x.code === l.code)) return;
      if (T.languages.length >= 20) return;
      T.languages.push({ code: l.code, name: l.name });
      $("cz-tt-lang-q").value = "";
      hideLangHits();
      renderLanguages(); updateCounts(); markStale();
    }

    // ---------------------------------------------------------------- wiring
    function bind() {
      const root = $("cz-tt");
      renderAll();
      root.addEventListener("click", (e) => {
        const b = e.target.closest("[data-tt]");
        if (!b || !root.contains(b)) return;
        const v = b.dataset.v;
        switch (b.dataset.tt) {
          case "age": T.age = toggled(T.age, v); break;
          case "gender": T.gender = v; break;
          case "spending": T.spending = v; break;
          case "income": T.income = toggled(T.income, v); break;
          case "os": T.os = v; break;
          case "network": T.networks = toggled(T.networks, v); break;
          case "place":
            if (v === "PLACEMENT_TIKTOK" || b.disabled) return;      // TikTok is always a placement: it is what the product runs on
            T.placements = toggled(T.placements, v);
            break;
          case "objective": T.objective = v; break;
          case "period": T.videoPeriod = Number(v); break;
          case "fix-objective": T.objective = "traffic"; break;
          case "rm-pick": T.picks.splice(Number(b.dataset.i), 1); break;
          case "rm-lang": T.languages.splice(Number(b.dataset.i), 1); renderLanguages(); break;
          case "tab":
            T.tab = v;
            $("cz-tt-tabs").querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.v === v)));
            $("cz-tt-filter").value = "";
            renderBrowse();
            return;
          case "browse-add": {
            const item = (T.catalog[T.tab] || []).find((i) => String(i.id) === b.dataset.id);
            if (!item) return;
            if (T.picks.some((p) => p.kind === T.tab && p.id === String(item.id))) T.picks = T.picks.filter((p) => !(p.kind === T.tab && p.id === String(item.id)));
            else pushPick(T.tab, item);
            break;                                                    // the tail below redraws the chips and flips the Browse chips in place
          }
          default: return;
        }
        changed();
      });

      // categories search
      $("cz-tt-q").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(searchPicks, 220); });
      $("cz-tt-q").addEventListener("keydown", (e) => {
        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
          if (!T.hits.length) return;
          e.preventDefault();
          T.active = (T.active + (e.key === "ArrowDown" ? 1 : -1) + T.hits.length) % T.hits.length;
          markHit();
        } else if (e.key === "Enter") {
          e.preventDefault();
          if (T.hits[T.active]) addPick(T.hits[T.active].kind, T.hits[T.active].item);
        } else if (e.key === "Escape" && !$("cz-tt-results").classList.contains("hidden")) { e.preventDefault(); e.stopPropagation(); hideHits(); }
      });
      $("cz-tt-results").addEventListener("click", (e) => {
        const el = e.target.closest(".cz-interest-item");
        if (el && T.hits[Number(el.dataset.i)]) { const h = T.hits[Number(el.dataset.i)]; addPick(h.kind, h.item); }
      });
      $("cz-tt-browse").addEventListener("toggle", (e) => { if (e.target.open) renderBrowse(); });
      $("cz-tt-filter").addEventListener("input", renderBrowse);

      // languages
      $("cz-tt-lang-q").addEventListener("focus", searchLanguages);
      $("cz-tt-lang-q").addEventListener("input", searchLanguages);
      $("cz-tt-lang-q").addEventListener("keydown", (e) => {
        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
          if (!T.langHits.length) return;
          e.preventDefault();
          T.langActive = (T.langActive + (e.key === "ArrowDown" ? 1 : -1) + T.langHits.length) % T.langHits.length;
          markLang();
        } else if (e.key === "Enter") {
          e.preventDefault();
          if (T.langHits[T.langActive]) addLanguage(T.langHits[T.langActive]);
        } else if (e.key === "Escape" && !$("cz-tt-lang-results").classList.contains("hidden")) { e.preventDefault(); e.stopPropagation(); hideLangHits(); }
      });
      $("cz-tt-lang-results").addEventListener("click", (e) => {
        const el = e.target.closest(".cz-interest-item");
        if (el && T.langHits[Number(el.dataset.i)]) addLanguage(T.langHits[Number(el.dataset.i)]);
      });
      document.addEventListener("mousedown", (e) => {
        if (!e.target.closest("#cz-tt-q, #cz-tt-results")) hideHits();
        if (!e.target.closest("#cz-tt-lang-q, #cz-tt-lang-results")) hideLangHits();
      });
    }

    function reset() {
      Object.assign(T, fresh(), { hits: [], active: -1, langHits: [], langActive: -1, tab: "general_interest" });
      if (!$("cz-tt")) return;
      ["cz-tt-q", "cz-tt-lang-q", "cz-tt-filter"].forEach((id) => { if ($(id)) $(id).value = ""; });
      hideHits(); hideLangHits();
      $("cz-tt-tabs").querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.v === T.tab)));
      renderAll();
      if ($("cz-tt-browse").open) renderBrowse();
    }

    // ---------------------------------------------------------------- the result
    const money = (n) => num(n);

    function headlineHtml(data, m) {
      const tt = data.tiktok, a = tt.audience;
      const per = tt.impressions_per_person;
      const flight = m.months > 1 ? `<div class="cz-metric"><span class="cz-mlabel">Flight total · ${m.months} mo</span><span class="cz-mvalue">${money(m.total.mid)}</span><span class="cz-mrange">${compact(m.total.low)} – ${compact(m.total.high)}</span></div>` : "";
      return `<div class="cz-headline">
        <div class="cz-metric main"><span class="cz-mlabel">Audience size · TikTok accounts, 18+</span><span class="cz-mvalue">${money(a.mid)}</span><span class="cz-mrange">${compact(a.low)} – ${compact(a.high)} range</span></div>
        <div class="cz-metric"><span class="cz-mlabel">Impressions · per month</span><span class="cz-mvalue">${money(m.month.mid)}</span><span class="cz-mrange">${compact(m.month.low)} – ${compact(m.month.high)} · audience × ${esc(String(per))}</span></div>
        ${flight}
      </div>
      ${stageHtml(tt)}`;
    }

    function stageHtml(tt) {
      if (!tt.stage) return "";
      const pills = [1, 2, 3, 4].map((s) => `<span class="cz-stage ${tt.stage === s ? "on s" + s : ""}">${STAGES[s]}</span>`).join("");
      return `<div class="cz-stagebar" role="img" aria-label="TikTok’s verdict on this audience: ${esc(tt.stage_label || "")}">${pills}</div>
        <p class="cz-note">TikTok: <strong>${esc(tt.stage_label || "")}</strong> — ${esc(tt.stage_note || "")}.</p>`;
    }

    function bodyHtml(data, m, asked) {
      const tt = data.tiktok;
      const parts = (asked && asked.method && asked.method.summary) || [];
      const asks = parts.concat([`Objective: ${tt.objective}`, `Placement: ${(tt.placements || []).map((p) => (p === "PLACEMENT_PANGLE" ? "Pangle" : "TikTok")).join(" + ")}`]);
      const where = (tt.locations || []).map((l) => `<span class="cz-chip cz-ok" title="TikTok ID ${esc(l.id)}">${esc(l.name)} <small>${esc(TYPE_LABEL[l.geo_type] || l.geo_type)}</small></span>`).join("");
      return `<div class="cz-section-title">What was asked of TikTok</div>
        <div class="cz-chips">${asks.map((p) => `<span class="cz-chip">${esc(p)}</span>`).join("")}</div>
        <div class="cz-section-title">Places sent to TikTok</div>
        <div class="cz-chips">${where || `<span class="cz-note">none</span>`}</div>`;
    }

    function aboutHtml(data) {
      const tt = data.tiktok;
      const items = [
        `Asked of TikTok’s audience-size estimator (the one behind Ads Manager’s audience panel)${tt.request_id ? `; request ${esc(tt.request_id)}` : ""}${tt.cached ? "; answered from the last ten minutes, not asked again" : ""}.`,
        `The audience is TikTok’s own count of the accounts that match; it leaves out under-18s. Impressions are that audience × ${esc(String(tt.impressions_per_person))} a month — Entravision’s planning factor, not a delivery forecast.`,
        `The Avails step takes the monthly impressions, and the audience as est. uniques, so its frequency reads ${esc(String(tt.impressions_per_person))}.`,
      ];
      return `<details class="cz-details"><summary>About this estimate</summary><ul class="cz-list">${items.map((i) => `<li>${i}</li>`).join("")}</ul></details>`;
    }

    // The Step 05 per-line card: the same facts, in the table the other products use.
    function cardHtml(data) {
      const tt = data.tiktok, a = tt.audience;
      return `<p class="constanza-uniques">Audience size (TikTok accounts, 18+): <strong>${num(a.mid)}</strong> <small>(${num(a.low)} – ${num(a.high)})</small>${tt.stage_label ? ` · TikTok: ${esc(tt.stage_label)}` : ""}</p>
        <p class="cz-note">Impressions are the audience × ${esc(String(tt.impressions_per_person))} a month. This is the geography alone — <strong>Refine in Constanza</strong> to set age, gender and interests.</p>`;
    }

    function summaryLines(data, asked) {
      const tt = data.tiktok, a = tt.audience;
      const lines = [`Audience size (TikTok accounts, 18+): ${num(a.mid)} (${num(a.low)} – ${num(a.high)})${tt.stage_label ? `; TikTok says: ${tt.stage_label}` : ""}`,
        `Impressions = audience × ${tt.impressions_per_person} per month`];
      const parts = (asked && asked.method && asked.method.summary) || [];
      if (parts.length) lines.push(`Asked of TikTok: ${parts.join(" · ")}`);
      if ((tt.locations || []).length) lines.push(`Places sent to TikTok: ${tt.locations.map((l) => l.name).join("; ")}`);
      return lines;
    }

    // Nothing is refused here: the server says "TikTok isn't connected yet" itself, and its answer is always current, where a status read
    // when the dock opened is not (an admin may have added the secrets since).
    function canPull() {
      return { ok: true };
    }

    function onError(status) {
      if (status === 503) loadStatus(true);                       // the connection changed under us: show it
    }

    return {
      goLabel: "Estimate audience →",
      formHtml, bind, reset, canPull, onError,
      onSelect: () => { renderAll(); loadStatus(false); },
      requestPart: () => ({ tiktok: targeting() }),
      context: () => ({ summary: summaryParts() }),
      headlineHtml, bodyHtml, aboutHtml, cardHtml, summaryLines,
      _pure: { targeting, summaryParts, stageHtml, headlineHtml, bodyHtml, aboutHtml, cardHtml, summaryLines, canPull, T, pushPick, selectedCount, moreCount, toggled, formHtml },
    };
  });
})();
