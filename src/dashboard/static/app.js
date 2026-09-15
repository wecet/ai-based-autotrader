"use strict";
const $ = id => document.getElementById(id);
const amountFields = ["minimum_order_notional", "maximum_order_notional"];
const exposureFields = ["maximum_total_exposure", "maximum_sector_exposure", "maximum_position"];
const integerFields = ["max_daily_trades", "top_n"];
let key = "", saved = null, revision = "", rows = [], dirty = false, busy = false, conflict = false;
let polling = false;
let models = [], unavailableModels = [], draftModelId = "";

function message(text, error = false) {
  $("notice").textContent = text;
  $("notice").className = error ? "notice error" : "notice";
  $("notice").hidden = !text;
}
function markDirty() {
  dirty = true;
  updateButtons();
}
function updateButtons() {
  $("save").disabled = !dirty || busy || conflict || !models.some(model => model.id === draftModelId);
  $("discard").disabled = (!dirty && !conflict) || busy;
  $("save-label").textContent = busy ? "Saving your settings…" : conflict ? "A newer version is available" : dirty ? "● Unsaved changes" : "● All changes saved";
}
async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {Authorization: "Bearer " + key, ...(options.headers || {})},
    cache: "no-store",
    signal: AbortSignal.timeout(15000),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    if (response.status === 409) { conflict = true; updateButtons(); }
    const details = (data.issues || []).map(issue => issue.field.replace("settings.", "") + ": " + issue.message);
    throw new Error(details.length ? details.join("\n") : data.detail || "Unable to reach the dashboard. Try again.");
  }
  return response;
}
function validateRows(entries) {
  if (entries.length < 2 || entries.length > 500) throw new Error("Your universe must contain 2–500 candidates.");
  const result = Object.create(null);
  for (const row of entries) {
    const symbol = row.symbol.trim().toUpperCase();
    const proxy = row.proxy.trim().toUpperCase();
    if (!/^[A-Z][A-Z0-9.-]{0,14}$/.test(symbol) || !/^[A-Z][A-Z0-9.-]{0,14}$/.test(proxy)) throw new Error("Each row needs a valid candidate and benchmark ticker.");
    if (Object.hasOwn(result, symbol)) throw new Error("Duplicate candidate: " + symbol);
    result[symbol] = proxy;
  }
  return result;
}
function formSettings() {
  if (!models.some(model => model.id === draftModelId)) throw new Error("Select an available strategy before saving.");
  const data = {...saved, model_id: draftModelId, universe: validateRows(rows), trading_enabled: $("trading_enabled").checked, feed: $("feed").value};
  for (const field of [...integerFields, ...amountFields]) data[field] = $(field).value === "" ? null : Number($(field).value);
  for (const field of exposureFields) data[field] = Number($(field).value) / 100;
  if (data.maximum_order_notional !== null && data.maximum_order_notional < data.minimum_order_notional) throw new Error("Maximum order must be at least the minimum order.");
  if (!(data.maximum_position <= data.maximum_sector_exposure && data.maximum_sector_exposure <= data.maximum_total_exposure)) throw new Error("Position exposure must not exceed benchmark group or total exposure.");
  return data;
}
function renderTable(focusLast = false) {
  const query = $("search").value.trim().toUpperCase();
  const body = $("universe-rows"); body.replaceChildren();
  let visible = 0;
  rows.forEach((row, index) => {
    if (query && !(row.symbol + " " + row.proxy).toUpperCase().includes(query)) return;
    visible++;
    const tr = document.createElement("tr");
    for (const field of ["symbol", "proxy"]) {
      const cell = document.createElement("td"); const input = document.createElement("input");
      input.value = row[field]; input.className = "ticker-input"; input.maxLength = 15;
      input.autocomplete = "off"; input.spellcheck = false;
      input.setAttribute("aria-label", (field === "symbol" ? "Candidate" : "Benchmark") + " in row " + (index + 1));
      input.addEventListener("input", () => { row[field] = input.value; markDirty(); });
      cell.append(input); tr.append(cell);
    }
    const cell = document.createElement("td"); const button = document.createElement("button");
    button.type = "button"; button.className = "remove"; button.textContent = "×";
    button.setAttribute("aria-label", "Remove " + (row.symbol || "empty candidate"));
    button.addEventListener("click", () => { rows.splice(index, 1); markDirty(); renderTable(); });
    cell.append(button); tr.append(cell); body.append(tr);
  });
  $("table-count").textContent = query ? visible + " of " + rows.length + " candidates" : rows.length + " candidates";
  $("no-results").hidden = visible !== 0;
  if (focusLast) body.lastElementChild?.querySelector("input")?.focus();
}
function renderModels() {
  const container = $("model-options"); container.replaceChildren();
  for (const model of models) {
    const label = document.createElement("label"); label.className = "model-option";
    const check = document.createElement("input"); check.type = "checkbox";
    check.checked = model.id === draftModelId; check.name = "active-model"; check.value = model.id;
    const content = document.createElement("span"); const heading = document.createElement("strong");
    heading.textContent = model.name;
    const description = document.createElement("span"); description.className = "model-description";
    description.textContent = model.description;
    const source = document.createElement("small"); source.textContent = model.file;
    content.append(heading, description, source); label.append(check, content); container.append(label);
    check.addEventListener("change", () => {
      // Exactly one active strategy: use the pause control to disable orders.
      draftModelId = model.id;
      for (const option of container.querySelectorAll("input")) option.checked = option.value === draftModelId;
      markDirty();
    });
  }
  const issues = unavailableModels.map(item => item.file + ": " + item.reason);
  if (!models.some(model => model.id === draftModelId)) issues.unshift("The saved model is unavailable. Select an available strategy.");
  $("model-errors").textContent = issues.join("\n"); $("model-errors").hidden = issues.length === 0;
}
function renderStatus(status) {
  const applied = models.find(model => model.id === status.applied_model_id);
  const selected = models.find(model => model.id === saved.model_id);
  $("model-current").textContent = status.settings_state === "applied"
    ? "Applied strategy: " + (applied?.name || selected?.name || saved.model_id)
    : "Saved strategy: " + (selected?.name || saved.model_id) + " · waiting for trader";
  $("health-label").textContent = status.health === "healthy" ? "●  Trader heartbeat healthy" : "○  Trader not reporting healthy";
  const enabled = saved.trading_enabled && saved.max_daily_trades > 0;
  $("mode-metric").textContent = enabled ? "Enabled" : "Paused";
  $("mode-caption").textContent = status.settings_state === "applied" ? "Current applied setting" : "Saved · waiting to apply";
  $("attempts-metric").textContent = status.attempts_today ?? "—";
  $("limit-metric").textContent = " / " + saved.max_daily_trades;
  $("day-caption").textContent = status.trading_day + " · New York";
  $("universe-metric").textContent = Object.keys(saved.universe).length;
  $("sync-metric").textContent = status.settings_state === "applied" ? "Applied" : "Pending";
  $("sync-caption").textContent = status.settings_state === "applied" ? "Trader picked up this version" : "Applies before the next cycle";
}
function fillForm(data) {
  saved = data.settings; revision = data.revision; draftModelId = saved.model_id;
  if (Array.isArray(data.models)) models = data.models;
  if (Array.isArray(data.unavailable)) unavailableModels = data.unavailable;
  renderModels();
  $("trading_enabled").checked = saved.trading_enabled; $("feed").value = saved.feed;
  for (const field of [...integerFields, ...amountFields]) $(field).value = saved[field] ?? "";
  for (const field of exposureFields) $(field).value = Number((saved[field] * 100).toFixed(6));
  rows = Object.entries(saved.universe).map(([symbol, proxy]) => ({symbol, proxy}));
  dirty = false; conflict = false; renderTable(); renderStatus(data.status); updateButtons();
}
async function load() { fillForm(await (await api("/api/settings")).json()); }
$("unlock-form").addEventListener("submit", async event => {
  event.preventDefault(); key = $("access-key").value.trim();
  const button = event.currentTarget.querySelector("button"); button.disabled = true;
  $("unlock-error").textContent = "";
  try {
    await load(); $("unlock-panel").hidden = true; $("workspace").hidden = false; $("lock").hidden = false;
    $("access-key").value = "";
  } catch (error) { key = ""; $("unlock-error").textContent = error.message; }
  finally { button.disabled = false; }
});
$("lock").addEventListener("click", () => {
  if (dirty && !window.confirm("Discard your unsaved changes and lock the dashboard?")) return;
  key = ""; saved = null; rows = []; dirty = false;
  $("universe-rows").replaceChildren(); $("settings-form").reset();
  $("workspace").hidden = true; $("lock").hidden = true; $("unlock-panel").hidden = false;
  $("access-key").focus();
});
$("settings-form").addEventListener("input", markDirty);
$("settings-form").addEventListener("change", markDirty);
$("search").addEventListener("input", event => { event.stopPropagation(); renderTable(); });
$("search").addEventListener("change", event => event.stopPropagation());
$("add-row").addEventListener("click", () => {
  if (rows.length >= 500) { message("The universe supports up to 500 candidates.", true); return; }
  $("search").value = ""; rows.push({symbol: "", proxy: "SPY"}); markDirty(); renderTable(true);
});
$("settings-form").addEventListener("submit", async event => {
  event.preventDefault(); if (busy || conflict) return;
  try {
    const settings = formSettings(); busy = true; updateButtons(); $("settings-form").inert = true;
    const response = await api("/api/settings", {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({revision, settings})});
    fillForm(await response.json()); message("Settings saved. The trader will apply this version before its next cycle. Today’s order count is preserved.");
  } catch (error) { message(error.message, true); }
  finally { busy = false; $("settings-form").inert = false; updateButtons(); }
});
$("discard").addEventListener("click", async () => {
  try { await load(); message("Loaded the latest saved settings."); } catch (error) { message(error.message, true); }
});
$("bulk-open").addEventListener("click", () => {
  try { $("bulk-json").value = JSON.stringify(validateRows(rows), null, 2); }
  catch { $("bulk-json").value = JSON.stringify(Object.fromEntries(rows.filter(r => r.symbol).map(r => [r.symbol, r.proxy])), null, 2); }
  $("bulk-error").textContent = ""; $("bulk-dialog").showModal();
});
$("bulk-close").addEventListener("click", () => $("bulk-dialog").close());
$("bulk-form").addEventListener("submit", event => {
  event.preventDefault();
  try {
    const text = $("bulk-json").value; const data = JSON.parse(text);
    if (!data || Array.isArray(data) || typeof data !== "object" || !Object.values(data).every(value => typeof value === "string")) throw new Error("Use a JSON object of ticker-to-benchmark strings.");
    // Detect repeated JSON keys before JSON.parse's last-value-wins behavior can hide them.
    const keys = [...text.matchAll(/"((?:[^"\\]|\\.)*)"\s*:/g)].map(match => JSON.parse('"' + match[1] + '"'));
    if (new Set(keys).size !== keys.length) throw new Error("Remove duplicate keys from your JSON.");
    const entries = Object.entries(data).map(([symbol, proxy]) => ({symbol, proxy}));
    validateRows(entries); rows = entries; $("search").value = ""; renderTable(); markDirty(); $("bulk-dialog").close();
  } catch (error) { $("bulk-error").textContent = error.message; }
});
$("export").addEventListener("click", async () => {
  try {
    const blob = await (await api("/api/export")).blob(); const url = URL.createObjectURL(blob);
    const link = document.createElement("a"); link.href = url; link.download = "runtime.env"; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { message(error.message, true); }
});
window.addEventListener("beforeunload", event => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });
setInterval(async () => {
  if (!key || !saved || busy || polling) return;
  polling = true;
  try {
    const data = await (await api("/api/status")).json();
    if (!key) return;
    if (data.revision !== revision) {
      if (dirty) { conflict = true; updateButtons(); message("Settings changed in another session. Discard your draft to load the latest version.", true); }
      else await load();
    } else renderStatus(data.status);
  } catch { $("health-label").textContent = "○  Dashboard connection interrupted"; }
  finally { polling = false; }
}, 5000);
