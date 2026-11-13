/* ============================================================================
 * DOOH screen list - the "Upload screen list" control on the DOOH line of Step 05 (the avails card).
 *
 * The planner picks the platform's inventory export (.csv or .xlsx). The server (POST /api/dooh/screenlist, app/services/dooh_screenlist.py)
 * reads it, stores the rows, and answers with a small reference: file name, how many screens were read and skipped, and the two totals -
 * the sum of every screen's max avails and max spend, worked out by the very functions that fill the export's helper columns. This file
 * does NO arithmetic of its own on them: it shows what the server returned, and puts the same two numbers into the line's Max Recommended
 * Monthly Imps / Spend (state.availsData[lid].max_imps / max_spend; whole numbers, like those fields always are).
 *
 * The reference (never the rows) is kept in state.availsData[lid].dooh_screenlist, so it travels with autosave, reopen and Duplicate like any
 * other avails value. The line stays editable: once the planner changes either number, "Recalculate from list" puts the list's totals back.
 *
 * Loaded before app.js; app.js's renderAvailsGrid() asks for DoohScreenlist.blockHtml(line, entry) on every DOOH line and the clicks are
 * handled here, once, at the document level, so a re-render of the card never loses a handler. All dynamic text (file name, the
 * server's messages, the skipped-row notes) goes through escapeHtml.
 * ========================================================================== */
