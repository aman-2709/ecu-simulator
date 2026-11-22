"use strict";
const el = id => document.getElementById(id);
let run = {state: "Connecting", id: null, endpoints: []};
let selected = "", original = "", sending = false, editing = false, online = false;
let revision = 0, broadcastPending = false, dbcAvailable = false;

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
  const broadcast = el("broadcast-toggle");
  broadcast.hidden = run.state !== "Running" || !run.restbus;
  broadcast.disabled = broadcast.hidden || !online || broadcastPending;
  broadcast.textContent = run.restbus?.paused ? "Resume broadcast" : "Pause broadcast";
  el("send").disabled = !online || sending || run.state !== "Running" || !el("endpoint").value || !el("payload").validity.valid;
}
function showRun(next) {
  if (next.id !== run.id) {
    el("exchanges").replaceChildren();
    el("result").hidden = true;
  }
  if (next.id !== run.id || next.endpoints.join() !== run.endpoints.join()) {
    el("endpoint").replaceChildren(...next.endpoints.map(name => new Option(name, name)));
  }
  run = next;
  window.dispatchEvent(new Event("run-update"));
  online = true;
  el("state").textContent = run.state;
  el("active").textContent = ["Starting","Running","Stopping"].includes(run.state) ? run.interface + " · " + run.id.slice(0, 8) : "";
  el("run-error").textContent = run.error;
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
  el("dbc-browse").disabled = busy;
  el("dbc-file").disabled = busy;
  el("dbc-editor").disabled = busy;
  for (const id of ["draft","profile","interface","name"]) el(id).disabled = busy;
  el("save-form").querySelector("button").disabled = busy;
  controls();
}
async function catalog() {
  const data = await api("/profiles");
  dbcAvailable = Boolean(data.dbc_import) && typeof dbcEditor !== "undefined";
  el("dbc-heading").parentElement.hidden = !dbcAvailable;
  el("live-dbc-file").closest("details").hidden = !dbcAvailable;
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
  if (dbcAvailable) await dbcEditor.load(data.dbc);
  original = el("draft").value = data.text;
  if (dbcAvailable) dbcEditor.refreshPreview();
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
  busyEditor(true);
  try {
    const text = el("draft").value;
    const review = dbcAvailable && await api("/dbc/review", {text});
    const body = {text};
    if (review?.required) {
      if (!await dbcEditor.confirmReview(review)) return;
      body.review = review.token;
    }
    const next = await api("/run", body);
    ++revision;
    showRun(next);
  } finally { busyEditor(false); }
});
el("broadcast-toggle").onclick = action(async () => {
  const id = run.id;
  broadcastPending = true;
  controls();
  try {
    const next = await api("/run/" + id + "/broadcast", {paused: !run.restbus.paused});
    if (run.id === id) { ++revision; showRun(next); }
  } finally { broadcastPending = false; controls(); }
});
el("stop").onclick = action(async () => {
  const id = run.id;
  el("stop").disabled = true;
  await api("/run/" + id + "/stop",{});
  await refresh();
});
// Each preset contains the complete UDS request, including any prerequisite service SID.
const services = [
  ["10", "Diagnostic session control", [
    ["10 01", "Default session"],
    ["10 02", "Programming session"],
    ["10 03", "Extended session"],
    ["10 04", "Safety session"],
    ["10 81", "Default with suppression"],
    ["10 83", "Extended with suppression"],
    ["10 99", "Unsupported session 19 with suppression · NRC 12"],
  ], "Session types depend on the profile. Bit 7 requests suppression of a positive reply."],
  ["11", "ECU reset", [
    ["11 01", "Hard reset"],
    ["11 02", "Ignition cycle reset"],
    ["11 03", "Soft reset"],
    ["11 04", "Enable rapid shutdown"],
    ["11 05", "Disable rapid shutdown"],
    ["11 81", "Hard reset with suppression"],
    ["11 83", "Soft reset with suppression"],
    ["11 00", "Reserved reset type"],
  ], "Reset types 01–03 return the simulated ECU to its default session. Type 04 reports a power-down time."],
  ["14", "Clear diagnostic information", [
    ["14 FF FF FF", "Clear all DTCs"],
    ["14 00 00 00", "Group 000000"],
    ["14 C0 00 00", "Group C00000"],
    ["14 B0 00 00", "Group B00000"],
    ["14 D0 00 00", "Group D00000"],
    ["14 00 01 01", "P0001, failure type 01 · NRC 31"],
    ["14 94 77 01", "B1477, failure type 01 · NRC 31"],
    ["14 FF FF", "Missing group byte · NRC 13"],
  ], "FF FF FF selects all DTCs. The bench rejects other group or individual DTC selectors with NRC 31."],
  ["19", "Read DTC information", [
    ["19 01 FF", "Count DTCs"],
    ["19 02 FF", "Read DTCs"],
    ["19 02 08", "Confirmed DTCs"],
    ["19 02 04", "Pending DTCs"],
    ["19 02 01", "Failed DTCs"],
    ["19 04 00 01 01 01", "P0001 snapshot record 01"],
    ["19 06 00 01 01 01", "P0001 extended record 01"],
    ["19 0A", "Supported DTCs"],
    ["19 0B", "First failed DTC"],
    ["19 0D", "Last failed DTC"],
    ["19 07 80 FF", "Count by severity mask"],
    ["19 08 80 FF", "List by severity mask"],
  ], "The bench implements report types 01, 02, 04, 06 and 0A. Select uds_records_demo for the P0001 stored records. Other report examples exercise NRC 12. Status bits 04 and 08 select pending and confirmed DTCs."],
  ["22", "Read data by identifier", [
    ["22 F1 90", "VIN"],
    ["22 F1 86", "Active session"],
    ["22 F1 91", "Manufacturer ECU hardware number"],
    ["22 F1 94", "Supplier ECU software number"],
    ["22 F1 95", "Supplier ECU software version"],
    ["22 F1 8B", "ECU manufacturing date"],
    ["22 F1 8C", "ECU serial number"],
    ["22 F1 88", "Manufacturer ECU software number"],
    ["22 F1 89", "Manufacturer ECU software version"],
    ["22 F1 87", "Manufacturer spare part number"],
    ["22 F1 90 F1 94", "VIN and software identifiers"],
    ["22 F1 90 F1 86", "VIN and active session"],
  ], "F190 and F186 are built in. Other DIDs require profile data. A multi-DID response includes the identifiers the ECU can serve."],
  ["23", "Read memory by address", [
    ["23 24 00 10 00 00 00 04", "Read four bytes"],
    ["23 24 00 10 00 00 00 08", "Read eight bytes"],
    ["23 24 00 10 00 00 00 10", "Read sixteen bytes"],
    ["23 24 00 00 10 00 00 04", "Four bytes at 0x1000"],
    ["23 24 00 20 00 00 00 04", "Four bytes at 0x200000"],
    ["23 13 10 00 00 04", "Three-byte address"],
    ["23 24 00 00 00 00 00 04", "Unmapped address zero · NRC 31"],
  ], "Bench RAM starts at 0x1000, 0x100000 and 0x200000. Format 24 specifies four address bytes followed by two size bytes."],
  ["27", "Security access", [
    ["10 03", "1. Enter extended session"],
    ["27 01", "2. Request level 1 seed"],
    ["27 02 B7 91 F3 DD", "3. Send bench level 1 key"],
    ["27 03", "Level 2 seed · unsupported on bench"],
    ["27 04 00 00 00 00", "Level 2 test key · unsupported on bench"],
    ["27 05", "Level 3 seed · unsupported on bench"],
    ["27 06 00 00 00 00", "Level 3 test key · unsupported on bench"],
    ["27 02 DE AD BE EF", "Incorrect key after seed · NRC 35"],
    ["27 02 00 00 00 00", "Send before seed · NRC 24"],
    ["27 81", "Request seed with suppression"],
    ["27 82 B7 91 F3 DD", "Send bench key with suppression"],
  ], "The bench uses a deterministic level 1 fixture in extended session. Request a fresh seed before sending its key. Levels 2 and 3 exercise NRC 12 here."],
  ["28", "Communication control", [
    ["28 00 01", "Enable normal RX and TX"],
    ["28 01 01", "Enable RX only"],
    ["28 02 01", "Enable TX only"],
    ["28 03 01", "Disable normal RX and TX"],
    ["28 00 03", "Enable normal and network traffic"],
    ["28 03 03", "Disable normal and network traffic"],
    ["28 80 01", "Enable with suppression"],
    ["28 81 01", "RX only with suppression"],
  ], "The first byte controls receive and transmit directions. The second selects normal traffic (01) or normal plus network-management traffic (03). Diagnostic replies stay enabled."],
  ["2A", "Read data by periodic identifier", [
    ["2A 01 01", "RPM · slow"],
    ["2A 02 01", "RPM · medium"],
    ["2A 03 02", "Vehicle speed · fast"],
    ["2A 01 03", "Coolant temperature · slow"],
    ["2A 04 01", "Stop RPM reports"],
    ["2A 04 02", "Stop vehicle speed reports"],
    ["2A 04", "Stop all periodic IDs"],
  ], "The bench assigns periodic IDs 01, 02 and 03 to RPM, speed and coolant. Actual vehicle assignments and supported rates depend on the ECU."],
  ["2E", "Write data by identifier", [
    ["2E F1 90 31 4D 38 47 44 4D 39 41 58 4B 50 30 34 32 37 38 38", "17-character VIN · read-only on bench"],
    ["2E F1 00 01", "Manufacturer DID F100 · example value 01"],
    ["2E F1 A2 0A", "Write 0A to the writable-DID test fixture"],
    ["2E F1 8C 53 49 4D 31", "ECU serial number · example ASCII SIM1"],
    ["2E F1 98 54 30 30 31", "Tester serial / repair shop code · example ASCII T001"],
  ], "A write needs a writable DID, its exact data length and an allowed session. The shipped profile has no writable DIDs. VIN uses 17 ASCII bytes; the other data records depend on the ECU."],
  ["2F", "Input/output control by identifier", [
    ["2F D1 00 03 01", "Turn bench lamp on"],
    ["2F D1 00 00", "Return lamp to ECU control"],
  ], "Select uds_records_demo and enter extended session (10 03). Read 22 D1 00 to observe the lamp state."],
  ["31", "Routine control", [
    ["31 01 01 00", "Start routine 0100"],
    ["31 03 01 00", "Results for 0100"],
    ["31 01 01 01", "Start routine 0101"],
    ["31 01 02 03", "Start routine 0203"],
    ["31 01 0F 01", "Start routine 0F01"],
    ["31 02 0F 01", "Stop routine 0F01"],
    ["31 03 0F 01", "Results for 0F01"],
    ["31 01 0F 02", "Start routine 0F02"],
    ["31 01 0F 03", "Start routine 0F03"],
    ["31 01 FF 00", "Erase bench RAM at 0x1000"],
  ], "Routine identifiers and results depend on the ECU. These routines use bench state; FF00 erases the simulated region at 0x1000."],
  ["34", "Request download", [
    ["34 00 44 00 00 10 00 00 00 00 04", "Download four bytes to bench RAM"],
    ["34 00 44 00 10 00 00 00 00 04 00", "Alternate memory region"],
    ["34 00 33 00 10 00 00 02 00", "Three-byte address and size"],
    ["34 10 44 00 00 10 00 00 00 10 00", "Compression test · NRC 31"],
    ["34 01 44 00 00 10 00 00 00 10 00", "Encryption test · NRC 31"],
  ], "Download sends data to ECU memory. Start with the four-byte bench transfer, send the first data block under 36, then finish under 37. The bench accepts plain format 00."],
  ["35", "Request upload", [
    ["35 00 44 00 00 10 00 00 00 00 04", "Upload four bytes from bench RAM"],
    ["35 00 44 00 10 00 00 00 00 04 00", "Alternate memory region"],
    ["35 00 33 00 10 00 00 02 00", "Three-byte address and size"],
    ["35 10 44 00 00 10 00 00 00 10 00", "Compression test · NRC 31"],
  ], "Upload reads from ECU memory. After the request, use the counter-only block under 36, then finish under 37. The bench accepts plain format 00."],
  ["36", "Transfer data", [
    ["36 01 AA BB CC DD", "First data block"],
    ["36 02 EE FF 00 11", "Second data block"],
    ["36 03 22 33 44 55", "Third data block"],
    ["36 01 10 20 30 40 50 60 70 80", "Eight-byte block"],
    ["36 01", "Upload block 01"],
    ["36 05 AA BB CC DD", "Out-of-order block"],
  ], "Open a download or upload first. The block counter starts at 01. A download carries data; the bench upload request carries only the counter."],
  ["37", "Request transfer exit", [
    ["37", "Finish transfer"],
    ["37 55 B4 01 A7", "Verify CRC32 of AA BB CC DD"],
    ["37 00", "One-byte exit record"],
  ], "Finish after the full byte count is transferred. The optional CRC32 example matches a four-byte bench download of AA BB CC DD."],
  ["3E", "Tester present", [
    ["3E 00", "Tester Present"],
    ["3E 80", "Suppress positive response"],
    ["3E 01", "Reserved subfunction"],
  ], "00 requests a reply. 80 requests positive-response suppression. The reserved subfunction exercises NRC 12."],
  ["3D", "Write memory by address", [
    ["3D 24 00 10 00 00 00 04 AA BB CC DD", "Four-byte write"],
    ["3D 24 00 10 00 00 00 02 12 34", "Two-byte write"],
    ["3D 24 00 20 00 00 00 04 55 66 77 88", "Write four bytes at 0x200000"],
    ["3D 24 00 00 10 00 00 04 99 88 77 66", "Write four bytes at 0x1000"],
    ["3D 13 10 00 00 02 AA BB", "Three-byte address"],
  ], "Writes affect only the simulated RAM regions. The size field must match the number of supplied data bytes."],
  ["83", "Access timing parameters", [
    ["83 01", "Read extended timing set"],
    ["83 02", "Restore timing defaults"],
    ["83 03", "Read current timing"],
    ["83 04 00 32 01 F4", "Set P2 to 50 ms and P2* to 5 s"],
    ["83 83", "Read timing with suppression"],
  ], "The bench models a four-byte timing record. 00 32 01 F4 represents P2 of 50 ms and P2* of 5 s in this model."],
  ["85", "Control DTC setting", [
    ["85 01", "Enable DTC updates"],
    ["85 02", "Disable DTC updates"],
    ["85 81", "Enable with suppression"],
    ["85 82", "Disable with suppression"],
    ["85 03", "Reserved setting"],
  ], "01 resumes DTC updates and 02 suspends them. Reading or explicitly clearing stored faults remains available."],
];
const splitServices = new Set(["10", "11", "19", "27", "28", "2A", "31", "3E", "83", "85"]);
const serviceSelect = el("service");
serviceSelect.add(new Option("Raw request (full payload)", ""));
for (const [sid, name] of services) serviceSelect.add(new Option("0x" + sid + " · " + name, sid));

