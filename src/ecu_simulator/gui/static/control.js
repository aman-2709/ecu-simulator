"use strict";
const el = id => document.getElementById(id);
let run = {state: "Connecting", id: null, endpoints: []};
let selected = "", original = "", sending = false, editing = false, online = false;
let revision = 0;

async function api(path, body) {
  const opts = body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)};
  const response = await fetch(path, {...opts, cache: "no-store"});
  const data = await response.json();
  if (!response.ok) throw Error(data.error || response.statusText);
  return data;
}
function controls() {
  el("start").disabled = !online || editing || !["Stopped", "Failed"].includes(run.state);
  el("stop").disabled = !online || !["Running", "Starting"].includes(run.state);
  el("send").disabled = !online || sending || run.state !== "Running" || !el("endpoint").value;
}
function showRun(next) {
  if (next.id !== run.id) el("result").hidden = true;
  if (next.id !== run.id || next.endpoints.join() !== run.endpoints.join()) {
    el("endpoint").replaceChildren(...next.endpoints.map(name => new Option(name, name)));
  }
  run = next;
  online = true;
  el("state").textContent = run.state;
  el("active").textContent = ["Starting","Running","Stopping"].includes(run.state) ? run.interface + " · " + run.id.slice(0, 8) : "";
  if (run.error) el("error").textContent = run.error;
  controls();
}
async function refresh() {
  const current = ++revision;
  try {
    const data = await api("/run");
    if (current === revision) showRun(data);
  } catch (error) {
    if (current !== revision) return;
    online = false;
    el("state").textContent = "Disconnected";
    controls();
  }
}
function action(fn) {
  return async event => {
    event.preventDefault();
    el("error").textContent = el("notice").textContent = "";
    try { await fn(); }
    catch (error) { await refresh(); el("error").textContent = error.message; }
  };
}
function busyEditor(busy) {
  editing = busy;
  for (const id of ["draft","profile","interface","name"]) el(id).disabled = busy;
  el("save-form").querySelector("button").disabled = busy;
  controls();
}
async function catalog() {
  const data = await api("/profiles");
  el("profile").replaceChildren();
  for (const [group, title] of [["shipped","Shipped profiles"],["user","My profiles"]]) {
    const options = document.createElement("optgroup");
    options.label = title;
    for (const name of data[group]) options.append(new Option(name, group + "/" + encodeURIComponent(name)));
    el("profile").append(options);
  }
  el("interface").replaceChildren(new Option("Use YAML setting", ""), ...data.interfaces.map(name => new Option(name,name)));
}
async function loadProfile(value) {
  const data = await api("/profiles/" + value);
  original = el("draft").value = data.text;
  el("interface").value = [...el("interface").options].some(o => o.value === data.interface) ? data.interface : "";
  selected = el("profile").value = value;
}
el("profile").onchange = action(async () => {
  const next = el("profile").value;
  if (original !== el("draft").value && !confirm("Discard the edits to this working copy?")) {
    el("profile").value = selected;
    return;
  }
  busyEditor(true);
  try { await loadProfile(next); } finally { el("profile").value = selected; busyEditor(false); }
});
el("interface").onchange = action(async () => {
  const iface = el("interface").value;
  if (!iface) return;
  busyEditor(true);
  try { el("draft").value = (await api("/draft/interface",{text: el("draft").value, interface: iface})).text; }
  catch (error) { el("interface").value = ""; throw error; }
  finally { busyEditor(false); }
});
el("draft").oninput = () => { el("interface").value = ""; };
el("save-form").onsubmit = action(async () => {
  busyEditor(true);
  const name = el("name").value;
  try {
    await api("/profiles",{name, text: el("draft").value});
    await catalog();
    await loadProfile("user/" + encodeURIComponent(name));
    el("notice").textContent = "Saved " + name;
  } finally { busyEditor(false); }
});
el("start").onclick = action(async () => {
  el("start").disabled = true;
  const next = await api("/run",{text: el("draft").value});
  ++revision;
  showRun(next);
});
el("stop").onclick = action(async () => {
  const id = run.id;
  el("stop").disabled = true;
  await api("/run/" + id + "/stop",{});
  await refresh();
});
el("send-form").onsubmit = action(async () => {
  const id = run.id;
  sending = true;
  controls();
  try {
    const data = await api("/run/" + id + "/send",{endpoint: el("endpoint").value, payload: el("payload").value});
    if (run.id !== data.id) return;
    el("request").textContent = "TX  " + data.request;
    el("raw").textContent = data.raw ? "RX  " + data.raw : "";
    el("decoded").textContent = data.message;
    el("records").replaceChildren(...data.records.map(text => {
      const li = document.createElement("li");
      li.textContent = text;
      return li;
    }));
    el("result").hidden = false;
  } finally { sending = false; controls(); }
});
async function init() {
  busyEditor(true);
  try { await catalog(); await loadProfile("shipped/ice_default"); }
  catch (error) { el("error").textContent = error.message; }
  finally { busyEditor(false); }
  while (true) { await refresh(); await new Promise(resolve => setTimeout(resolve, 350)); }
}
init();
