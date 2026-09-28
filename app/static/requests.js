const ui = { status: "Active", search: "", page: 1, total: 0, rows: [], selected: null, detail: null, isLead: false, viewerId: null, users: [], labels: {} };
const byId = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[ch]);
const fmtDate = value => value ? new Date(`${String(value).slice(0,10)}T12:00:00`).toLocaleDateString(undefined,{month:"short",day:"numeric",year:"numeric"}) : "—";
const statusClass = value => ({New:"New",Progress:"Progress",Paused:"Paused",Reviewing:"Reviewing",Done:"Done",Canceled:"Canceled"})[value] || "";
const activeStatuses = ["New","Progress","Paused","Reviewing"];

async function api(path, options) {
  const response = await fetch(path, options);
  if (response.status === 401) { location.assign("/login?next=%2Frequests"); throw new Error("Session expired"); }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
  return data;
}

async function loadList() {
  byId("request-rows").innerHTML = '<tr><td colspan="9" class="empty">Loading requests…</td></tr>';
  try {
    const q = new URLSearchParams({status:ui.status,search:ui.search,page:String(ui.page)});
    const data = await api(`/api/requests?${q}`);
    ui.rows = data.requests;
    ui.total = data.total;
    ui.isLead = data.is_lead;
    ui.viewerId = data.viewer_id;
    renderRows();
  } catch (error) {
    byId("request-rows").innerHTML = `<tr><td colspan="9" class="empty error">${esc(error.message)}</td></tr>`;
  }
}

function renderRows() {
  const tbody = byId("request-rows");
  tbody.innerHTML = ui.rows.length ? ui.rows.map(r => `
    <tr data-id="${esc(r.id)}" tabindex="0" class="${r.id === ui.selected ? "active" : ""}">
      <td><span class="badge ${statusClass(r.status)}">${esc(r.status)}</span></td>
      <td class="code">${esc(r.code)}</td>
      <td title="${esc(r.client_name)}">${esc(r.client_name || "—")}</td>
      <td title="${esc(r.request_type)}">${esc(r.request_type)}</td>
      <td title="${esc(r.seller_name)}">${esc(r.seller_name)}</td>
      <td title="${esc(r.market)}">${esc(r.market)}</td>
      <td title="${esc(r.owner_email || "Unassigned")}">${esc(r.owner_email || "Unassigned")}</td>
      <td>${esc(fmtDate(r.due_date))}</td>
      <td><span class="priority ${esc(r.priority)}">${esc(r.priority)}</span></td>
    </tr>`).join("") : '<tr><td colspan="9" class="empty">No requests match this view.</td></tr>';
  tbody.querySelectorAll("tr[data-id]").forEach(row => {
    row.addEventListener("click", () => selectRequest(row.dataset.id));
    row.addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectRequest(row.dataset.id); } });
  });
  byId("count").textContent = `${ui.total} request${ui.total === 1 ? "" : "s"}`;
  byId("page").textContent = `Page ${ui.page}`;
  byId("prev").disabled = ui.page <= 1;
  byId("next").disabled = ui.page * 50 >= ui.total;
}

async function selectRequest(id) {
  ui.selected = id;
  ui.detail = null;
  renderRows();
  byId("inspector").innerHTML = '<div class="inspector-empty">Loading details…</div>';
  try {
    ui.detail = await api(`/api/requests/${encodeURIComponent(id)}`);
    renderDetail();
  } catch (error) {
    byId("inspector").innerHTML = `<p class="error">${esc(error.message)}</p>`;
  }
}

function formAnswers(details) {
  return Object.entries(details?.answers || {}).filter(([,value]) => value !== null && value !== "" && value !== false && !(Array.isArray(value) && !value.length));
}

function allowedNext(row) {
  if (!ui.isLead && row.owner_id !== ui.viewerId) return [];
  const choices = {New:["Progress","Paused"],Progress:["Paused","Reviewing"],Paused:["Progress"],Reviewing:["Progress"],Done:[],Canceled:[]};
  const next = [...(choices[row.status] || [])];
  if (ui.isLead && row.status === "Reviewing") next.push("Done");
  if (ui.isLead && activeStatuses.includes(row.status)) next.push("Canceled");
  return next;
}