function requestBytes() {
  return [serviceSelect.value, el("sub-byte").value, el("payload").value.trim()].filter(Boolean).join(" ");
}

function updatePreview() {
  const text = requestBytes();
  const valid = el("sub-byte").validity.valid && /^(?:[0-9a-f]{2}\s*)+$/i.test(text);
  el("payload").setCustomValidity(valid ? "" : "Enter complete hexadecimal byte pairs.");
  el("payload").setAttribute("aria-invalid", String(Boolean(text) && !valid));
  el("byte-error").hidden = !text || valid;
  el("preview").textContent = valid ? text.match(/[0-9a-f]{2}/gi).join(" ").toUpperCase() : "—";
  controls();
}

function selectService() {
  const previous = el("preview").textContent;
  const sid = serviceSelect.value;
  const selected = services.find(service => service[0] === sid);
  const samples = selected?.[2] || [];
  el("sub-field").hidden = !splitServices.has(sid);
  el("sub-byte").disabled = !splitServices.has(sid);
  if (!splitServices.has(sid)) el("sub-byte").value = "";
  el("sub-label").textContent = sid === "2A" ? "Transmission mode (optional)" : "Sub-Function (Optional)";
  el("payload").placeholder = sid ? "e.g., F1 90 or 00 01 02 03" : "e.g., 19 02 FF";
  el("sample-note").textContent = selected?.[3] || "";
  el("presets").replaceChildren();
  el("samples").hidden = !sid;
  el("data-label").textContent = sid ? "Data (Hex bytes, space-separated)" : "Request bytes (hex)";
  for (const [request, name] of samples) {
    const button = document.createElement("button");
    button.type = "button";
    const code = document.createElement("code");
    code.textContent = request;
    button.append(code, name);
    button.onclick = () => {
      const sampleSid = request.slice(0, 2);
      if (sampleSid !== serviceSelect.value) {
        serviceSelect.value = sampleSid;
        selectService();
      }
      const split = splitServices.has(sampleSid);
      el("sub-byte").value = split ? request.slice(3, 5) : "";
      el("payload").value = request.slice(split ? 6 : 3);
      updatePreview();
    };
    el("presets").append(button);
  }
  if (!sid) el("payload").value = previous === "—" ? "" : previous;
  else if (previousService === "") el("payload").value = "";
  previousService = sid;
  updatePreview();
}
let previousService = "";
serviceSelect.value = "";
serviceSelect.onchange = selectService;
el("payload").oninput = updatePreview;
el("sub-byte").oninput = updatePreview;
selectService();