(function () {
  "use strict";

  const ENDPOINT = "/api/dooh/screenlist";
  const transient = {};              // lid -> { busy, error, notes[] }: what the last upload said, kept across the card's re-renders

  const esc = (s) => (typeof escapeHtml === "function" ? escapeHtml(s) : String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c])));
  const cssEscape = (s) => (window.CSS && CSS.escape ? CSS.escape(String(s)) : String(s).replace(/["\\]/g, "\\$&"));
  const int = (n) => Number(n || 0).toLocaleString("en-US", { maximumFractionDigits: 0 });
  const money = (n) => "$" + Number(n || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  // The two numbers the line holds after a fill: the totals, as the whole numbers the Step 05 fields hold.
  const filled = (ref) => ({ imps: Math.round(ref.total_impressions), spend: Math.round(ref.total_spend) });

  function edited(entry, ref) {
    const f = filled(ref);
    return entry.max_imps !== f.imps || entry.max_spend !== f.spend;
  }

  function blockHtml(li, entry) {
    const lid = li && li.id;
    const ref = entry && entry.dooh_screenlist;
    const t = transient[lid] || {};
    const id = esc(lid);
    const input = `<input type="file" class="dooh-list-file" hidden data-dooh-file data-lid="${id}" accept=".csv,.xlsx,.xls,.txt,.tsv,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" aria-label="Choose the DOOH screen list file" />`;
    const busy = t.busy ? " disabled" : "";
    let body;
    if (!ref) {
      body = `
        <p class="dooh-list-hint">Upload the platform's inventory export (.csv or .xlsx). It fills the impressions and spend above, and the DOOH tabs of the export come out complete: nothing to paste and nothing to refresh in Excel.</p>
        <div class="dooh-list-actions">
          <button type="button" class="btn-secondary dooh-btn" data-dooh-action="pick" data-lid="${id}"${busy}>Upload screen list</button>
        </div>`;
    } else {
      const notes = [];
      if (ref.screens < ref.minimum_screens) notes.push(`Only ${int(ref.screens)} screens: the DOOH Summary asks for a minimum of ${int(ref.minimum_screens)}.`);
      (t.notes || []).forEach((n) => notes.push(n));
      body = `
        <p class="dooh-list-source">From the uploaded screen list (<strong>${int(ref.screens)} screens</strong>) <span class="dooh-list-file-name" title="${esc(ref.filename)}">${esc(ref.filename)}</span></p>
        <dl class="dooh-list-facts">
          <div><dt>Screens read</dt><dd>${int(ref.screens)}</dd></div>
          <div><dt>Skipped</dt><dd>${int(ref.skipped)}</dd></div>
          <div><dt>Total impressions</dt><dd>${int(ref.total_impressions)}</dd></div>
          <div><dt>Total spend</dt><dd>${money(ref.total_spend)}</dd></div>
        </dl>
        <p class="dooh-list-fine">Four-week totals of every screen, worked out exactly as the DOOH Summary in the export does it; not scaled to the flight.</p>
        ${ref.skipped ? `<p class="dooh-list-skipped">${esc(skippedLine(ref))}</p>` : ""}
        ${notes.map((n) => `<p class="dooh-list-note">${esc(n)}</p>`).join("")}
        ${edited(entry, ref) ? `<p class="dooh-list-note dooh-list-edited">The impressions or spend above no longer match the list.</p>` : ""}
        <div class="dooh-list-actions">
          <button type="button" class="btn-secondary dooh-btn" data-dooh-action="pick" data-lid="${id}"${busy}>Replace file</button>
          ${edited(entry, ref) ? `<button type="button" class="btn-secondary dooh-btn" data-dooh-action="recalc" data-lid="${id}"${busy}>Recalculate from list</button>` : ""}
          <button type="button" class="dooh-btn dooh-btn-quiet" data-dooh-action="remove" data-lid="${id}"${busy}>Remove list</button>
        </div>`;
    }
    return `
      <div class="dooh-list" data-dooh-block data-lid="${id}">
        <h4 class="dooh-list-title">DOOH screen list</h4>
        ${body}
        ${input}
        <p class="dooh-list-status" role="status" aria-live="polite">${t.busy ? "Reading the file&hellip;" : ""}</p>
        <p class="dooh-list-error" role="alert">${t.error ? esc(t.error) : ""}</p>
      </div>`;
  }

  function skippedLine(ref) {
    const shown = (ref.skipped_examples || []).join("; ");
    const more = ref.skipped > (ref.skipped_examples || []).length ? " and more" : "";
    return `Skipped ${int(ref.skipped)} ${ref.skipped === 1 ? "row" : "rows"} that couldn't be used${shown ? ` (${shown}${more})` : ""}.`;
  }

  // Put the list's two totals into the line's avails, as the numbers the planner would have typed (not estimates; no formula behind them).
  function applyTotals(lid, ref) {
    const entry = (state.availsData[lid] = state.availsData[lid] || {});
    const f = filled(ref);
    entry.freeform = false;                              // numbers, not free text
    entry.max_imps = f.imps;
    entry.max_spend = f.spend;
    entry.max_imps_estimated = false;
    entry.max_spend_estimated = false;
    delete entry.basis;                                  // both figures stand as they are: no live formula may re-derive the spend from the imps
    delete entry.win_rate;                               // not a Constanza figure any more
    delete entry.hispanic_share;
    entry.dooh_screenlist = ref;
  }

  function rerender(lid) {
    if (typeof renderAvailsGrid === "function") renderAvailsGrid();
    const again = document.querySelector(`[data-dooh-action="pick"][data-lid="${cssEscape(lid)}"]`);
    if (again) again.focus({ preventScroll: true });
  }

  function paint(lid) {                                  // refresh just this block (no state changed)
    const block = document.querySelector(`[data-dooh-block][data-lid="${cssEscape(lid)}"]`);
    const li = state.lineItems.find((x) => x.id === lid);
    if (!block || !li) return;
    const holder = document.createElement("div");
    holder.innerHTML = blockHtml(li, state.availsData[lid] || {});
    block.replaceWith(holder.firstElementChild);
  }

  function errorText(data, res) {
    if (data && typeof data.detail === "string" && data.detail) return data.detail;
    if (res && res.status === 413) return "That file is too large. Filter the screen list (for example to the markets in this proposal) and upload it again.";
    return "The screen list could not be read just now. Try again in a moment.";
  }

  async function upload(lid, file) {
    transient[lid] = { busy: true };
    paint(lid);
    try {
      const res = await fetch(ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream", "X-Filename": encodeURIComponent(file.name) },
        body: file,
      });
      let data = null;
      try { data = await res.json(); } catch (e) { data = null; }
      if (!res.ok || !data || !data.screenlist) {
        transient[lid] = { error: errorText(data, res) };
        paint(lid);
        return;
      }
      const notes = [];
      if (data.sheet) notes.push(`Read from the sheet "${data.sheet}".`);
      if (data.ignored_column_count) {
        const names = (data.ignored_columns || []).join(", ");
        notes.push(`${int(data.ignored_column_count)} column${data.ignored_column_count === 1 ? "" : "s"} this tool doesn't use ${data.ignored_column_count === 1 ? "was" : "were"} left out${names ? ` (${names}${data.ignored_column_count > (data.ignored_columns || []).length ? ", and more" : ""})` : ""}.`);
      }
      if (data.blanked_cells) notes.push(`${int(data.blanked_cells)} cell${data.blanked_cells === 1 ? "" : "s"} in the other columns weren't the kind of value the column holds and were left empty.`);
      applyTotals(lid, data.screenlist);
      transient[lid] = { notes };
      rerender(lid);
    } catch (e) {
      transient[lid] = { error: "Couldn't reach the server. Check your connection and try again." };
      paint(lid);
    }
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest && event.target.closest("[data-dooh-action]");
    if (!button) return;
    const lid = button.dataset.lid;
    const action = button.dataset.doohAction;
    if (action === "pick") {
      const input = document.querySelector(`[data-dooh-file][data-lid="${cssEscape(lid)}"]`);
      if (input) input.click();
    } else if (action === "recalc") {
      const ref = (state.availsData[lid] || {}).dooh_screenlist;
      if (!ref) return;
      applyTotals(lid, ref);
      transient[lid] = {};
      rerender(lid);
    } else if (action === "remove") {
      if (state.availsData[lid]) delete state.availsData[lid].dooh_screenlist;     // the two numbers stay as they are: they are the planner's now
      transient[lid] = {};
      rerender(lid);
    }
  });

  document.addEventListener("change", (event) => {
    const input = event.target;
    if (!input || !input.matches || !input.matches("[data-dooh-file]")) return;
    const file = input.files && input.files[0];
    input.value = "";                                    // the same file can be chosen again
    if (file) upload(input.dataset.lid, file);
  });

  window.DoohScreenlist = { blockHtml, applyTotals, upload };
})();