function renderDetail() {
  const r = ui.detail;
  if (!r) return;
  const builderEligible = r.status === "Progress" && (ui.isLead || r.owner_id === ui.viewerId) && ["Proposal Page Only (No Avails Needed)","Avails / Estimates Only (I don't need a proposal right now)","Proposal Page With Avails","Full Presentation","Renewal Proposal Request"].includes(r.request_type);
  const next = allowedNext(r);
  const ownerOptions = ui.users.map(u => `<option value="${esc(u.id)}" ${u.id === r.owner_id ? "selected" : ""}>${esc(u.email)}</option>`).join("");
  const details = formAnswers(r.details);
  byId("inspector").innerHTML = `
    <div class="inspector-head"><span class="code">${esc(r.code)}</span><span class="badge ${statusClass(r.status)}">${esc(r.status)}</span></div>
    <h2>${esc(r.client_name || r.seller_name)}</h2><p class="type">${esc(r.request_type)}</p>
    ${builderEligible ? `<a class="primary" style="display:block;text-align:center" href="/?request=${encodeURIComponent(r.id)}">Open in proposal builder ↗</a>` : ""}
    <dl class="detail-grid">
      <div><dt>Seller</dt><dd>${esc(r.seller_name)}<br><span class="muted">${esc(r.seller_email)}</span></dd></div>
      <div><dt>Due date</dt><dd>${esc(fmtDate(r.due_date))}</dd></div>
      <div><dt>Market</dt><dd>${esc(r.market)}</dd></div>
      <div><dt>Priority</dt><dd class="priority ${esc(r.priority)}">${esc(r.priority)}</dd></div>
      <div><dt>Owner</dt><dd>${esc(r.owner_email || "Unassigned")}</dd></div>
      <div><dt>Monthly budget</dt><dd>${r.monthly_budget === null ? "—" : esc(Number(r.monthly_budget).toLocaleString(undefined,{style:"currency",currency:"USD"}))}${r.details?.needs_budget_review ? ' <span class="warning">Confirm amount</span>' : ""}</dd></div>
    </dl>
    ${r.review_link && /^https?:\/\//i.test(r.review_link) ? `<section><h3>Seller review</h3><a class="proposal-link" href="${esc(r.review_link)}" target="_blank" rel="noopener noreferrer">Open delivered plan ↗</a></section>` : ""}
    <section><h3>Manage request</h3><div class="actions">
      <div><label for="status-change">Status</label><select id="status-change"><option value="">Choose…</option>${next.map(s => `<option>${esc(s)}</option>`).join("")}</select></div>
      ${ui.isLead ? `<div><label for="priority-change">Priority</label><select id="priority-change">${["Untriaged","Low","Medium","High","Critical"].map(p => `<option ${p === r.priority ? "selected" : ""}>${p}</option>`).join("")}</select></div>
      <div><label for="owner-change">Owner</label><select id="owner-change"><option value="">Choose…</option>${ownerOptions}</select></div>` : ""}
    </div><p id="action-error" class="error" role="alert"></p></section>
    <section><h3>Proposal work</h3>${r.proposals.length ? r.proposals.map(p => `<a class="proposal-link" href="/?reopen=${encodeURIComponent(p.proposal_id)}">${esc(p.proposal_title || p.proposal_id)} · ${esc(p.status)}</a>`).join("") : '<span class="muted">No proposal drafts linked yet.</span>'}</section>
    <section class="source-details"><h3>Submitted details</h3><div class="kv"><dt>Submission ID</dt><dd>${esc(r.submission_id || "—")}</dd><dt>Source</dt><dd>Fillout intake</dd></div><details><summary>View form answers (${details.length})</summary>${details.map(([key,value]) => `<div class="answer"><strong>${esc(ui.labels[key] || key)}</strong>${esc(Array.isArray(value) ? value.join(", ") : typeof value === "object" ? JSON.stringify(value) : value)}</div>`).join("")}</details></section>
    <section class="activity"><h3>Activity</h3>${r.activity.length ? r.activity.map(a => `<div class="activity-item">${esc(a.actor_email)} · ${esc(a.action.replaceAll("_"," "))}${a.note ? `<div>${esc(a.note)}</div>` : ""}<small>${esc(new Date(a.created_at).toLocaleString())}</small></div>`).join("") : '<span class="muted">No activity yet.</span>'}</section>`;
  byId("status-change").addEventListener("change", async e => { if (e.target.value) await change("status",e.target.value); });
  if (ui.isLead) {
    byId("priority-change").addEventListener("change", async e => { if (e.target.value !== r.priority) await change("priority",e.target.value); });
    byId("owner-change").addEventListener("change", async e => { if (e.target.value) await change("owner_id",e.target.value); });
  }
}

async function change(field, value) {
  const body = {version:ui.detail.version,[field]:value};
  if (field === "status" && value === "Reviewing") {
    const link = prompt("Paste the seller delivery link for this plan:");
    if (link) body.review_link = link;
    else {
      const note = prompt("Describe how the answer or plan was delivered to the seller:");
      if (!note?.trim()) { renderDetail(); return; }
      body.note = note.trim();
    }
  }
  if (field === "status" && value === "Canceled") {
    const note = prompt("Why was this request canceled?");
    if (!note?.trim()) { renderDetail(); return; }
    body.note = note.trim();
  }
  try {
    await api(`/api/requests/${encodeURIComponent(ui.selected)}`, {method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    await loadList();
    await selectRequest(ui.selected);
  } catch (error) {
    byId("action-error").textContent = error.message;
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  try { ui.labels = await (await fetch("/static/fillout-labels.json")).json(); } catch (_) { /* IDs remain visible */ }
  byId("search").addEventListener("input", event => {
    clearTimeout(ui.searchTimer);
    ui.searchTimer = setTimeout(() => { ui.search = event.target.value.trim(); ui.page = 1; loadList(); }, 250);
  });
  document.querySelectorAll(".tabs button").forEach(button => button.addEventListener("click", () => {
    document.querySelector(".tabs .selected")?.classList.remove("selected");
    button.classList.add("selected");
    ui.status = button.dataset.status;
    ui.page = 1;
    loadList();
  }));
  byId("prev").addEventListener("click", () => { ui.page--; loadList(); });
  byId("next").addEventListener("click", () => { ui.page++; loadList(); });
  await loadList();
  if (ui.isLead) {
    try { ui.users = (await api("/api/requests/users/assignable")).users; } catch (_) { /* Queue remains usable */ }
  }
});