function recordExchange(time, endpoint, request, outcome, raw, records) {
  const entry = el("exchange-template").content.firstElementChild.cloneNode(true);
  entry.querySelector(".exchange-meta").textContent = time.toISOString().slice(11, 23) + " UTC · " + endpoint;
  entry.querySelector(".request").textContent = "Request  " + request;
  entry.querySelector(".outcome").textContent = outcome;
  if (raw) entry.querySelector(".raw").textContent = "RX  " + raw;
  for (const record of records) {
    const item = document.createElement("li");
    item.textContent = record;
    entry.querySelector(".records").append(item);
  }
  const history = el("exchanges");
  if (history.firstElementChild) history.firstElementChild.open = false;
  history.prepend(entry);
  el("result").hidden = false;
  history.scrollTop = 0;
}

el("send-form").onsubmit = action(async () => {
  const id = run.id;
  const endpoint = el("endpoint").value;
  const payload = requestBytes();
  const time = new Date();
  sending = true;
  controls();
  try {
    const data = await api("/run/" + id + "/send", {endpoint, payload});
    if (run.id === data.id) {
      recordExchange(time, endpoint, data.request, data.message || "Response received", data.raw, data.records);
    }
  } catch (error) {
    if (run.id === id) recordExchange(time, endpoint, payload, "Request failed · " + error.message, "", []);
    throw error;
  } finally { sending = false; controls(); }
});
async function init() {
  busyEditor(true);
  try { await catalog(); await loadProfile("shipped/ice_default"); }
  catch (error) { el("error").textContent = error.message; }
  finally { busyEditor(false); }
  while (true) { await refresh(); await new Promise(resolve => setTimeout(resolve, 350)); }
}
window.addEventListener("DOMContentLoaded", init);
