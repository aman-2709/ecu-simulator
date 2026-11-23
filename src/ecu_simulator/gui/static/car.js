"use strict";
// The Car panel: choose a car, check what it will send, play it and watch it on the bus.
// It edits the working copy (#draft) through /car/draft, starts a run through /run as Start ECU does, and reads the
// run state from control.js's run-update event. It keeps no state shared with live.js or dbc.js.
(() => {
  const $ = id => document.getElementById(id);
  const draft = $("draft");
  const banner = $("car-banner");
  const FIRST_ROWS = 25;
  const MAX_LOG_BYTES = 16 * 1024 * 1024;
  const PERIOD_SOURCES = {override: "set by you", log: "from log", dbc: "from DBC",
    table: "opendbc safety table, approximate", assumed: "assumed"};
  const CHIPS = [["valid", "valid"], ["counter_only", "counter only"], ["user_rule", "valid (user rule)"],
    ["none", "no counter or checksum"], ["skipped", "skipped"]];
  const SHORT_INTEGRITY = {valid: "valid", counter_only: "counter only", user_rule: "valid (user rule)",
    none: "no counter or checksum"};
  const RULES = {toyota: "Toyota", honda: "Honda", hyundai_can: "Hyundai (classic CAN)"};
  const HYUNDAI_NOTE = "Hyundai classic CAN: “valid” means the counter and checksum bytes equal opendbc's packer " +
    "for the signals it names. They are not checked by opendbc's parser or against a real car, and a given model " +
    "may not use these fields.";

  let catalog = null;
  let checked = null;       // the last /car/check answer for the working copy
  let phase = "choose";     // choose, check, ready, running
  let stepOneOpen = false;
  let showAll = false;
  let editing = "";
  let busy = false;
  let version = 0;
  let ruleFile = null;      // {name, sha256, rules} of the rule file this panel uploaded
  let active = null;        // {kind: "run" | "replay", id, interface, source, started, name}
  let live = null;          // the capture session of the active run
  let lastLive = null;      // the session whose rows stay on screen after its run ended
  let statusNote = "";      // how the last run ended
  let checkedVersion = 0;
  let tableKey = "";
  let ticker = null;

  const source = () => $("car-source-recorded").checked ? "recorded" : "generated";
  const hex = m => "0x" + Number(m.id).toString(16).toUpperCase().padStart(m.extended ? 8 : 3, "0");
  const kbit = bits => bits == null ? "an unknown bitrate" : (bits / 1000).toLocaleString("en") + " kbit/s";

  // --- the banner: the cause first, the next step second --------------------------------------------------------------

  function problem(cause, next) {
    const previous = banner.textContent;
    const first = document.createElement("p");
    first.className = "cause";
    first.textContent = cause;
    const second = document.createElement("p");
    second.className = "next";
    second.textContent = "Next: " + next;
    banner.replaceChildren(first, second);
    if (banner.textContent !== previous) banner.focus();
  }
  function clearProblem() { banner.replaceChildren(); }

  function failure(error, iface) {
    const text = error || "the run ended without giving a reason";
    const sending = /^sending |short .* frame write/i.test(text);
    if (/No buffer space/i.test(text)) {
      return ["Send failed: the transmit queue of " + iface + " is full (" + text + ").",
        "check that the bus is connected and terminated, that another node acknowledges frames and that the " +
        "bitrate matches, or lengthen the heaviest periods, then press Play again."];
    }
    if (/Network is down/i.test(text)) {
      return [(sending ? "Send failed: " : "") + iface + " is down (" + text + ").",
        "bring it up with: sudo ip link set up " + iface + ", then press Play again."];
    }
    if (/No such device|does not exist|not found/i.test(text)) {
      return ["The interface " + iface + " is missing (" + text + ").",
        "connect the adapter or choose another interface in step 3, then press Play again."];
    }
    if (/bus.?off/i.test(text)) {
      return ["The interface " + iface + " went bus-off (" + text + ").",
        "check the wiring, termination and bitrate, restart the interface, then press Play again."];
    }
    if (/encod/i.test(text)) {
      return ["A message could not be encoded, so the run stopped (" + text + ").",
        "change the message's period or upload a corrected DBC, then press Play again."];
    }
    return [(sending ? "Send failed: " : "The run failed: ") + text,
      "fix the cause above, then press Play again."];
  }

  function refusal(load) {
    const over = load.over.map(item => item.name + " " + (item.share * 100).toFixed(1) + "%").join(", ");
    problem("Play is refused: " + load.refused + (over ? " (largest shares: " + over + ")" : ""),
      "lengthen the periods of the heaviest messages or use an interface with a higher bitrate, then press Play " +
      "again.");
  }

  // --- requests --------------------------------------------------------------------------------------------------------

  async function upload(path, file) {
    const response = await fetch(path, {method: "POST", headers: {"Content-Type": "application/octet-stream"},
      body: file});
    if (response.status === 413) throw Error(file.name + " is larger than the 16 MiB the server accepts");
    const data = await response.json();
    if (!response.ok) throw Error(data.error || response.statusText);
    return data;
  }
  async function redraft(selection) {
    draft.value = (await api("/car/draft", {text: draft.value, ...selection})).text;
  }
  async function recheck() {
    const mine = ++version;
    phase = "check";
    render();
    try {
      const result = await api("/car/check", {text: draft.value});
      if (mine !== version) return false;
      checked = result;
      ++checkedVersion;
      phase = active ? "running" : "ready";
      stepOneOpen = false;
      if (result.bus_load.refused && source() === "generated") refusal(result.bus_load);
      render();
      return true;
    } catch (error) {
      if (mine !== version) return false;
      checked = null;
      ++checkedVersion;
      problem("The car could not be checked: " + error.message,
        "choose another car or DBC, or correct the working copy, then try again.");
      render();
      return false;
    }
  }
  async function step(work, cause, next) {
    busy = true;
    clearProblem();
    render();
    try { await work(); }
    catch (error) { problem(cause + error.message, next); }
    finally { busy = false; render(); }
  }

  // --- step 1: choose --------------------------------------------------------------------------------------------------

  function fillBrands() {
    const brands = Object.keys(catalog.brands);
    $("car-brand").replaceChildren(new Option("Choose a brand", ""), ...brands.map(brand =>
      new Option(brand[0].toUpperCase() + brand.slice(1), brand)));
  }
  function platformsOf(brand) { return catalog?.brands[brand] || []; }
  function fillPlatforms(brand, value) {
    const options = platformsOf(brand).map(p => new Option(p.names.join(" / "), p.id));
    $("car-platform").replaceChildren(new Option(brand ? "Choose a platform" : "Choose a brand first", ""),
      ...options);
    $("car-platform").value = value || "";
  }
  function fillBuses(platform, value) {
    const buses = platform?.buses || [];
    $("car-bus").replaceChildren(...buses.map(bus => new Option(bus, bus)));
    $("car-bus").value = value || buses[0] || "";
  }
  function findPlatform(id) {
    for (const [brand, platforms] of Object.entries(catalog?.brands || {})) {
      const platform = platforms.find(p => p.id === id);
      if (platform) return [brand, platform];
    }
    return ["", null];
  }
  function chooseCar(selection) {
    ruleFile = null;
    $("car-rule").value = "";
    $("car-timing-status").textContent = "";
    showAll = false;
    editing = "";
    return step(async () => {
      await redraft({...selection, periods: {}, log_periods: {}, rule: null, rule_file: null});
      await recheck();
    }, "The car could not be chosen: ", "choose another car or DBC file and try again.");
  }
  $("car-brand").onchange = () => {
    fillPlatforms($("car-brand").value, "");
    fillBuses(null);
    render();
  };
  $("car-platform").onchange = () => {
    const [, platform] = findPlatform($("car-platform").value);
    fillBuses(platform);
    if (platform) chooseCar({platform: platform.id, bus: $("car-bus").value});
  };
  $("car-bus").onchange = () => {
    if ($("car-platform").value) chooseCar({platform: $("car-platform").value, bus: $("car-bus").value});
  };
  $("car-upload").onchange = async event => {
    const file = event.target.files[0];
    if (!file) return;
    await step(async () => {
      const data = await upload("/dbc/import?filename=" + encodeURIComponent(file.name), file);
      $("car-brand").value = "";
      fillPlatforms("", "");
      fillBuses(null);
      ruleFile = null;
      $("car-rule").value = "";
      await redraft({sha256: data.sha256, filename: file.name, periods: {}, log_periods: {}, rule: null,
        rule_file: null});
      await recheck();
    }, "The DBC could not be used: ", "choose a valid .dbc file and upload it again.");
    event.target.value = "";
  };
  for (const id of ["car-source-generated", "car-source-recorded"]) {
    $(id).onchange = () => {
      editing = "";
      clearProblem();
      if (checked?.bus_load.refused && source() === "generated") refusal(checked.bus_load);
      render();
    };
  }
  $("car-log-file").onchange = render;
  $("car-timing-log").onchange = async event => {
    const file = event.target.files[0];
    if (!file || !checked) return;
    await step(async () => {
      const result = await upload("/car/log?sha256=" + encodeURIComponent(checked.dbc.sha256), file);
      await redraft({log_periods: result.periods});
      await recheck();
      const notes = [result.matched + " message periods measured from " + file.name];
      if (result.clamped.length) notes.push("clamped to 10 to 60000 ms: " + result.clamped.join(", "));
      const unmatched = result.unmatched_ids.length + result.unmatched_more;
      if (unmatched) notes.push(unmatched + " identifiers in the log are not in this DBC");
      $("car-timing-status").textContent = notes.join(" · ");
    }, "The timing log could not be read: ", "choose a candump log of this car's bus.");
    event.target.value = "";
  };
  $("car-change-1").onclick = () => { stepOneOpen = !stepOneOpen; render(); };

  // --- step 2: check ---------------------------------------------------------------------------------------------------

  $("car-rule").onchange = () => step(async () => {
    await redraft({rule: $("car-rule").value || null});
    await recheck();
  }, "The rule could not be set: ", "choose another rule.");
  $("car-rule-file").onchange = async event => {
    const file = event.target.files[0];
    if (!file) return;
    await step(async () => {
      const result = await upload("/car/rule?filename=" + encodeURIComponent(file.name), file);
      await redraft({rule_file: result.sha256});
      ruleFile = {name: file.name, ...result};
      await recheck();
    }, "The rule file could not be used: ", "correct the rule file at the place the message names and upload it " +
      "again.");
    event.target.value = "";
  };
  $("car-rule-clear").onclick = () => step(async () => {
    await redraft({rule_file: null});
    ruleFile = null;
    await recheck();
  }, "The rule file could not be removed: ", "try again.");
  $("car-table-more").onclick = () => { showAll = !showAll; render(); };

  function overrides() {
    return Object.fromEntries(checked.messages.filter(m => m.period_source === "override")
      .map(m => [m.name, m.period_ms]));
  }
  function savePeriod(name, value) {
    const periods = overrides();
    if (value === "") delete periods[name];
    else periods[name] = Number(value);
    return step(async () => {
      await redraft({periods});
      editing = "";
      await recheck();
    }, "The period could not be set: ", "enter whole milliseconds from 10 to 60000.");
  }

  function integrityText(m) {
    if (source() === "recorded") return "as recorded";
    if (m.skipped) return "skipped: " + m.skipped;
    if (m.integrity === "counter_only") return "counter only: " + (m.detail || "checksum not computed");
    if (m.integrity === "user_rule") return "valid (user rule): " + (m.detail || "computed as the supplied rule says");
    const label = SHORT_INTEGRITY[m.integrity] || m.integrity;
    return m.detail ? label + ": " + m.detail : label;
  }
  function periodCell(cell, m) {
    const locked = source() === "recorded" || Boolean(m.skipped) || busy || phase === "running";
    if (editing === m.name && !locked) {
      const form = document.createElement("form");
      form.className = "period-edit";
      const label = document.createElement("label");
      label.textContent = "Period of " + m.name + " in ms";
      label.className = "visually-hidden";
      const input = document.createElement("input");
      input.type = "number";
      input.min = "10";
      input.max = "60000";
      input.step = "1";
      input.value = String(m.period_ms);
      input.id = "car-period-input";
      label.htmlFor = input.id;
      const save = document.createElement("button");
      save.textContent = "Save";
      const cancel = document.createElement("button");
      cancel.type = "button";
      cancel.textContent = "Cancel";
      cancel.onclick = () => { editing = ""; render(); };
      form.onsubmit = event => {
        event.preventDefault();
        if (input.value !== "" && !input.reportValidity()) return;
        savePeriod(m.name, input.value);
      };
      form.append(label, input, save, cancel);
      cell.append(form);
      queueMicrotask(() => input.focus());
      return;
    }
    const value = document.createElement("span");
    value.className = "period-value";
    value.textContent = m.period_ms + " ms";
    const chip = document.createElement("span");
    chip.className = "chip period-source";
    chip.dataset.source = m.period_source;
    chip.textContent = (PERIOD_SOURCES[m.period_source] || m.period_source) + (m.clamped ? ", clamped" : "");
    const edit = document.createElement("button");
    edit.type = "button";
    edit.textContent = "Edit";
    edit.setAttribute("aria-label", "Edit period of " + m.name);
    edit.disabled = locked;
    edit.onclick = () => { editing = m.name; render(); };
    cell.append(value, " ", chip, " ", edit);
  }
  function renderTable() {
    // Rebuilt only when what it shows changes, so a status poll never replaces a period being typed.
    const key = JSON.stringify([checkedVersion, showAll, editing, source(), busy, phase === "running"]);
    if (key === tableKey) return;
    tableKey = key;
    const rows = $("car-table-rows");
    const messages = checked?.messages || [];
    const shown = showAll ? messages : messages.slice(0, FIRST_ROWS);
    const focused = document.activeElement?.closest?.("#car-table-rows tr")?.dataset.message;
    rows.replaceChildren(...shown.map(m => {
      const row = document.createElement("tr");
      row.dataset.message = m.name;
      const name = document.createElement("th");
      name.scope = "row";
      name.textContent = m.name;
      row.append(name);
      row.insertCell().textContent = hex(m) + (m.extended ? " ext" : "");
      const period = row.insertCell();
      period.className = "period";
      periodCell(period, m);
      const integrity = row.insertCell();
      integrity.className = "integrity";
      integrity.dataset.state = source() === "recorded" ? "recorded" : m.skipped ? "skipped" : m.integrity;
      integrity.textContent = integrityText(m);
      return row;
    }));
    if (focused && !editing) rows.querySelector(`tr[data-message="${CSS.escape(focused)}"] button`)?.focus();
    const more = $("car-table-more");
    more.hidden = messages.length <= FIRST_ROWS;
    more.textContent = showAll ? "Show the first " + FIRST_ROWS + " messages" : "Show all " + messages.length +
      " messages";
    more.setAttribute("aria-expanded", String(showAll));
    $("car-table").classList.toggle("recorded", source() === "recorded");
  }
  function renderChecked() {
    const recorded = source() === "recorded";
    $("car-chips").hidden = recorded || !checked;
    $("car-recorded-note").hidden = !recorded || !checked;
    $("car-chips").replaceChildren(...CHIPS.map(([key, label]) => {
      const chip = document.createElement("li");
      chip.className = "chip";
      chip.dataset.state = key;
      chip.textContent = (checked?.counts[key] ?? 0) + " " + label;
      return chip;
    }));
    $("car-integrity-note").textContent = checked?.rule === "hyundai_can" && !recorded ? HYUNDAI_NOTE : "";
    $("car-warnings").replaceChildren(...(checked?.warnings || []).map(text => {
      const item = document.createElement("li");
      item.textContent = text;
      return item;
    }));
    const catalogRule = checked && !$("car-rule").value ? (RULES[checked.rule] || "none") : "";
    $("car-rule").options[0].textContent = "From the catalog" + (catalogRule ? ": " + catalogRule : "");
    $("car-rule-status").textContent = ruleFile ? "Rule file " + ruleFile.name + " · " + ruleFile.rules +
      (ruleFile.rules === 1 ? " rule" : " rules") + " · applied to the messages it names" : "";
    $("car-rule-clear").hidden = !ruleFile;
    renderTable();
    const fields = {"car-identity-file": checked?.dbc.filename, "car-identity-sha": checked?.dbc.sha256,
      "car-identity-revision": checked ? (checked.revision || "not a catalog DBC") : "",
      "car-identity-rule": checked ? (RULES[checked.rule] || checked.rule || "none") : ""};
    for (const [id, value] of Object.entries(fields)) $(id).textContent = value || "—";
    $("car-identity").hidden = !checked;
  }

  // --- step 3: run -----------------------------------------------------------------------------------------------------

  function syncInterfaces() {
    const bar = $("interface");
    const mine = $("car-interface");
    const options = [...bar.options].map(o => [o.value, o.textContent]);
    if (JSON.stringify(options) !== JSON.stringify([...mine.options].map(o => [o.value, o.textContent]))) {
      mine.replaceChildren(...options.map(([value, text]) => new Option(text, value)));
    }
    if (source() === "recorded") {
      if (![...mine.options].some(o => o.value === "vcan0")) mine.append(new Option("vcan0", "vcan0"));
      mine.value = "vcan0";
    } else mine.value = bar.value;
  }
  $("car-interface").onchange = () => {
    const bar = $("interface");
    bar.value = $("car-interface").value;
    bar.dispatchEvent(new Event("change"));  // control.js rewrites the working copy and then reports working-copy
  };
  $("interface").addEventListener("change", syncInterfaces);

  function busLoadText() {
    if (!checked) return "Choose a car to estimate the bus load.";
    if (source() === "recorded") return "Recorded frames are sent on vcan0 at their recorded timing; no estimate.";
    const load = checked.bus_load;
    if (load.unavailable) return "Bus load could not be estimated on " + load.interface + ": " + load.unavailable;
    const share = load.estimate == null ? "unknown" : (load.estimate * 100).toFixed(1) + "%";
    let text = "Estimated bus load " + share + " of " + kbit(load.bitrate) + " on " + load.interface +
      (load.assumed ? " (virtual interface, bitrate assumed)" : "") + (load.worst_case ? ", worst case" : "");
    if (load.refused) text += ". Refused: " + load.refused;
    return text;
  }

  function confirmReview(review) {
    const dialog = $("dbc-review");
    $("review-target").textContent = "Start periodic transmission on isolated simulator bench " +
      review.interface + "?";
    $("review-note").textContent = review.note;
    $("review-messages").replaceChildren(...review.messages.map(item => {
      const row = document.createElement("li");
      const title = document.createElement("strong");
      title.textContent = item.name + " · " + hex(item) + (item.extended ? " extended" : " standard") + " · " +
        item.dlc + " bytes every " + item.period_ms + " ms";
      const raw = document.createElement("code");
      raw.textContent = item.raw;
      row.append(title, document.createElement("br"), raw);
      return row;
    }));
    $("review-conflicts").textContent = review.conflicts.length ? review.conflicts.join(" · ") :
      "No matching IDs observed during the " + review.observation_ms + " ms check.";
    dialog.returnValue = "cancel";
    return new Promise(resolve => {
      dialog.addEventListener("close", () => {
        $("review-note").textContent = "";
        resolve(dialog.returnValue === "start");
      }, {once: true});
      dialog.showModal();
    });
  }

  async function playGenerated() {
    busyEditor(true);
    try {
      if (!await recheck()) return;
      if (checked.bus_load.refused) { refusal(checked.bus_load); return; }
      const text = draft.value;
      const review = await api("/car/review", {text});
      const body = {text};
      if (review.required) {
        if (!await confirmReview(review)) return;
        body.review = review.token;
      }
      const started = await api("/run", body);
      statusNote = "";
      active = {kind: "run", id: started.id, interface: started.interface, source: "generated",
        started: Date.now()};
      phase = "running";
      await refresh();
    } finally { busyEditor(false); }
  }
  async function playRecorded() {
    const file = $("car-log-file").files[0];
    if (file.size > MAX_LOG_BYTES) throw Error(file.name + " is larger than 16 MiB");
    const messages = checked.messages;
    const session = openLive("vcan0", checked.dbc.sha256, messages, null, true);
    const error = await session.ready;
    if (error) throw error;
    const params = new URLSearchParams({name: file.name.slice(0, 200), loop: "0"});
    const response = await fetch("/replay?" + params, {method: "POST", body: file});
    if (response.status === 413) throw Error(file.name + " is larger than 16 MiB");
    const data = await response.json();
    if (!response.ok) throw Error(data.error || response.statusText);
    active = {kind: "replay", id: data.id, interface: "vcan0", source: "recorded", started: Date.now(),
      name: file.name};
    statusNote = "";
    phase = "running";
    watchReplay();
  }
  $("car-play").onclick = () => step(async () => {
    try {
      if (source() === "recorded") await playRecorded();
      else await playGenerated();
    } catch (error) {
      if (!active) endLive();
      throw error;
    }
  }, "Play failed: ", "fix the cause above, then press Play again.");
  $("car-stop").onclick = async () => {
    if (active?.kind === "run") {
      if (run.id === active.id && !$("stop").disabled) $("stop").click();  // the run bar's own Stop ECU flow
      return;
    }
    if (active?.kind === "replay") {
      try { await api("/replay/" + active.id + "/stop", {}); }
      catch (error) { problem("The replay could not be stopped: " + error.message, "try Stop again."); }
    }
  };

  async function watchReplay() {
    const mine = active;
    while (active === mine) {
      let data;
      try { data = await api("/replay"); }
      catch (error) {
        problem("The replay status could not be read: " + error.message, "check that the server is running.");
        await new Promise(resolve => setTimeout(resolve, 1500));
        continue;
      }
      if (data.id === mine.id && data.state !== "Playing") {
        mine.result = data.sent + " of " + data.frames + " frames sent";
        if (data.error) {
          const [cause, next] = failure(data.error, "vcan0");
          problem(cause, next);
        }
        finishRun();
        return;
      }
      await new Promise(resolve => setTimeout(resolve, 400));
    }
  }

  function finishRun(failed) {
    const ended = active;
    active = null;
    phase = checked ? "ready" : "choose";
    endLive(ended);
    if (failed) {
      const [cause, next] = failure(failed.error, ended.interface);
      problem(cause, next);
    }
    statusNote = ended.kind === "replay" ?
      "Played the recorded log " + ended.name + " on vcan0 · " + (ended.result || "stopped") + "." :
      failed ? "The run on " + ended.interface + " failed." : "Stopped the generated drive on " + ended.interface + ".";
    render();
  }

  window.addEventListener("run-update", () => {
    if (active?.kind === "run") {
      if (run.id !== active.id) {
        if (active.seen) finishRun();
        return;
      }
      active.seen = true;
      if (run.state === "Running" && !live) {
        openLive(run.interface, run.car?.dbc.sha256, null, run.car?.bus_load.bitrate);
        render();
      }
      if (run.state === "Failed") return finishRun(run);
      if (run.state === "Stopped") return finishRun();
    } else if (!active && run.state === "Running" && run.car) {
      active = {kind: "run", id: run.id, interface: run.interface, source: "generated", started: Date.now(),
        seen: true};
      phase = "running";
      statusNote = "";
      openLive(run.interface, run.car.dbc.sha256, null, run.car.bus_load.bitrate);
    }
    render();
  });
  window.addEventListener("working-copy", () => {
    syncInterfaces();
    if (active) return;
    if (/^car:/m.test(draft.value)) {
      const rule = /^car:[\s\S]*?^ {2}rule: (\w+)/m.exec(draft.value);
      $("car-rule").value = rule && RULES[rule[1]] ? rule[1] : "";
      recheck().then(adopt);
    } else {
      ++version;
      checked = null;
      phase = "choose";
      render();
    }
  });
  function adopt(ok) {
    if (!ok || !checked) return;
    const [brand, platform] = findPlatform(checked.platform);
    $("car-brand").value = brand;
    fillPlatforms(brand, platform?.id);
    fillBuses(platform, /^ {2}bus: (\w+)/m.exec(draft.value)?.[1]);
    render();
  }

  // --- the live data panel ---------------------------------------------------------------------------------------------

  function openLive(iface, sha256, messages, bitrate, recorded = false) {
    endLive();
    const session = {controller: new AbortController(), captureId: null, rows: new Map(), frames: 0,
      carFrames: 0, errorFrames: 0, bits: [], link: null, baseline: null, paused: false, bitrate, iface,
      linkTimer: null, linkNotice: "", recorded};
    live = session;
    lastLive = null;
    let signalReady;
    session.ready = new Promise(resolve => { signalReady = resolve; });
    $("car-live-rows").replaceChildren();
    $("car-live-pause").setAttribute("aria-pressed", "false");
    $("car-live-pause").textContent = "Pause display";
    if (messages) buildRows(session, messages);
    else api("/car/run").then(car => { if (live === session) buildRows(session, car.messages); })
      .catch(error => { if (live === session) problem("The run's messages could not be read: " + error.message,
        "stop the run and press Play again."); });
    stream(session, iface, sha256, signalReady);
    if (!recorded) {
      pollLink(session);
      session.linkTimer = setInterval(() => pollLink(session), 2000);
    }
    return session;
  }
  function buildRows(session, messages) {
    for (const m of messages) {
      if (m.skipped) continue;
      const row = document.createElement("tr");
      row.dataset.message = m.name;
      row.dataset.id = Number(m.id).toString(16).toUpperCase();
      const name = document.createElement("th");
      name.scope = "row";
      name.textContent = m.name;
      row.append(name);
      row.insertCell().textContent = hex(m);
      const cells = {};
      for (const key of ["values", "set-period", "measured-period", "counter", "checksum", "integrity"]) {
        cells[key] = row.insertCell();
        cells[key].className = key;
        cells[key].textContent = "—";
      }
      cells["set-period"].textContent = session.recorded ? "as recorded" :
        m.period_ms + " ms · " + (PERIOD_SOURCES[m.period_source] || m.period_source);
      cells.integrity.textContent = session.recorded ? "as recorded" : SHORT_INTEGRITY[m.integrity] || m.integrity;
      session.rows.set(m.name, {message: m, row, cells, signals: null, last: null, intervals: [], dirty: false});
    }
    $("car-live-rows").replaceChildren(...[...session.rows.values()].map(entry => entry.row));
    filterRows();
  }
  async function stream(session, iface, sha256, signalReady) {
    try {
      const response = await fetch("/capture", {method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({interface: iface, sha256: sha256 || undefined}), signal: session.controller.signal});
      if (!response.ok) throw Error((await response.json()).error || response.statusText);
      session.captureId = response.headers.get("X-Capture-Id");
      const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
      let buffer = "";
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        buffer += value;
        let newline;
        while ((newline = buffer.indexOf("\n")) >= 0) {
          const packet = JSON.parse(buffer.slice(0, newline));
          buffer = buffer.slice(newline + 1);
          if (packet.error) throw Error(packet.error);
          if (packet.finished) return;
          if (packet.ready) signalReady(null);
          else observe(session, packet);
        }
      }
    } catch (error) {
      signalReady(error);
      if (!session.controller.signal.aborted && live === session) {
        problem("Live data stopped: " + error.message, "check the interface, then press Play again.");
      }
    } finally {
      paint(session);
    }
  }
  function observe(session, batch) {
    const now = Date.now();
    for (const frame of batch.frames) {
      session.frames += 1;
      if (frame.kind === "ERR") { session.errorFrames += 1; continue; }
      session.bits.push([now, (frame.id.length === 8 ? 67 : 47) + 8 * frame.length]);
      const decoded = frame.dbc;
      const entry = decoded && !decoded.error ? session.rows.get(decoded.name) : null;
      if (!entry) continue;
      session.carFrames += 1;
      if (entry.last != null) {
        entry.intervals.push((frame.time - entry.last) / 1000);
        if (entry.intervals.length > 20) entry.intervals.shift();
      }
      entry.last = frame.time;
      entry.signals = decoded.signals;
      entry.dirty = true;
    }
    while (session.bits.length && session.bits[0][0] < now - 2000) session.bits.shift();
  }
  function number(value) {
    return typeof value === "number" && !Number.isInteger(value) ? String(Number(value.toFixed(3))) : String(value);
  }
  function paint(session = live) {
    if (!session) return;
    if (!session.paused) {
      for (const entry of session.rows.values()) {
        if (!entry.dirty) continue;
        entry.dirty = false;
        const {message: m, cells, signals} = entry;
        const integrity = new Set([...m.counters, ...m.checksums]);
        cells.values.textContent = Object.entries(signals).filter(([name]) => !integrity.has(name))
          .map(([name, field]) => name + " " + number(field.value) + (field.unit ? " " + field.unit : ""))
          .join(" · ") || "—";
        cells.counter.textContent = m.counters.map(name => signals[name] ? number(signals[name].value) : "?")
          .join(" / ") || "—";
        cells.checksum.textContent = m.checksums.map(name => signals[name] ?
          "0x" + Number(signals[name].value).toString(16).toUpperCase().padStart(2, "0") : "?").join(" / ") || "—";
        if (entry.intervals.length) {
          const mean = entry.intervals.reduce((a, b) => a + b, 0) / entry.intervals.length;
          cells["measured-period"].textContent = mean.toFixed(1) + " ms";
        }
      }
    }
    const bits = session.bits.reduce((total, [, size]) => total + size, 0);
    const parts = [session.carFrames.toLocaleString("en") + " car frames seen on the bus"];
    if (session.bitrate) parts.push("bus load seen " + (bits / 2 / session.bitrate * 100).toFixed(1) + "% of " +
      kbit(session.bitrate) + " (frame bits without stuffing)");
    parts.push("error frames " + session.errorFrames);
    if (session.link) {
      const show = value => value == null ? "n/a" : value.toLocaleString("en");
      parts.push("bus errors " + show(session.link.bus_errors), "bus-off " + show(session.link.bus_off),
        "tx dropped (kernel) " + show(session.link.tx_dropped), "link " + (session.link.state || "unknown"));
    }
    if (session.paused) parts.unshift("Display paused");
    if (session === live || session === lastLive) $("car-live-stats").textContent = parts.join(" · ");
  }
  async function pollLink(session) {
    try {
      const link = await api("/car/link?interface=" + encodeURIComponent(session.iface));
      if (live !== session) return;
      session.baseline ??= link;
      session.link = link;
      let notice = "";
      if (link.state === "DOWN") notice = "down";
      else if ((link.bus_off ?? 0) > (session.baseline.bus_off ?? 0)) notice = "bus-off";
      if (notice && notice !== session.linkNotice) {
        const [cause, next] = notice === "down" ? failure("Network is down", session.iface) :
          failure("bus-off reported by the interface", session.iface);
        problem(cause, next);
      }
      session.linkNotice = notice;
    } catch (error) {
      if (live === session) {
        const [cause, next] = failure(error.message, session.iface);
        problem(cause, next);
      }
    }
  }
  function endLive(ended) {
    const session = live;
    if (!session) return;
    clearInterval(session.linkTimer);
    if (ended?.kind === "replay" && session.captureId) {
      // Let the frames queued before the replay ended arrive, then the server ends the stream.
      api("/capture/" + session.captureId + "/finish", {}).catch(() => session.controller.abort());
      setTimeout(() => session.controller.abort(), 3000);
    } else session.controller.abort();
    paint(session);
    live = null;
    lastLive = session;
  }
  function filterRows() {
    const query = $("car-live-filter").value.trim();
    const id = query.replace(/^0x/i, "").toUpperCase();
    const hexQuery = /^[0-9A-F]{1,8}$/.test(id);
    for (const row of $("car-live-rows").rows) {
      const match = !query || row.dataset.message.toLowerCase().includes(query.toLowerCase()) ||
        (hexQuery && parseInt(row.dataset.id, 16) === parseInt(id, 16));
      row.hidden = !match;
    }
  }
  $("car-live-filter").oninput = filterRows;
  $("car-live-pause").onclick = () => {
    if (!live) return;
    live.paused = !live.paused;
    $("car-live-pause").setAttribute("aria-pressed", String(live.paused));
    $("car-live-pause").textContent = live.paused ? "Resume display" : "Pause display";
    if (!live.paused) for (const entry of live.rows.values()) entry.dirty = entry.signals != null;
    paint();
  };

  // --- the whole panel -------------------------------------------------------------------------------------------------

  function elapsed(since) {
    const seconds = Math.floor((Date.now() - since) / 1000);
    return Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
  }
  function summary() {
    if (!checked) return "";
    const [, platform] = findPlatform(checked.platform);
    const what = platform ? platform.names[0] + " · " + (/^ {2}bus: (\w+)/m.exec(draft.value)?.[1] || "pt") +
      " bus" : checked.dbc.filename + " (uploaded DBC)";
    return what + " · " + (source() === "recorded" ? "recorded log, played as recorded" : "generated drive");
  }
  function setStep(id, state, collapsed) {
    const element = $(id);
    element.dataset.state = state;
    if (state === "active") element.setAttribute("aria-current", "step");
    else element.removeAttribute("aria-current");
    element.querySelector(".car-step-state").textContent = state === "done" ? "Done" : "";
    element.querySelector(".car-step-body").hidden = collapsed;
  }
  function renderStatus() {
    let text = statusNote;
    if (active) {
      text = (active.kind === "replay" ? "Playing the recorded log " + active.name + " on vcan0" :
        (live ? "Running on " : "Starting on ") + active.interface + " · generated drive") + " · " +
        elapsed(active.started) + " elapsed";
    }
    if ($("car-run-status").textContent !== text) $("car-run-status").textContent = text;
  }
  function render() {
    const running = phase === "running";
    const recorded = source() === "recorded";
    const one = phase === "choose" ? "active" : "done";
    const two = phase === "choose" ? "todo" : phase === "check" ? "active" : "done";
    const three = phase === "ready" || running ? "active" : "todo";
    setStep("car-step-1", one, one === "done" && (!stepOneOpen || running));
    setStep("car-step-2", two, two === "todo" || running);
    setStep("car-step-3", three, false);
    $("car-summary-1").textContent = summary();
    $("car-summary-1").hidden = one !== "done";
    $("car-change-1").hidden = one !== "done" || running;
    $("car-change-1").setAttribute("aria-expanded", String(stepOneOpen));
    $("car-change-1").textContent = stepOneOpen ? "Close" : "Change car or source";
    $("car-summary-2").textContent = checked ? CHIPS.map(([key, label]) => checked.counts[key] + " " + label)
      .join(", ") : "";
    $("car-summary-2").hidden = !(running && checked);
    for (const id of ["car-brand", "car-bus", "car-upload", "car-source-generated", "car-source-recorded",
      "car-rule", "car-rule-file", "car-rule-clear", "car-timing-log", "car-log-file"]) {
      $(id).disabled = busy || running;
    }
    $("car-platform").disabled = busy || running || !$("car-brand").value;
    $("car-bus").disabled = busy || running || $("car-bus").options.length < 2;
    $("car-timing-log").disabled ||= !checked;
    $("car-generated-options").hidden = recorded;
    $("car-recorded-options").hidden = !recorded;
    renderChecked();
    syncInterfaces();
    $("car-interface").disabled = busy || running || recorded;
    $("car-bus-load").textContent = busLoadText();
    $("car-bus-load").classList.toggle("refused", Boolean(checked?.bus_load.refused) && !recorded);
    const blocked = recorded ? !$("car-log-file").files.length :
      ["Starting", "Running", "Stopping"].includes(run.state);
    $("car-play").disabled = busy || running || !checked || blocked;
    $("car-play").textContent = recorded ? "Play recorded log on vcan0" : "Play";
    $("car-stop").disabled = !running;
    renderStatus();
    const idle = !live;
    $("car-live").classList.toggle("idle", idle);
    $("car-live-idle").hidden = !idle;
    $("car-live-idle").textContent = lastLive && idle ?
      "Stopped. The values are the last ones seen; they refresh when you press Play." : "Starts when you press Play";
    $("car-live-pause").disabled = !live;
    $("car-live-filter").disabled = !live && !lastLive;
  }

  async function init() {
    render();
    try {
      catalog = await api("/car/catalog");
      fillBrands();
      fillPlatforms("", "");
      $("car-catalog-note").textContent = "Catalog revision " + catalog.revision + " · " +
        Object.values(catalog.brands).reduce((total, list) => total + list.length, 0) + " platforms";
    } catch (error) {
      problem("The car catalog could not be loaded: " + error.message, "reload the page.");
    }
    render();
    ticker = setInterval(() => {
      paint();
      if (active) renderStatus();
    }, 250);
  }
  window.addEventListener("DOMContentLoaded", init);
  window.addEventListener("pagehide", () => { live?.controller.abort(); clearInterval(ticker); });
})();
