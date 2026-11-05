/* ============================================================================
 * SegmentBrowser - the shared "browse every segment" pop-up: a searchable, sortable, multi-select list in an accessible modal dialog.
 * Used by the Meta panel (Step 05 avails cards), the TikTok panel and the Entravision audience block in the Constanza dock, so all
 * three open the same window. Dependency-free; styled by segment-browser.css (every selector there is .segb-*). Loaded before app.js.
 *
 *   const handle = SegmentBrowser.open({
 *     title: "Browse interests", subtitle: "Pick as many as you like",
 *     tabs: [{ key: "general_interest", label: "Interests" }, { key: "hashtag_interaction", label: "Hashtags" }],   // optional
 *     activeTab: "general_interest",
 *     load: (tab) => fetch(...).then((r) => r.json()).then((d) => d.items),      // Promise<Item[] | { error }>
 *     columns: [{ key: "name", label: "Name" }, { key: "group", label: "Category" }, { key: "size", label: "Audience size", numeric: true }],
 *     isChosen: (tab, item) => picks.some((p) => p.kind === tab && p.id === item.id),
 *     max: 20, applyLabel: "Add to audience",
 *     onApply: ({ added, removed }) => { added.forEach(({ tab, item }) => addPick(tab, item)); removed.forEach(({ tab, item }) => dropPick(tab, item)); },
 *   });                                                                         // handle.close() closes it (discarding)
 *   SegmentBrowser.close();  SegmentBrowser.isOpen();                           // the same, without a handle
 *
 * opts (all optional except title, load, onApply):
 *   title, subtitle     heading text (plain text, never HTML).
 *   tabs, activeTab     [{ key, label }]; omit for ONE list (key "all", no tab bar). With tabs a role=tablist bar shows each tab's item
 *                       count once that tab has loaded; Left/Right/Home/End move between tabs.
 *   load(tabKey)        called lazily, ONCE per tab (the dialog caches what it returns), possibly synchronously. Return/resolve an array of
 *                       Items, or { error: "plain-language message" }; a rejected promise or a throw shows its message too. Either way
 *                       the dialog shows the message with a Retry button, and Retry calls load(tabKey) again.
 *   columns             [{ key, label, numeric?, format?(value, item) -> string, value?(item), sortable? }]. Default: Name (item.name) and
 *                       Category (item.group, else item.path joined with " › "). The cell shows format(value, item); value defaults to
 *                       item[key] and is what the column sorts and aligns by. key "size" is the conventional audience-size column: it
 *                       shows Item.size (the LOWER bound, e.g. "1.2M") or "size – sizeUpper" when sizeUpper is larger, and "—" when the
 *                       size is null/undefined. A numeric column is right-aligned and its first click sorts biggest first.
 *   isChosen(tab, item) which items the caller already holds; they open ticked. Asked once per item, when its tab loads.
 *   max                 the most items the caller can hold IN TOTAL, counting what it already holds in every tab. At the limit the
 *                       unticked rows are disabled and a plain-language note says so; unticking something frees a slot.
 *   maxPerTab           the most items ONE tab may hold in total: what the caller already holds there (chosenCount(tab), counted while the
 *                       tab's list has not loaded) plus the ticks. At that number the unticked rows OF THAT TAB are disabled and a note says
 *                       so; every other tab stays usable. It composes with `max`: set both and a row is disabled when EITHER limit is
 *                       reached (the global note wins when both are). Without tabs it is simply a second name for `max`.
 *   maxNote(tab)        optional: the note shown at the per-tab limit, plain text ("TikTok allows at most 50 of one kind."). Default:
 *                       "You’ve reached the limit of 50 for Interests. Untick one to add another."
 *   chosenCount(tab)    optional: how many items the caller holds in that tab. Lets `max` and `maxPerTab` count tabs that have not loaded
 *                       yet, and picks the list does not contain (a ZIP typed by hand); also tells Clear which unloaded tabs hold picks.
 *                       Leave it out when there is no limit and only one tab.
 *   applyLabel          primary button text. Default "Add selected" (the button then reads "Add 3 selected", "Remove 2" or "Add 3,
 *                       remove 2"). A custom label is for ADDING: it shows the number of changes after it, e.g. "Add to audience (3)",
 *                       only while nothing is being removed. Once something is unticked the button says what it does whatever the label:
 *                       "Remove 2" (removals only) or "Apply changes (3)" (a mix). Disabled at zero changes.
 *   onApply({ added, removed })
 *                       called ONCE, after the dialog has closed (so you can move focus): `added` = [{ tab, item }] the user ticked, in the
 *                       order they ticked them; `removed` = [{ tab, item }] they unticked. Only the CHANGES against isChosen, and the very
 *                       item objects load() returned (extra fields untouched).
 *   returnFocus         element that gets the focus back on close (default: whatever had it when the dialog opened).
 *   noun, nounSingular  what the items are called, plural, for the counts ("3,000 interests"); default "segments". The singular is
 *                       worked out ("1 interest") unless you pass nounSingular.
 *   searchPlaceholder, emptyText   texts for the search box and for a list that came back empty from the server.
 *
 * Item = { id: string, name: string, group?, path?: string | string[], size?: number, sizeUpper?: number, description?, ...extras }.
 * Ids must be unique within a tab (a repeated id is dropped); the same id may appear in two tabs.
 *
 * Behaviour: role=dialog aria-modal; focus moves in (the search box; the dialog itself on a phone, so the keyboard stays down), Tab is
 * trapped, focus goes back to the opener, the page cannot scroll behind it (restored exactly), only one dialog at a time (opening another
 * closes the first). Below 640px wide it is a full-screen sheet.
 * Closing: Cancel is the explicit "throw it away". Esc and the X close too, but never silently. Esc in a search box that has text clears the
 * text first. With nothing ticked or unticked, Esc / the X just close. With changes pending the first Esc (or the X) raises "Close without
 * applying your 3 changes?" (Keep picking / Discard and close), and a second Esc discards. A click on the backdrop closes only when nothing is
 * ticked. The second click of a double-click on the button that opened the dialog is ignored, so it neither closes the dialog nor ticks the
 * row under the pointer (a double-click that starts inside the dialog is not touched).
 * Search (debounced 120 ms) needs every typed word to appear in name + group + path + description + the text of every non-numeric column,
 * ignoring case and accents ("espanol" finds "Español"; a "TikTok code" column is searchable, an audience-size column is not). Click a
 * header button to sort (aria-sort on its th); the sort and the search survive tab switches. Rows draw 100 at a time ("Show 100 more", or
 * just scroll), so a 10,000-row list stays instant; cells wrap at spaces and break a word only when it cannot fit the sheet.
 * Selecting: the header checkbox ticks everything matching the search, up to the limits. "Clear" unticks everything, including what the
 * caller already holds (Apply then reports those as removed): tabs that have not loaded yet are loaded first so their picks go too (the
 * ones chosenCount says hold something; every unloaded tab when there is no chosenCount), and Apply waits for that. A row is focusable:
 * Space/Enter ticks it, Up/Down/Home/End move. Dragging across a row's text (to copy a name) selects it without ticking the row.
 *
 * Hooks for tests (stable): .segb-dialog, .segb-search-input, .segb-tab, .segb-sort, .segb-row[data-id][aria-selected], .segb-all,
 * .segb-more, .segb-retry, .segb-clear, .segb-search-clear, .segb-apply, .segb-cancel, .segb-count, .segb-state, .segb-limit, .segb-discard,
 * .segb-keep, .segb-discard-btn, .segb-live. SegmentBrowser._pure holds the pure helpers.
 * ========================================================================== */
(function () {
  "use strict";

  const CHUNK = 100;                       // rows drawn at a time
  const SEARCH_DELAY_MS = 120;
  const LOAD_AHEAD_PX = 280;               // draw the next chunk when the list is this close to its end
  const DEFAULT_APPLY = "Add selected";
  const LOAD_FAILED = "This list couldn’t be loaded. Please try again.";
  const PHONE_QUERY = "(max-width: 640px)";
  const OPEN_GUARD_MS = 700;               // a repeat click this soon after opening, whose first click was not in the dialog, is the opener's
  const SAY_DELAY_MS = 40;                 // the live region is emptied, then filled this much later, so a repeated message is read out again
  const ECHO_MAX = 40;                     // characters of the typed search echoed back in "Nothing matches ..."

  // ---------------------------------------------------------------- pure helpers (no DOM; tests/test_segment_browser_frontend.py runs them)

  const HAS_NORMALIZE = typeof "".normalize === "function";

  // Lower-case with the accents taken off, so "Español" and "espanol" are the same word to the search and to the sort.
  function fold(value) {
    let s = value == null ? "" : String(value);
    if (HAS_NORMALIZE) s = s.normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    return s.toLowerCase();
  }

  // "Age 25-34" -> ["age ", 25, "-", 34]: text and numbers in turn, so numbers inside names compare as numbers (2 before 10).
  function naturalKey(value) {
    const s = fold(value).replace(/\s+/g, " ").trim();
    if (!s) return [];
    return s.split(/(\d+)/).filter((t) => t !== "").map((t) => (/^\d/.test(t) ? Number(t) : t));
  }

  // <0, 0, >0 like Array.sort wants. A number sorts before text; equal-looking keys are 0 (the caller's index makes the sort stable).
  function compareKeys(a, b) {
    const n = Math.min(a.length, b.length);
    for (let i = 0; i < n; i++) {
      const x = a[i], y = b[i];
      if (x === y) continue;
      const xNum = typeof x === "number", yNum = typeof y === "number";
      if (xNum !== yNum) return xNum ? -1 : 1;
      return x < y ? -1 : 1;
    }
    return a.length - b.length;
  }

  function compareNatural(a, b) { return compareKeys(naturalKey(a), naturalKey(b)); }

  function toNumber(v) {
    if (v == null || (typeof v === "string" && v.trim() === "")) return null;
    const n = typeof v === "number" ? v : Number(v);
    return isFinite(n) ? n : null;
  }

  function pathText(item) {
    const p = item && item.path;
    if (Array.isArray(p)) return p.filter((x) => x != null && x !== "").join(" › ");
    return p == null ? "" : String(p);
  }

  // The default Category column: the group when there is one, else the path.
  function categoryOf(item) {
    return item.group != null && String(item.group).trim() !== "" ? String(item.group) : pathText(item);
  }

  function valueOf(column, item) {
    return typeof column.value === "function" ? column.value(item) : item[column.key];
  }

  // What a column sorts by: null (missing) | a number (numeric column) | a natural key (text column).
  function sortKey(column, item) {
    const v = valueOf(column, item);
    if (column.numeric) return toNumber(v);
    return v == null || String(v).trim() === "" ? null : naturalKey(v);
  }

  // A missing value sorts LAST in both directions; everything else by `numeric`/natural order, reversed for "desc".
  function compareSortKeys(ka, kb, numeric, dir) {
    if (ka === null || kb === null) return ka === kb ? 0 : ka === null ? 1 : -1;
    const r = numeric ? ka - kb : compareKeys(ka, kb);
    return dir === "desc" ? -r : r;
  }

  // (itemA, itemB) => number for one column. Ties return 0: sort with the original index as the tiebreak (sortedIndices does).
  function comparator(column, dir) {
    return (a, b) => compareSortKeys(sortKey(column, a), sortKey(column, b), !!column.numeric, dir);
  }

  // Indices of `items` in sorted order, stable. `keys` (optional) are the sortKey()s already computed for this column.
  function sortedIndices(items, column, dir, keys) {
    const ks = keys || items.map((it) => sortKey(column, it));
    const numeric = !!column.numeric;
    const idx = items.map((_, i) => i);
    idx.sort((a, b) => compareSortKeys(ks[a], ks[b], numeric, dir) || a - b);
    return idx;
  }

  function sortItems(items, column, dir) { return sortedIndices(items, column, dir).map((i) => items[i]); }

  function searchWords(query) { return fold(query).split(/\s+/).filter(Boolean); }

  // Everything the search looks at, folded; "\n" between the parts so a word can never match across two of them. With `columns` it also
  // takes in the text every NON-numeric column shows (a "TikTok code" column, a "Top level" placeholder): what is on screen can be searched.
  // The id and the numeric columns (sizes, rates) are never searched.
  function haystack(item, columns) {
    const parts = [item.name, item.group, pathText(item), item.description];
    if (columns) columns.forEach((c) => { if (!c.numeric) { const t = cellText(c, item); if (t !== "" && parts.indexOf(t) < 0) parts.push(t); } });
    return fold(parts.filter((x) => x != null && x !== "").join("\n"));
  }

  function matchesWords(hay, words) {
    for (let i = 0; i < words.length; i++) if (hay.indexOf(words[i]) < 0) return false;
    return true;
  }

  function filterPredicate(query, columns) {
    const words = searchWords(query);
    return (item) => matchesWords(haystack(item, columns), words);
  }

  // [start, end) ranges of `text` (original characters) covered by the typed words, merged. Used for the highlight.
  function highlightRanges(text, words) {
    text = text == null ? "" : String(text);
    if (!words.length || !text) return [];
    let folded = "";
    const map = [];                                              // folded index -> index in `text`
    for (let i = 0; i < text.length;) {
      const ch = String.fromCodePoint(text.codePointAt(i));
      const f = fold(ch);
      for (let k = 0; k < f.length; k++) map.push(i);
      folded += f;
      i += ch.length;
    }
    const spans = [];
    words.forEach((w) => {
      for (let from = 0, at; (at = folded.indexOf(w, from)) >= 0; from = at + w.length) spans.push([at, at + w.length]);
    });
    spans.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const merged = [];
    spans.forEach(([s, e]) => {
      const last = merged[merged.length - 1];
      if (last && s <= last[1]) last[1] = Math.max(last[1], e);
      else merged.push([s, e]);
    });
    return merged.map(([s, e]) => [map[s], e < map.length ? map[e] : text.length]).filter(([s, e]) => e > s);
  }

  // 1,234,567 -> "1.2M"; 15,000 -> "15K"; 999,999 -> "1M". Audience sizes are estimates, so a short form reads better than 7 digits.
  function compact(n) {
    n = Number(n);
    if (!isFinite(n)) return "—";
    const sign = n < 0 ? "-" : "", a = Math.abs(n);
    if (a < 1000) return sign + String(Math.round(a));
    const round = (x) => (x >= 10 ? Math.round(x) : Math.round(x * 10) / 10);
    const k = round(a / 1e3);
    if (k < 1000) return sign + k + "K";
    const m = round(a / 1e6);
    if (m < 1000) return sign + m + "M";
    return sign + round(a / 1e9) + "B";
  }

  function formatNumber(n) { return Number(n).toLocaleString("en-US"); }

  // The "size" column: the lower bound, or "lower – upper" when Item.sizeUpper is larger; "—" when there is no size.
  function formatSize(item) {
    const lo = toNumber(item.size), hi = toNumber(item.sizeUpper);
    if (lo === null) return "—";
    return hi !== null && hi > lo ? compact(lo) + " – " + compact(hi) : compact(lo);
  }

  function sizeTitle(item) {
    const lo = toNumber(item.size), hi = toNumber(item.sizeUpper);
    if (lo === null) return "";
    return hi !== null && hi > lo ? formatNumber(lo) + " to " + formatNumber(hi) + " people" : formatNumber(lo) + " people";
  }

  function defaultColumns() {
    return [{ key: "name", label: "Name" }, { key: "category", label: "Category", value: categoryOf }];
  }

  function normColumns(cols) {
    const list = Array.isArray(cols) && cols.length ? cols : defaultColumns();
    return list.map((c) => Object.assign({}, c, { key: String(c.key), label: c.label == null ? String(c.key) : String(c.label), numeric: !!c.numeric }));
  }

  // The text a cell shows. A failing caller-supplied format() must not take the whole list down.
  function cellText(column, item) {
    const v = valueOf(column, item);
    if (typeof column.format === "function") {
      try { const t = column.format(v, item); return t == null ? "" : String(t); } catch (e) { return ""; }
    }
    if (column.key === "size") return formatSize(item);
    if (column.numeric) return toNumber(v) === null ? "—" : formatNumber(toNumber(v));
    return v == null ? "" : String(v);
  }

  // The typed search, shortened for the "Nothing matches ..." lines (a pasted list of 200 ZIPs must not fill the screen or the screen reader).
  function echoQuery(query) {
    const chars = Array.from(String(query == null ? "" : query).trim());
    return chars.length > ECHO_MAX ? chars.slice(0, ECHO_MAX).join("") + "…" : chars.join("");
  }

  function singular(noun) {
    return /ies$/.test(noun) ? noun.slice(0, -3) + "y" : /s$/.test(noun) ? noun.slice(0, -1) : noun;
  }

  // What changed against what the caller already held. `initial` and `picked` are Maps of key -> { tab, item }; both keep insertion order.
  function diffSelection(initial, picked) {
    const added = [], removed = [];
    picked.forEach((v, k) => { if (!initial.has(k)) added.push({ tab: v.tab, item: v.item }); });
    initial.forEach((v, k) => { if (!picked.has(k)) removed.push({ tab: v.tab, item: v.item }); });
    return { added, removed };
  }

  // The primary button. A caller's label ("Add to audience") is a promise to ADD, so it is used only while nothing is being removed; the moment
  // something is unticked the button says what it really does, whatever the label: "Remove 2", or "Apply changes (3)" for a mix.
  function applyText(label, added, removed) {
    if (label) {
      if (!added && !removed) return label;
      if (!removed) return label + " (" + added + ")";
      return added ? "Apply changes (" + (added + removed) + ")" : "Remove " + removed;
    }
    if (added && removed) return "Add " + added + ", remove " + removed;
    if (added) return "Add " + added + " selected";
    if (removed) return "Remove " + removed;
    return DEFAULT_APPLY;
  }

  // ---------------------------------------------------------------- the dialog

  let current = null;                      // the one open dialog: { close }
  let uid = 0;
  let scrollLock = null;

  const fmt = (n) => formatNumber(n);

  function h(tag, props) {
    const node = document.createElement(tag);
    if (props) {
      Object.keys(props).forEach((k) => {
        const v = props[k];
        if (v == null || v === false) return;
        if (k === "class") node.className = v;
        else if (k === "text") node.textContent = v;
        else node.setAttribute(k, v === true ? "" : String(v));
      });
    }
    for (let i = 2; i < arguments.length; i++) if (arguments[i]) node.append(arguments[i]);   // strings become text nodes: never parsed as HTML
    return node;
  }

  // `text` into `el` with the searched words wrapped in <mark>. Built from text nodes, so an item name can never inject markup.
  function appendHighlighted(el, text, words) {
    text = text == null ? "" : String(text);
    const ranges = highlightRanges(text, words);
    if (!ranges.length) { el.append(text); return; }
    let at = 0;
    ranges.forEach(([s, e]) => {
      if (s > at) el.append(text.slice(at, s));
      el.append(h("mark", { class: "segb-mark", text: text.slice(s, e) }));
      at = e;
    });
    if (at < text.length) el.append(text.slice(at));
  }

  function visible(el) { return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length); }

  // The page behind must not scroll while the dialog is open, and must come back EXACTLY as it was: the whole inline style attribute is put
  // back when nothing else touched it meanwhile; if something did, only the properties we changed are restored.
  function lockPageScroll() {
    const body = document.body, root = document.documentElement;
    const bar = Math.max(0, window.innerWidth - root.clientWidth);          // keep the page from jumping sideways when its scrollbar goes
    const pad = (parseFloat(window.getComputedStyle(body).paddingRight) || 0) + bar;
    const was = {};
    ["overflow-x", "overflow-y", "padding-right"].forEach((p) => { was[p] = [body.style.getPropertyValue(p), body.style.getPropertyPriority(p)]; });
    const before = body.getAttribute("style");
    body.style.setProperty("overflow", "hidden");
    if (bar) body.style.setProperty("padding-right", pad + "px");
    scrollLock = { before, after: body.getAttribute("style"), was };
  }

  function unlockPageScroll() {
    if (!scrollLock) return;
    const body = document.body, s = scrollLock;
    scrollLock = null;
    if (body.getAttribute("style") === s.after) {
      if (s.before === null) body.removeAttribute("style"); else body.setAttribute("style", s.before);
      return;
    }
    Object.keys(s.was).forEach((p) => { if (s.was[p][0]) body.style.setProperty(p, s.was[p][0], s.was[p][1]); else body.style.removeProperty(p); });
  }

  function open(opts) {
    if (typeof document === "undefined") return { close() {} };
    opts = opts || {};
    if (current) current.close();                                           // one dialog at a time (the first one is discarded)

    const opener = opts.returnFocus || document.activeElement;
    const pre = "segb" + (++uid);
    const columns = normColumns(opts.columns);
    const nameColumn = columns.find((c) => c.key === "name") || columns[0];
    const tabs = Array.isArray(opts.tabs) && opts.tabs.length
      ? opts.tabs.map((t) => ({ key: String(t.key), label: t.label == null ? String(t.key) : String(t.label) }))
      : [{ key: "all", label: "All" }];
    const showTabs = Array.isArray(opts.tabs) && opts.tabs.length > 0;
    const max = typeof opts.max === "number" && isFinite(opts.max) && opts.max >= 0 ? Math.floor(opts.max) : null;
    const perTab = typeof opts.maxPerTab === "number" && isFinite(opts.maxPerTab) && opts.maxPerTab >= 0 ? Math.floor(opts.maxPerTab) : null;
    const noun = opts.noun ? String(opts.noun) : "segments";
    const phone = () => !!(window.matchMedia && window.matchMedia(PHONE_QUERY).matches);

    const S = {
      tab: (tabs.find((t) => t.key === String(opts.activeTab)) || tabs[0]).key,
      query: "", words: [], sort: { key: nameColumn.key, dir: "asc" },
      view: [],                            // indices into the active tab's items: sorted, then filtered
      shown: 0, picked: new Map(), initial: new Map(), entries: {}, estimate: {}, closed: false,
      clearing: new Set(),                 // tabs Clear is still loading (their held picks are being cleared, not ticked)
      clearFailed: [],                     // labels of the tabs that would not load while clearing
      asking: false,                       // the "Close without applying?" bar is up
    };
    tabs.forEach((t) => {
      let est = 0;
      if (typeof opts.chosenCount === "function") { try { est = Math.max(0, Math.floor(Number(opts.chosenCount(t.key)) || 0)); } catch (e) { est = 0; } }
      S.estimate[t.key] = est;
      S.entries[t.key] = { status: "idle", items: [], keys: [], hay: null, sortKeys: {}, orders: {}, selected: 0, extra: 0, error: "", token: 0 };
    });

    let rowsLimited = false;               // whether the rows on the screen were last drawn/synced as "at the limit" (the unticked ones disabled)
    const entry = () => S.entries[S.tab];
    const sortColumn = () => columns.find((c) => c.key === S.sort.key) || nameColumn;

    // ------------------------------------------------ DOM
    const titleId = pre + "-title", subId = pre + "-sub", bodyId = pre + "-body";
    const dialog = h("div", { class: "segb-dialog", role: "dialog", "aria-modal": "true", "aria-labelledby": titleId, "aria-describedby": opts.subtitle ? subId : null, tabindex: "-1" });
    const overlay = h("div", { class: "segb-overlay" }, dialog);

    const closeBtn = h("button", { type: "button", class: "segb-x", "aria-label": "Close without changes" }, "×");
    dialog.append(h("header", { class: "segb-head" },
      h("div", { class: "segb-head-text" },
        h("h2", { class: "segb-title", id: titleId, text: opts.title == null ? "Browse" : opts.title }),
        opts.subtitle ? h("p", { class: "segb-sub", id: subId, text: opts.subtitle }) : null),
      closeBtn));

    const tabButtons = [];
    if (showTabs) {
      const bar = h("div", { class: "segb-tabs", role: "tablist", "aria-label": opts.title == null ? "Lists" : String(opts.title) });
      tabs.forEach((t, n) => {
        const btn = h("button", { type: "button", class: "segb-tab", role: "tab", id: pre + "-tab" + n, "aria-controls": bodyId, "data-tab": t.key },
          h("span", { class: "segb-tab-label", text: t.label }), h("span", { class: "segb-tab-count" }));
        tabButtons.push(btn);
        bar.append(btn);
      });
      dialog.append(bar);
    }

    const searchInput = h("input", { type: "search", class: "segb-search-input", autocomplete: "off", spellcheck: "false", "aria-controls": bodyId,
      placeholder: opts.searchPlaceholder || "Search by name, category or keyword", "aria-label": "Search " + noun });
    const searchClear = h("button", { type: "button", class: "segb-search-clear", "aria-label": "Clear search", hidden: true }, "×");
    const countEl = h("p", { class: "segb-count", role: "status", "aria-live": "polite", "aria-atomic": "true" });
    dialog.append(h("div", { class: "segb-tools" }, h("div", { class: "segb-search" }, searchInput, searchClear), countEl));

    const limitEl = h("p", { class: "segb-limit", role: "status", hidden: true });
    dialog.append(limitEl);

    // header row, built once
    const allBox = h("input", { type: "checkbox", class: "segb-all" });
    const headRow = h("tr", { class: "segb-head-row" }, h("th", { class: "segb-th segb-th-check", scope: "col" }, allBox));
    const thFor = {};
    columns.forEach((c) => {
      const th = h("th", { class: "segb-th" + (c.numeric ? " segb-num" : ""), scope: "col", "aria-sort": c.sortable === false ? null : "none" });
      if (c.sortable === false) th.append(c.label);
      else th.append(h("button", { type: "button", class: "segb-sort", "data-key": c.key, title: "Sort by " + c.label },
        h("span", { class: "segb-sort-label", text: c.label }), h("span", { class: "segb-sort-ico", "aria-hidden": "true" })));
      thFor[c.key] = th;
      headRow.append(th);
    });
    const tbody = h("tbody", { class: "segb-tbody" });
    const table = h("table", { class: "segb-table", role: "grid", "aria-multiselectable": "true", "aria-labelledby": titleId, hidden: true },
      h("thead", { class: "segb-thead" }, headRow), tbody);
    const moreBtn = h("button", { type: "button", class: "segb-more" });
    const moreLeft = h("span", { class: "segb-more-left" });
    const moreRow = h("tr", { class: "segb-more-row" }, h("td", { colspan: columns.length + 1 }, moreBtn, moreLeft));
    const stateEl = h("div", { class: "segb-state", hidden: true });
    const scroller = h("div", { class: "segb-body", id: bodyId, role: showTabs ? "tabpanel" : null }, table, stateEl);
    dialog.append(scroller);

    const discardText = h("p", { class: "segb-discard-text" });
    const keepBtn = h("button", { type: "button", class: "btn-secondary segb-keep" }, "Keep picking");
    const discardBtn = h("button", { type: "button", class: "btn-secondary segb-discard-btn" }, "Discard and close");
    const discardEl = h("div", { class: "segb-discard", role: "alert", hidden: true }, discardText, h("div", { class: "segb-discard-actions" }, keepBtn, discardBtn));
    dialog.append(discardEl);

    const selN = h("span", { class: "segb-sel-n" });
    const selDelta = h("span", { class: "segb-sel-delta" });
    const clearBtn = h("button", { type: "button", class: "segb-clear" }, "Clear");
    const cancelBtn = h("button", { type: "button", class: "btn-secondary segb-cancel" }, "Cancel");
    const applyBtn = h("button", { type: "button", class: "btn-primary segb-apply" }, opts.applyLabel || DEFAULT_APPLY);
    const live = h("p", { class: "segb-live", role: "status", "aria-live": "polite", "aria-atomic": "true" });
    dialog.append(h("footer", { class: "segb-foot" },
      h("div", { class: "segb-sel" }, h("div", { class: "segb-sel-text" }, selN, selDelta), clearBtn),
      h("div", { class: "segb-foot-actions" }, cancelBtn, applyBtn)), live);

    // ------------------------------------------------ the loading of a tab
    function loadTab(key) {
      const en = S.entries[key];
      if (en.status === "loading" || en.status === "ready") return;
      en.status = "loading";
      const token = ++en.token;
      let p;
      try { p = Promise.resolve(opts.load(key)); } catch (err) { p = Promise.reject(err); }
      p.then((res) => {
        if (S.closed || token !== en.token) return;
        if (Array.isArray(res)) accept(key, en, res);
        else {
          en.status = "error";
          en.error = res && typeof res === "object" && res.error ? String(res.error) : LOAD_FAILED;
        }
        settleClear(key);
        loaded(key);
      }, (err) => {
        if (S.closed || token !== en.token) return;
        en.status = "error";
        en.error = err && err.message ? String(err.message) : LOAD_FAILED;
        settleClear(key);
        loaded(key);
      });
    }

    // Takes the items in: drops a repeated id, and ticks the ones the caller already holds (unless Clear is waiting for this tab: then they
    // are remembered as held, so Apply reports them as removed, but they come in unticked).
    function accept(key, en, raw) {
      const seen = new Set();
      const cleared = S.clearing.has(key);
      let already = 0, inList = 0;
      raw.forEach((item, n) => {
        if (!item || typeof item !== "object") return;
        const id = item.id != null ? String(item.id) : "#" + n;
        if (seen.has(id)) return;
        seen.add(id);
        const k = key + "\u0001" + id;
        en.items.push(item);
        en.keys.push(k);
        let chosen = false;
        if (typeof opts.isChosen === "function") { try { chosen = !!opts.isChosen(key, item); } catch (e) { chosen = false; } }
        if (chosen) {
          S.initial.set(k, { tab: key, item });
          inList++;
          if (!cleared) { S.picked.set(k, { tab: key, item }); already++; }
        }
      });
      en.selected = already;
      en.extra = Math.max(0, S.estimate[key] - inList);                     // held by the caller but not in this list: counted, never removed here
      en.status = "ready";
    }

    function loaded(key) {
      const btn = tabButtons[tabs.findIndex((t) => t.key === key)];
      const en = S.entries[key];
      if (btn) btn.querySelector(".segb-tab-count").textContent = en.status === "ready" ? fmt(en.items.length) : "";
      if (key === S.tab) refresh();
      else afterChange();                                                    // its already-held items may have reached the limit
      revealTab();                                                           // a badge that arrived widens a tab and can push the selected one out of view
    }

    // ------------------------------------------------ the view: sorted once per change, then filtered
    function orderFor(en) {
      const col = sortColumn(), id = col.key + "|" + S.sort.dir;
      if (!en.orders[id]) {
        if (!en.sortKeys[col.key]) en.sortKeys[col.key] = en.items.map((it) => sortKey(col, it));
        en.orders[id] = sortedIndices(en.items, col, S.sort.dir, en.sortKeys[col.key]);
      }
      return en.orders[id];
    }

    function computeView(en) {
      const order = orderFor(en);
      if (!S.words.length) { S.view = order; return; }
      if (!en.hay) en.hay = en.items.map((it) => haystack(it, columns));
      S.view = order.filter((i) => matchesWords(en.hay[i], S.words));
    }

    // ------------------------------------------------ drawing
    function refresh() {
      if (S.closed) return;
      const en = entry();
      updateTabs();
      if (en.status === "idle") loadTab(S.tab);
      scroller.setAttribute("aria-busy", en.status === "loading" ? "true" : "false");
      if (en.status === "idle" || en.status === "loading") showState("loading");
      else if (en.status === "error") showState("error", en.error);
      else {
        computeView(en);
        if (!en.items.length) showState("empty");
        else if (!S.view.length) showState("nomatch");
        else showList();
      }
      updateSortHeaders();
      updateCount();
      updateFooter();
    }

    function showState(kind, message) {
      const hadFocus = stateEl.contains(document.activeElement);            // Retry / "Clear search" are about to be rebuilt
      table.hidden = true;
      stateEl.hidden = false;
      stateEl.textContent = "";
      stateEl.className = "segb-state segb-state--" + kind;
      stateEl.removeAttribute("role");
      if (kind === "loading") {
        stateEl.append(h("span", { class: "segb-sr", text: "Loading…" }));
        for (let i = 0; i < 8; i++) stateEl.append(h("span", { class: "segb-skel", style: "width:" + (46 + ((i * 17) % 44)) + "%" }));
      } else if (kind === "error") {
        stateEl.setAttribute("role", "alert");
        stateEl.append(h("strong", { class: "segb-state-title", text: "Couldn’t load this list" }), h("p", { class: "segb-state-text", text: message }),
          h("button", { type: "button", class: "btn-secondary segb-retry" }, "Retry"));
      } else if (kind === "empty") {
        stateEl.append(h("p", { class: "segb-state-text", text: opts.emptyText || "There’s nothing to show here yet." }));
      } else {
        stateEl.append(h("strong", { class: "segb-state-title", text: "Nothing matches “" + echoQuery(S.query) + "”" }),
          h("p", { class: "segb-state-text", text: "Try fewer or different words, or clear the search to see all " + fmt(entry().items.length) + " " + noun + "." }),
          h("button", { type: "button", class: "btn-secondary segb-clear-search" }, "Clear search"));
      }
      if (hadFocus) (stateEl.querySelector("button") || stable()).focus();   // a focused button that disappeared would drop the focus to <body>, outside the dialog
    }

    // An element that is always there to hand the keyboard focus to: the search box (the dialog itself on a phone, so the keyboard stays down).
    function stable() { return phone() ? dialog : searchInput; }

    function showList() {
      stateEl.hidden = true;
      table.hidden = false;
      table.setAttribute("aria-rowcount", String(S.view.length + 1));
      tbody.textContent = "";
      S.shown = Math.min(CHUNK, S.view.length);
      drawRows(0, S.shown);
      scroller.scrollTop = 0;
      fillIfShort();
      fitWords();
    }

    // Cells wrap at spaces and a word breaks only when it cannot fit anywhere. The table is laid out with whole words first; if that is wider
    // than the sheet (a 40-character hashtag on a phone), .is-tight lets a cell break inside a word, which is what keeps it from scrolling sideways.
    function fitWords() {
      table.classList.remove("is-tight");
      if (!table.hidden && scroller.scrollWidth > scroller.clientWidth + 1) table.classList.add("is-tight");
    }
    // Chunks that are drawn later (scrolling, End on a 10,000-row list) ask for the same check once, on the next frame, not once per chunk.
    let fitQueued = false;
    function fitSoon() {
      if (fitQueued) return;
      fitQueued = true;
      (window.requestAnimationFrame || setTimeout)(() => { fitQueued = false; if (!S.closed) fitWords(); });
    }
    function onResize() { if (!S.closed && !table.hidden) fitSoon(); }

    function drawRows(from, to) {
      const en = entry(), frag = document.createDocumentFragment(), limited = blocking();
      rowsLimited = limited;
      for (let pos = from; pos < to; pos++) frag.append(buildRow(en, S.view[pos], pos, limited));
      if (moreRow.parentNode === tbody) tbody.insertBefore(frag, moreRow);   // the "Show more" row never moves: moving it would drop the keyboard focus
      else tbody.append(frag);
      updateMoreRow();
      const first = tbody.querySelector("tr.segb-row[tabindex='0']") || tbody.querySelector("tr.segb-row");
      if (first) first.tabIndex = 0;                                         // one row is the tab stop; the arrow keys move between rows
      if (from > 0) fitSoon();                                               // a later chunk may bring a longer word
    }

    function buildRow(en, i, pos, limited) {
      const item = en.items[i], on = S.picked.has(en.keys[i]);
      const tr = h("tr", { class: "segb-row" + (on ? " is-selected" : ""), role: "row", tabindex: "-1", "data-i": i, "data-id": item.id, "aria-rowindex": pos + 2, "aria-selected": on ? "true" : "false" });
      const box = h("input", { type: "checkbox", class: "segb-check", tabindex: "-1", "aria-label": "Select " + (item.name == null ? item.id : item.name) });
      box.checked = on;
      const blocked = limited && !on;
      if (blocked) { box.disabled = true; tr.setAttribute("aria-disabled", "true"); tr.classList.add("is-blocked"); }
      tr.append(h("td", { class: "segb-td segb-td-check" }, box));
      columns.forEach((c) => {
        const td = h("td", { class: "segb-td" + (c.numeric ? " segb-num" : "") + (c === nameColumn ? " segb-td-name" : "") });
        const text = cellText(c, item);
        if (c.numeric) td.textContent = text;
        else {
          const main = h("span", { class: "segb-cell" });
          appendHighlighted(main, text, S.words);
          td.append(main);
        }
        if (c.key === "size" && !c.format) { const t = sizeTitle(item); if (t) td.title = t; }
        if (c === nameColumn && item.description) {
          const d = h("span", { class: "segb-desc" });
          appendHighlighted(d, item.description, S.words);
          td.append(d);
        }
        tr.append(td);
      });
      return tr;
    }

    function updateMoreRow() {
      const left = S.view.length - S.shown;
      if (left <= 0) {
        const lost = moreRow.contains(document.activeElement);
        moreRow.remove();
        if (lost) { const rows = tbody.querySelectorAll("tr.segb-row"); if (rows.length) rows[rows.length - 1].focus(); }
        return;
      }
      moreBtn.textContent = "Show " + Math.min(CHUNK, left) + " more";
      moreLeft.textContent = fmt(left) + " more not shown yet";
      if (moreRow.parentNode !== tbody) tbody.append(moreRow);
    }

    function drawMore(focusNew) {
      if (S.shown >= S.view.length) return;
      const from = S.shown;
      S.shown = Math.min(S.view.length, from + CHUNK);
      drawRows(from, S.shown);
      if (focusNew && tbody.children[from]) tbody.children[from].focus();
    }

    // Tall windows may show the whole first chunk with room to spare: keep drawing until the list fills the box or ends.
    function fillIfShort() {
      let guard = 0;
      while (S.shown < S.view.length && scroller.scrollHeight <= scroller.clientHeight + 4 && guard++ < 60) drawMore(false);
    }

    function onScroll() {
      if (S.shown < S.view.length && scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < LOAD_AHEAD_PX) drawMore(false);
    }

    function updateTabs() {
      tabButtons.forEach((btn, n) => {
        const on = tabs[n].key === S.tab;
        btn.setAttribute("aria-selected", on ? "true" : "false");
        btn.tabIndex = on ? 0 : -1;
        if (on) scroller.setAttribute("aria-labelledby", btn.id);
      });
      revealTab();
    }

    // The tab strip scrolls sideways on a phone: the selected tab (label, count and focus ring) must never be left half outside it.
    function revealTab() {
      const btn = tabButtons[tabs.findIndex((t) => t.key === S.tab)];
      if (!btn) return;
      const bar = btn.parentNode, b = btn.getBoundingClientRect(), r = bar.getBoundingClientRect(), pad = 8;
      if (!b.width || !r.width) return;                                      // not laid out (yet)
      if (b.left < r.left + pad) bar.scrollLeft -= r.left + pad - b.left;
      else if (b.right > r.right - pad) bar.scrollLeft += b.right - (r.right - pad);
    }

    function updateSortHeaders() {
      columns.forEach((c) => {
        const th = thFor[c.key];
        if (c.sortable === false) return;
        th.setAttribute("aria-sort", S.sort.key === c.key ? (S.sort.dir === "asc" ? "ascending" : "descending") : "none");
      });
    }

    function updateCount() {
      const en = entry();
      let text = "";
      if (en.status === "loading" || en.status === "idle") text = "Loading…";
      else if (en.status === "ready") {
        const total = en.items.length, n = S.view.length;
        const word = (c) => (c === 1 ? opts.nounSingular || singular(noun) : noun);
        if (!S.words.length) text = fmt(total) + " " + word(total);
        else if (!n) text = "Nothing matches “" + echoQuery(S.query) + "”";
        else text = fmt(n) + " of " + fmt(total) + " " + word(total) + " match";
      }
      countEl.textContent = text;
    }

    // ------------------------------------------------ the selection
    // What the caller holds in one tab: the ticks and the picks the list does not show, or - for a tab that has not loaded - its chosenCount.
    // A tab Clear is still loading counts as empty: its picks are being cleared.
    function heldIn(key) {
      const en = S.entries[key];
      if (en.status === "ready") return en.selected + en.extra;
      return S.clearing.has(key) ? 0 : S.estimate[key];
    }
    function held() {
      let n = 0;
      tabs.forEach((t) => { n += heldIn(t.key); });
      return n;
    }
    const atLimit = () => max !== null && held() >= max;
    const tabFull = (key) => perTab !== null && heldIn(key) >= perTab;
    const blocking = () => atLimit() || tabFull(S.tab);                    // whether the unticked rows OF THE TAB ON SHOW are disabled

    const limitText = () => "You’ve reached the limit of " + max + ". Untick one to add another.";
    function tabNote(key) {
      if (typeof opts.maxNote === "function") {
        try { const t = opts.maxNote(key); if (t) return String(t); } catch (e) { /* the default below */ }
      }
      const label = tabs.find((t) => t.key === key).label;
      return "You’ve reached the limit of " + fmt(perTab) + (showTabs ? " for " + label : "") + ". Untick one to add another.";
    }
    // The note under the search box: the global limit when it is the one that bites, else the per-tab one, else nothing.
    const limitNote = () => (atLimit() ? limitText() : tabFull(S.tab) ? tabNote(S.tab) : "");

    function setPicked(key, i, on) {
      const en = S.entries[key], k = en.keys[i], was = S.picked.has(k);
      if (on && !was) { S.picked.set(k, { tab: key, item: en.items[i] }); en.selected++; }
      else if (!on && was) { S.picked.delete(k); en.selected--; }
    }

    function syncRow(tr, limited) {
      const en = entry(), i = Number(tr.dataset.i), on = S.picked.has(en.keys[i]);
      const box = tr.querySelector(".segb-check");
      const blocked = limited && !on;
      tr.setAttribute("aria-selected", on ? "true" : "false");
      tr.classList.toggle("is-selected", on);
      tr.classList.toggle("is-blocked", blocked);
      if (blocked) tr.setAttribute("aria-disabled", "true"); else tr.removeAttribute("aria-disabled");
      box.checked = on;
      box.disabled = blocked;
    }

    function syncRows() {
      const limited = blocking();
      rowsLimited = limited;
      tbody.querySelectorAll("tr.segb-row").forEach((tr) => syncRow(tr, limited));
    }

    function afterChange() {
      if (blocking() !== rowsLimited) syncRows();
      updateFooter();
    }

    // What a screen reader hears. The region is emptied first and filled a moment later: the same sentence twice in a row (a second tap on a
    // blocked row) is still a change, so it is read out again.
    let sayTimer = null;
    function say(text) {
      clearTimeout(sayTimer);
      live.textContent = "";
      sayTimer = setTimeout(() => { sayTimer = null; if (!S.closed) live.textContent = text; }, SAY_DELAY_MS);
    }

    function toggleRow(tr) {
      const en = entry(), i = Number(tr.dataset.i), on = S.picked.has(en.keys[i]);
      if (!on && blocking()) { say(limitNote()); return; }
      setPicked(S.tab, i, !on);
      dropAsk();
      syncRow(tr, blocking());
      afterChange();
    }

    function toggleAll() {
      const en = entry();
      if (en.status !== "ready" || !S.view.length) return;
      let said = "";
      if (S.view.every((i) => S.picked.has(en.keys[i]))) {
        S.view.forEach((i) => setPicked(S.tab, i, false));
        said = "Cleared all " + fmt(S.view.length) + " shown.";
      } else {
        let room = Math.min(max === null ? Infinity : max - held(), perTab === null ? Infinity : perTab - heldIn(S.tab)), added = 0;
        for (let p = 0; p < S.view.length && room > 0; p++) {
          const i = S.view[p];
          if (!S.picked.has(en.keys[i])) { setPicked(S.tab, i, true); room--; added++; }
        }
        if (added) said = "Selected " + fmt(added) + (added < S.view.length ? " of " + fmt(S.view.length) + " shown" : "") + (blocking() ? ". " + limitNote() : ".");
        else {                                                               // no room: the box unticks what this tab has in view, and says so; with nothing to untick it says why nothing happened
          let cleared = 0;
          S.view.forEach((i) => { if (S.picked.has(en.keys[i])) { setPicked(S.tab, i, false); cleared++; } });
          said = cleared ? "Cleared the selection shown." : "Nothing was added. " + limitNote();
        }
      }
      dropAsk();
      syncRows();
      updateFooter();
      say(said);
    }

    // Unticks everything the caller holds. A tab that has not loaded yet holds picks too (chosenCount says how many; without it every unloaded
    // tab is assumed to): those tabs are loaded now and come in cleared, and Apply waits until they have, because it needs the items to report them.
    function clearAll() {
      if (S.clearing.size) return;
      S.picked.clear();
      tabs.forEach((t) => { S.entries[t.key].selected = 0; });
      const known = typeof opts.chosenCount === "function";
      const waiting = tabs.filter((t) => S.entries[t.key].status !== "ready" && (!known || S.estimate[t.key] > 0));
      waiting.forEach((t) => S.clearing.add(t.key));
      dropAsk();
      syncRows();
      waiting.forEach((t) => loadTab(t.key));
      if (waiting.some((t) => t.key === S.tab)) refresh();
      updateFooter();
      say(waiting.length ? "Clearing the selection…" : "Selection cleared.");
    }

    // Called when a tab Clear was waiting for has loaded (or failed to).
    function settleClear(key) {
      if (!S.clearing.delete(key)) return;
      if (S.entries[key].status !== "ready") S.clearFailed.push(tabs.find((t) => t.key === key).label);
      if (S.clearing.size) return;
      const failed = S.clearFailed;
      S.clearFailed = [];
      say(failed.length ? "Selection cleared, except " + failed.join(" and ") + ", which couldn’t be loaded." : "Selection cleared.");
    }

    function pendingCount() {
      const d = diffSelection(S.initial, S.picked);
      return d.added.length + d.removed.length;
    }

    function updateFooter() {
      const en = entry(), n = held();
      selN.textContent = max === null ? fmt(n) + " selected" : fmt(n) + " of " + fmt(max) + " selected";
      const { added, removed } = diffSelection(S.initial, S.picked);
      const parts = [];
      if (added.length) parts.push(fmt(added.length) + " to add");
      if (removed.length) parts.push(fmt(removed.length) + " to remove");
      selDelta.textContent = parts.join(" · ");
      // Clear is there while something can be cleared: a tick, or (when chosenCount says so) picks in a tab that has not loaded yet.
      const unseen = typeof opts.chosenCount === "function" && tabs.some((t) => S.entries[t.key].status !== "ready" && S.estimate[t.key] > 0);
      const busy = S.clearing.size > 0;
      clearBtn.disabled = !busy && S.picked.size === 0 && !unseen;
      clearBtn.textContent = busy ? "Clearing…" : "Clear";
      if (busy) clearBtn.setAttribute("aria-busy", "true"); else clearBtn.removeAttribute("aria-busy");
      if (clearBtn.disabled && document.activeElement === clearBtn) stable().focus();   // a disabled button cannot keep the focus everywhere
      applyBtn.textContent = applyText(opts.applyLabel, added.length, removed.length);
      applyBtn.disabled = busy || added.length + removed.length === 0;
      const note = limitNote();
      limitEl.hidden = !note;
      limitEl.textContent = note;
      if (S.asking) { if (added.length + removed.length) showAsk(); else dropAsk(); }
      // the header box: ticked when everything matching is ticked, dashed when some is
      let some = 0;
      if (en.status === "ready") for (let p = 0; p < S.view.length; p++) if (S.picked.has(en.keys[S.view[p]])) some++;
      const total = en.status === "ready" ? S.view.length : 0;
      allBox.checked = total > 0 && some === total;
      allBox.indeterminate = some > 0 && some < total;
      allBox.disabled = total === 0;
      allBox.setAttribute("aria-label", "Select all " + fmt(total) + " shown");
    }

    // ------------------------------------------------ closing
    let searchTimer = null;
    function flushSearch() {
      if (searchTimer === null) return;
      clearTimeout(searchTimer);
      searchTimer = null;
      applyQuery();
    }
    function clearSearch() {
      clearTimeout(searchTimer);
      searchTimer = null;
      searchInput.value = "";
      searchClear.hidden = true;
      applyQuery();
      searchInput.focus();
    }
    function applyQuery() {
      S.query = searchInput.value;
      S.words = searchWords(S.query);
      refresh();
    }

    function close(apply) {
      if (S.closed) return;
      const result = apply ? diffSelection(S.initial, S.picked) : null;
      S.closed = true;
      clearTimeout(searchTimer);
      clearTimeout(sayTimer);
      document.removeEventListener("keydown", onDocKey, true);
      document.removeEventListener("focusin", onFocusIn, true);
      window.removeEventListener("resize", onResize);
      overlay.remove();
      unlockPageScroll();
      if (current === handle) current = null;
      if (opener && opener.isConnected && typeof opener.focus === "function") { try { opener.focus({ preventScroll: true }); } catch (e) { /* the opener is gone: nothing to return to */ } }
      if (result && typeof opts.onApply === "function") opts.onApply(result);
    }

    // Esc and the X never throw ticks away without being asked twice: the first one raises the "Close without applying?" bar (an alert, so a
    // screen reader reads it out), the second Esc closes. Cancel and "Discard and close" are the explicit way out, and need no second step.
    function showAsk() {
      const n = pendingCount(), text = "Close without applying your " + fmt(n) + (n === 1 ? " change" : " changes") + "? Press Esc again to close, or keep picking.";
      if (discardText.textContent !== text) discardText.textContent = text;
      discardEl.hidden = false;
      S.asking = true;
    }
    function dropAsk() {
      if (!S.asking) return;
      S.asking = false;
      discardEl.hidden = true;
    }
    // viaButton: the X was clicked, so the choice is put in front of the person (focus on "Keep picking") instead of waiting for another Esc.
    function requestClose(viaButton) {
      if (pendingCount() === 0) { close(false); return; }
      if (!S.asking) showAsk();
      else if (!viaButton) { close(false); return; }
      if (viaButton) keepBtn.focus();
    }

    // ------------------------------------------------ events
    function focusables() {
      return Array.from(dialog.querySelectorAll("button, input, select, textarea, a[href], [tabindex]"))
        .filter((el) => !el.disabled && el.tabIndex >= 0 && visible(el));
    }

    function onDocKey(e) {
      if (S.closed) return;
      if (e.key === "Escape") {
        if (e.isComposing || e.keyCode === 229) return;                      // Esc that cancels an IME composition is not "close"
        e.preventDefault(); e.stopPropagation();                             // stopped here so the dock behind does not close too
        if (e.target === searchInput && searchInput.value !== "") { clearSearch(); say("Search cleared."); return; }   // the first Esc empties the box (what a search box does anyway)
        requestClose(false);
        return;
      }
      if (e.key !== "Tab") return;
      const list = focusables();
      if (!list.length) { e.preventDefault(); dialog.focus(); return; }
      const first = list[0], last = list[list.length - 1], active = document.activeElement;
      if (!dialog.contains(active) || active === dialog) { e.preventDefault(); (e.shiftKey ? last : first).focus(); }
      else if (e.shiftKey && active === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && active === last) { e.preventDefault(); first.focus(); }
    }

    function onFocusIn(e) {
      if (S.closed || dialog.contains(e.target)) return;
      const list = focusables();
      (list[0] || dialog).focus();                                           // focus that escapes (a script, a click-through) comes straight back
    }

    function switchTab(key) {
      if (key === S.tab) return;
      flushSearch();
      S.tab = key;
      refresh();
    }

    function moveFocus(tr, delta) {
      let rows = Array.from(tbody.querySelectorAll("tr.segb-row"));
      const n = rows.indexOf(tr) + delta;
      if (n < 0) { searchInput.focus(); return; }
      while (n >= rows.length && S.shown < S.view.length) { drawMore(false); rows = Array.from(tbody.querySelectorAll("tr.segb-row")); }
      const next = rows[Math.min(n, rows.length - 1)];
      if (next) next.focus();
    }

    closeBtn.addEventListener("click", () => requestClose(true));
    cancelBtn.addEventListener("click", () => close(false));
    discardBtn.addEventListener("click", () => close(false));
    keepBtn.addEventListener("click", () => { dropAsk(); stable().focus(); });
    applyBtn.addEventListener("click", () => { if (!applyBtn.disabled) close(true); });
    clearBtn.addEventListener("click", clearAll);
    allBox.addEventListener("click", toggleAll);

    // The second click of a double-click on the opener lands on the dialog that the first one just opened: on the backdrop (it would close the
    // dialog) or on a row (it would tick it). A press that is the 2nd or 3rd of a multi-click, soon after opening, whose FIRST press was not in
    // the dialog (there was no dialog yet: it was the opener) belongs to the opener: it, and the mouseup and click that follow, are dropped.
    // A double-click that starts inside the dialog is the person's own and goes through.
    const openedAt = Date.now();
    let startedHere = false, dropping = false;
    const dropOpenersRepeat = (e) => {
      if (e.type === "mousedown") {
        if (e.detail <= 1) { startedHere = true; dropping = false; }
        else dropping = !startedHere && Date.now() - openedAt < OPEN_GUARD_MS;
      }
      if (!dropping) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      if (e.type === "click") dropping = false;
    };
    ["mousedown", "mouseup", "click"].forEach((type) => overlay.addEventListener(type, dropOpenersRepeat, true));

    let downOnBackdrop = false;
    overlay.addEventListener("mousedown", (e) => { downOnBackdrop = e.target === overlay; });
    overlay.addEventListener("click", (e) => { if (e.target === overlay && downOnBackdrop && pendingCount() === 0) close(false); });

    searchInput.addEventListener("input", () => {
      searchClear.hidden = searchInput.value === "";
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => { searchTimer = null; applyQuery(); }, SEARCH_DELAY_MS);
    });
    searchClear.addEventListener("click", () => { clearSearch(); });
    searchInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); flushSearch(); }
      else if (e.key === "ArrowDown") {
        flushSearch();
        const row = tbody.querySelector("tr.segb-row[tabindex='0']") || tbody.querySelector("tr.segb-row");
        if (row) { e.preventDefault(); row.focus(); }
      }
    });

    headRow.addEventListener("click", (e) => {
      const b = e.target.closest(".segb-sort");
      if (!b) return;
      flushSearch();
      const col = columns.find((c) => c.key === b.dataset.key);
      if (S.sort.key === col.key) S.sort.dir = S.sort.dir === "asc" ? "desc" : "asc";
      else S.sort = { key: col.key, dir: col.numeric ? "desc" : "asc" };         // a number column starts with the biggest
      refresh();
      say("Sorted by " + col.label + ", " + (S.sort.dir === "asc" ? "ascending" : "descending") + ".");
    });

    // A drag across a row's text (to copy a name) ends in a click on that row: that is a selection, not a tick. The gesture counts as one when
    // the pointer moved between press and release, or when it left new text selected. A double-click on a word is two plain clicks, which tick
    // and untick again, so it leaves the row as it was; a click on text that was ALREADY selected before the press just ticks. The checkbox and
    // the keyboard (detail 0) always tick.
    const selectedText = () => { const sel = window.getSelection ? window.getSelection() : null; return sel && !sel.isCollapsed ? String(sel).trim() : ""; };
    let downAt = null;
    tbody.addEventListener("mousedown", (e) => { downAt = { x: e.clientX, y: e.clientY, text: selectedText() }; });
    function wasSelecting(e, tr) {
      if (e.detail === 0 || e.target.closest(".segb-check")) return false;
      if (downAt && Math.abs(e.clientX - downAt.x) + Math.abs(e.clientY - downAt.y) > 4) return true;
      if (e.detail > 1) return false;
      const text = selectedText(), sel = window.getSelection();
      return text !== "" && (!downAt || text !== downAt.text) && tr.contains(sel.anchorNode);
    }
    tbody.addEventListener("click", (e) => {
      if (e.target.closest(".segb-more-row")) return;
      const tr = e.target.closest("tr.segb-row");
      if (tr && !wasSelecting(e, tr)) toggleRow(tr);
    });
    tbody.addEventListener("keydown", (e) => {
      const tr = e.target.closest("tr.segb-row");
      if (!tr) return;
      const onBox = e.target.tagName === "INPUT";
      if (e.key === " " || e.key === "Spacebar") { if (!onBox) { e.preventDefault(); toggleRow(tr); } }   // on the box itself the browser ticks it, then our click handler runs
      else if (e.key === "Enter") { e.preventDefault(); toggleRow(tr); }
      else if (e.key === "ArrowDown") { e.preventDefault(); moveFocus(tr, 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); moveFocus(tr, -1); }
      else if (e.key === "PageDown") { e.preventDefault(); moveFocus(tr, 10); }
      else if (e.key === "PageUp") { e.preventDefault(); moveFocus(tr, -10); }
      else if (e.key === "Home") { e.preventDefault(); const f = tbody.querySelector("tr.segb-row"); if (f) f.focus(); }
      else if (e.key === "End") { e.preventDefault(); moveFocus(tr, 100000); }
    });
    tbody.addEventListener("focusin", (e) => {
      const tr = e.target.closest && e.target.closest("tr.segb-row");
      if (!tr) return;
      tbody.querySelectorAll("tr.segb-row[tabindex='0']").forEach((r) => { if (r !== tr) r.tabIndex = -1; });
      tr.tabIndex = 0;
    });
    moreBtn.addEventListener("click", (e) => drawMore(e.detail === 0));
    let scrollQueued = false;
    scroller.addEventListener("scroll", () => {
      if (scrollQueued) return;
      scrollQueued = true;
      (window.requestAnimationFrame || setTimeout)(() => { scrollQueued = false; onScroll(); });
    });
    stateEl.addEventListener("click", (e) => {
      if (e.target.closest(".segb-retry")) {
        const en = entry();
        en.status = "idle";
        refresh();
      } else if (e.target.closest(".segb-clear-search")) clearSearch();
    });

    tabButtons.forEach((btn, n) => {
      btn.addEventListener("click", () => switchTab(tabs[n].key));
      btn.addEventListener("keydown", (e) => {
        let to = null;
        if (e.key === "ArrowRight") to = (n + 1) % tabs.length;
        else if (e.key === "ArrowLeft") to = (n - 1 + tabs.length) % tabs.length;
        else if (e.key === "Home") to = 0;
        else if (e.key === "End") to = tabs.length - 1;
        if (to === null) return;
        e.preventDefault();
        switchTab(tabs[to].key);
        tabButtons[to].focus();
      });
    });

    // ------------------------------------------------ go
    const handle = { close() { close(false); } };
    current = handle;
    lockPageScroll();
    document.body.append(overlay);
    document.addEventListener("keydown", onDocKey, true);
    document.addEventListener("focusin", onFocusIn, true);
    window.addEventListener("resize", onResize);
    refresh();
    (phone() ? dialog : searchInput).focus();
    return handle;
  }

  const api = {
    open,
    close() { if (current) current.close(); },
    isOpen() { return !!current; },
    // Pure helpers, exposed so tests/test_segment_browser_frontend.py can run them without a browser.
    _pure: {
      fold, naturalKey, compareKeys, compareNatural, comparator, sortedIndices, sortItems, sortKey, searchWords, haystack, filterPredicate,
      highlightRanges, compact, formatSize, sizeTitle, categoryOf, pathText, cellText, diffSelection, applyText, singular, defaultColumns, echoQuery,
    },
  };
  if (typeof window !== "undefined") window.SegmentBrowser = api;
})();
