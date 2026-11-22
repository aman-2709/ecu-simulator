// Capture is per browser tab and independent of the ECU process.
(() => {
  const byId = id => document.getElementById(id);
  const select = byId("capture-interface");
  const toggle = byId("capture-toggle");
  const pause = byId("capture-pause");
  const status = byId("capture-status");
  const limit = 512;
  const previousValues = new Map();
  let trace = [], groups = new Map(), total = 0, dropped = 0;
  let pgnGroups = new Map();
  let automaticJ1939 = false;
  const jMode = byId("j1939-mode");
  let session = null, frozen = null, grouped = false, renderTimer = null;
  let catalogVersion = 0, wheel = null, reconnect = false;
  window.addEventListener("capture-dbc-change", () => {
    if (session) {
      reconnect = true;
      session.abort();
    }
  });

  function renderWheel() {
    const active = run.state === "Running" && run.restbus && run.interface === select.value;
    const panel = byId("wheel-monitor");
    panel.hidden = !active;
    if (!active || (wheel && wheel.run_id !== run.id)) wheel = null;
    if (!active || byId("live-panel").hidden) return;
    for (const field of ["dbc", "revision", "sha256"]) {
      const value = run.restbus[field] || "—";
      const output = byId("wheel-" + field);
      if (output.textContent !== value) output.textContent = value;
    }
    const sample = frozen ? frozen.wheel : wheel;
    const current = sample?.run_id === run.id ? sample : null;
    const state = current?.state || "Waiting";
    byId("wheel-state").textContent = state;
    panel.dataset.state = state;
    for (const name of ["FR", "FL", "RR", "RL"]) {
      byId("wheel-" + name).textContent = current?.speeds?.[name]?.toFixed(2) ?? "—";
    }
    for (const field of ["age", "period"]) {
      const ms = current?.[field + "_ms"];
      byId("wheel-" + field).textContent = ms == null ? "—" : ms.toFixed(1) + " ms";
    }
    byId("wheel-raw").textContent = current?.bytes?.map(
      value => value.toString(16).toUpperCase().padStart(2, "0")).join(" ") || "—";
  }
  window.addEventListener("run-update", renderWheel);
  select.onchange = () => { wheel = null; renderWheel(); };

  let trendModule = null, trends = null;
  async function loadTrends() {
    const script = document.createElement("script");
    script.src = "/uPlot.iife.min.js";
    const ready = new Promise((resolve, reject) => {script.onload = resolve; script.onerror = reject;});
    const css = document.createElement("link");
    css.rel = "stylesheet"; css.href = "/uPlot.min.css";
    document.head.append(css, script);
    await ready;
    trends = (await import("/trends.js")).liveTrends;
    byId("trends").hidden = !jMode.checked;
  }

  async function showTab(live) {
    byId("live-panel").hidden = !live;
    byId("sim-panel").hidden = live;
    byId("live-tab").setAttribute("aria-pressed", String(live));
    byId("sim-tab").setAttribute("aria-pressed", String(!live));
    const version = ++catalogVersion;
    if (!live) return;
    render();
    if (session) { refreshReplay(); return; }
    try {
      const catalog = await api("/profiles");
      if (version !== catalogVersion || session) return;
      if (catalog.signal_plot) {
        trendModule ??= loadTrends().catch(() => {
          trendModule = null;
          byId("trend-status").textContent = "Could not load the plot.";
        });
        await trendModule;
        if (version !== catalogVersion || session) return;
      }
      byId("replay-panel").hidden = !catalog.replay;
      if (!catalog.kenworth_drive) byId("replay-source").querySelector('[value="kenworth-drive"]')?.remove();
      if (catalog.replay) refreshReplay();
      const previous = select.value;
      select.replaceChildren(...catalog.interfaces.map(name => new Option(name, name)));
      for (const name of [previous, run.interface, byId("interface").value, "vcan0"]) {
        if (catalog.interfaces.includes(name)) { select.value = name; break; }
      }
      renderWheel();
      toggle.disabled = !select.value;
      if (!select.value) status.textContent = "No CAN interfaces found";
    } catch (error) {
      if (version === catalogVersion) status.textContent = error.message;
    }
  }
  byId("sim-tab").onclick = () => showTab(false);
  byId("live-tab").onclick = () => showTab(true);

  const pgnNames = new Map([
    [0xEB00, "TP.DT · Data transfer"],
    [0xEE00, "Address claim"],
    [0xFEEB, "Component identification"],
    [0xEA00, "RQST · Request"],
    [0xFEE5, "Engine hours"],
    [0xF002, "ETC1 · Electronic Transmission Controller 1"],
    [0xF003, "EEC2 · Electronic Engine Controller 2"],
    [0xF004, "EEC1 · Electronic Engine Controller 1"],
    [0xFECA, "DM1 · Active Diagnostic Trouble Codes"],
    [0xFEEE, "ET1 · Engine Temperature 1"],
    [0xFEF1, "CCVS · Cruise Control / Vehicle Speed"],
  ]);
  function j1939(frame) {
    if (frame.kind || frame.id.length !== 8) return null;
    const id = parseInt(frame.id, 16);
    const directed = ((id >>> 16) & 255) < 240;
    const pgn = (id >>> 8) & (directed ? 0x3FF00 : 0x3FFFF);
    const sa = id & 255;
    let value, measurements;
    if (pgn === 0xF004) {
      const raw = frame.bytes[3] + 256 * frame.bytes[4];
      measurements = {rpm: frame.length === 8 && raw <= 0xFAFF ? raw / 8 : null};
      if (frame.length !== 8) value = "Engine speed · malformed EEC1";
      else if (raw >= 0xFF00) value = "Engine speed · unavailable";
      else if (raw >= 0xFE00) value = "Engine speed · error";
      else if (raw > 0xFAFF) value = "Engine speed · outside defined range";
      else value = "Engine speed " + measurements.rpm + " rpm";
    }
    if (pgn === 0xF003) {
      measurements = {
        pedal: frame.length === 8 && frame.bytes[1] <= 250 ? frame.bytes[1] * 4 / 10 : null,
        load: frame.length === 8 && frame.bytes[2] <= 250 ? frame.bytes[2] : null,
      };
      const percent = (raw, number) => {
        if (raw === 255) return "unavailable";
        if (raw === 254) return "error";
        if (raw > 250) return "outside defined range";
        return number + "%";
      };
      value = frame.length !== 8 ? "Malformed EEC2" :
        "Accelerator pedal " + percent(frame.bytes[1], measurements.pedal) +
        " · Engine load " + percent(frame.bytes[2], measurements.load);
    }
    if (pgn === 0xEB00) {
      value = frame.length === 8 && frame.bytes[0] ? "Multipacket message · fragment " + frame.bytes[0] :
        "Malformed data transfer";
    }
    if (pgn === 0xEA00) {
      const requested = frame.bytes[0] | (frame.bytes[1] << 8) | (frame.bytes[2] << 16);
      const target = pgnNames.get(requested);
      value = frame.length !== 3 || requested > 0x3FFFF ? "Malformed request" :
        (target ? "Request " + target + " · " : "Request ") + "PGN " + requested;
    }
    return {pgn, sa, da: directed ? (id >>> 8) & 255 : null, name: pgnNames.get(pgn), value, measurements};
  }

  function addBatch(batch) {
    wheel = batch.wheel || null;
    renderWheel();
    if (batch.dropped != null) dropped = batch.dropped;
    for (const frame of batch.frames) {
      const key = frame.id + frame.kind;
      const previous = groups.get(key);
      const changed = frame.bytes.map((value, i) => Boolean(previous && previous.bytes[i] !== value));
      const info = describe(frame);
      if (info.value !== undefined) {
        const signalKey = frame.id + ":" + info.name;
        const prior = previousValues.get(signalKey);
        info.changed = prior !== undefined && prior !== info.value;
        previousValues.set(signalKey, info.value);
      }
      frame.info = info;
      frame.j = j1939(frame);
      const jKey = frame.j ? frame.j.pgn + "/" + frame.j.sa : key;
      const prior = pgnGroups.get(jKey);
      frame.period = prior ? (frame.time - prior.time) / 1000 : null;
      frame.crossedLoop = prior && prior.time < batch.loop_boundary_us && batch.loop_boundary_us <= frame.time;
      pgnGroups.delete(jKey);
      pgnGroups.set(jKey, {...frame, count: (prior?.count || 0) + 1,
        changed: frame.bytes.map((v, i) => Boolean(prior && prior.bytes[i] !== v))});
      if (pgnGroups.size > limit) pgnGroups.delete(pgnGroups.keys().next().value);
      groups.delete(key);
      groups.set(key, {...frame, changed, count: (previous?.count || 0) + 1});
      if (groups.size > limit) groups.delete(groups.keys().next().value);
      trace.push(frame);
    }
    trends?.observe(batch);
    total += batch.frames.length;
    if (trace.length > limit) trace.splice(0, trace.length - limit);
    if (batch.frames.length && !frozen && !byId("live-panel").hidden && !renderTimer) {
      renderTimer = setTimeout(() => { renderTimer = null; render(); }, 150);
    }
  }

  // Interpret normal-addressed Classical CAN diagnostics on conventional 11-bit IDs.
  // Unknown traffic keeps its original bytes and neutral styling.
  function describe(frame) {
    if (frame.kind === "ERR") return {name: "CAN controller error", label: "Error", role: "error"};
    if (frame.kind === "RTR") return {label: "Remote"};
    if (frame.dbc) {
      const decoded = frame.dbc;
      if (decoded.error) return {name: decoded.name, label: "Malformed", value: decoded.error, role: "error"};
      const values = Object.entries(decoded.signals).map(([name, field]) =>
        name + " " + field.value + (field.unit ? " " + field.unit : ""));
      const period = decoded.period_ms == null ? "" : " · " + Number(decoded.period_ms).toFixed(1) + " ms";
      return {name: decoded.name, label: "DBC" + period, value: values.join(" · ")};
    }
    if ("wheels" in frame) {
      if (!frame.wheels) return {name: "WHEEL_SPEEDS", label: "Malformed", role: "error"};
      const value = ["FR", "FL", "RR", "RL"].map(name => name + " " + frame.wheels[name].toFixed(2)).join(" · ");
      return {name: "WHEEL_SPEEDS", label: "Broadcast", value: value + " km/h"};
    }
    const id = parseInt(frame.id, 16);
    if (frame.id.length !== 3 || id < 0x7DF || id > 0x7EF || !frame.bytes.length) return {};
    const [pci, service, pid, a, b] = frame.bytes;
    const type = pci >> 4;
    const length = pci & 0x0F;
    if (type !== 0) {
      if (id === 0x7DF) return {};
      if (type === 1 && frame.bytes.length === 8) {
        let size = length * 256 + service;
        // A zero 12-bit length introduces a four-byte extended payload length.
        if (size === 0) {
          size = frame.bytes.slice(2, 6).reduce((total, byte) => total * 256 + byte, 0);
          if (size <= 4095) return {};
        }
        if (size > 7) return {label: "First frame"};
      }
      if (type === 2 && frame.bytes.length >= 2) return {label: "Consecutive frame"};
      if (type === 3 && length <= 2 && frame.bytes.length >= 3) return {label: "Flow control", role: "flow-control", end: 3};
      return {};
    }
    if (!length || length >= frame.bytes.length) return {};
    const single = {label: "Single frame", end: length + 1};
    if (id <= 0x7E7 && length === 3 && service === 0x19 && pid === 0x02) {
      return {...single, name: "Read DTCs by status mask"};
    }
    if (id <= 0x7E7 && length === 2 && service === 0x3E && (pid === 0 || pid === 0x80)) {
      return {...single, name: pid === 0 ? "Tester Present" : "Tester Present · suppression requested"};
    }
    if (id >= 0x7E8 && length === 2 && service === 0x7E && pid === 0) {
      return {...single, name: "Tester Present · positive response"};
    }
    const speed = pid === 0x0D;
    if (!speed && pid !== 0x10) return single;
    const name = speed ? "Vehicle speed" : "Mass airflow";
    if (service === 0x01 && length === 2 && id >= 0x7DF && id <= 0x7E7) {
      return {name, label: "Request", role: "request", end: length + 1};
    }
    if (service === 0x41 && length === (speed ? 3 : 4) && id >= 0x7E8 && id <= 0x7EF) {
      const value = speed ? a + " km/h" : ((256 * a + b) / 100).toFixed(2) + " g/s";
      return {name, value, label: "Reply", role: "reply", end: length + 1};
    }
    return single;
  }

  function hasReading(frame) {
    return frame.j?.pgn === 0xF003 || frame.j?.pgn === 0xF004 ||
      Boolean(frame.dbc?.signals && Object.keys(frame.dbc.signals).length);
  }

  function render() {
    renderWheel();
    const snapshot = frozen || {trace, groups, pgnGroups, total, dropped};
    const headings = grouped ? ["CAN ID", "Count", "Type", "Latest signal / service", "Latest value", "Length", "Latest bytes"] : ["Time (UTC)", "CAN ID", "Type", "Signal / service", "Value", "Length", "Bytes"];
    if (jMode.checked) headings.splice(2, 5, "PGN", "Source", "Last interval", "Message", "Value", "DLC", "Bytes");
    byId("frame-scroll").classList.toggle("j1939-table", jMode.checked);
    byId("frame-head").replaceChildren(...headings.map(label => {
      const th = document.createElement("th"); th.textContent = label; return th;
    }));
    const filter = byId("can-filter").value.trim().replace(/^0x/i, "").toUpperCase();
    const selectedGroups = jMode.checked ? snapshot.pgnGroups : snapshot.groups;
    const readingsOnly = grouped && jMode.checked && byId("readings-only").checked;
    byId("reading-filter").hidden = !grouped || !jMode.checked;
    const frames = grouped ? [...selectedGroups.values()].sort((a, b) =>
      jMode.checked ? Number(hasReading(b)) - Number(hasReading(a)) || (a.j?.pgn ?? -1) - (b.j?.pgn ?? -1) || (a.j?.sa ?? -1) - (b.j?.sa ?? -1) ||
        a.id.localeCompare(b.id) : a.id.localeCompare(b.id)) : snapshot.trace;
    const rows = document.createDocumentFragment();
    for (const frame of frames) {
      if (readingsOnly && !hasReading(frame)) continue;
      const j = jMode.checked && frame.j;
      const idMatch = /^[0-9A-F]{1,8}$/.test(filter) && parseInt(frame.id, 16) === parseInt(filter, 16);
      const pgnMatch = j && (String(j.pgn) === filter || j.pgn.toString(16).toUpperCase() === filter ||
        j.name?.toUpperCase().includes(filter) || j.value?.toUpperCase().includes(filter) || (j.pgn === 0xF004 && /^(RPM|ENGINE SPEED|SPN 190|190)$/.test(filter)));
      if (filter && !idMatch && !pgnMatch) continue;
      const row = document.createElement("tr");
      const id = [frame.id, frame.kind].filter(Boolean).join(" ");
      if (grouped) {
        row.insertCell().textContent = id;
        row.insertCell().textContent = frame.count;
      } else {
        const iso = new Date(Math.floor(frame.time / 1000)).toISOString();
        row.insertCell().textContent = iso.slice(11, 23) + String(frame.time % 1000).padStart(3, "0");
        row.insertCell().textContent = id;
      }
      if (jMode.checked) {
        row.insertCell().textContent = j ? j.pgn : "—";
        const sourceCell = row.insertCell();
        const address = n => "0x" + n.toString(16).toUpperCase().padStart(2, "0");
        sourceCell.textContent = j ? address(j.sa) : "—";
        if (j && j.da !== null) sourceCell.title = "Destination " + address(j.da);
        const interval = row.insertCell();
        interval.textContent = j && frame.period != null ? frame.period.toFixed(1) + " ms" : "—";
        if (j && frame.crossedLoop) {
          const note = document.createElement("small");
          note.textContent = "Replay loop";
          interval.append(note);
        }
      }
      const info = j ? {...frame.info, label: "J1939", name: j.name || frame.info.name,
        value: [j.value, frame.info.value].filter(Boolean).join(" · ") || undefined} : frame.info;
      row.className = info.role || "";
      if (!jMode.checked) {
        const type = row.insertCell();
        if (info.label) {
          const label = document.createElement("span");
          label.className = "frame-type";
          label.textContent = info.label;
          type.append(label);
        } else type.textContent = "—";
      }
      row.insertCell().textContent = info.name || (jMode.checked && !j && info.label) || "—";
      const value = row.insertCell();
      if (j?.pgn === 0xF003) value.title = "Byte 2: accelerator pedal (SPN 91). Byte 3: engine load (SPN 92).";
      if (j?.pgn === 0xEA00) value.title = "Bytes 1–3: requested PGN, least significant byte first.";
      if (frame.dbc) value.className = "dbc-reading";
      if (info.value !== undefined) {
        const reading = document.createElement(info.changed ? "mark" : "strong");
        reading.textContent = info.value;
        if (info.changed) reading.title = "Changed since the last reply for this signal";
        value.append(reading);
      } else value.textContent = j ? "No signal decoder" : "—";
      row.insertCell().textContent = frame.length;
      const bytes = row.insertCell();
      frame.bytes.forEach((value, i) => {
        if (i) bytes.append(" ");
        const hex = value.toString(16).toUpperCase().padStart(2, "0");
        if (info.end !== undefined && i >= info.end) {
          const padding = document.createElement("span");
          padding.className = "padding-byte";
          padding.title = "CAN padding";
          padding.textContent = hex;
          bytes.append(padding);
        } else if (grouped && frame.changed[i]) {
          const mark = document.createElement("mark"); mark.textContent = hex; bytes.append(mark);
        } else bytes.append(hex);
      });
      rows.append(row);
    }
    const scroll = byId("frame-scroll");
    const follow = scroll.scrollHeight - scroll.clientHeight - scroll.scrollTop < 4;
    const count = rows.childElementCount;
    byId("frame-rows").replaceChildren(rows);
    byId("frame-summary").textContent = snapshot.total + " frames received · " + count +
      (grouped ? (jMode.checked ? " of " + selectedGroups.size + " groups shown" : " IDs shown") : " frames shown") + (snapshot.dropped ? " · " + snapshot.dropped + " socket drops" : "");
    if (follow && !grouped) scroll.scrollTop = scroll.scrollHeight;
  }

  let captureReady = null, captureId = null;
  let linkedRun = null, closingCapture = false;

  async function finishLinkedCapture() {
    if (closingCapture || !session) return;
    closingCapture = true;
    reconnect = false;
    const current = session;
    toggle.disabled = true;
    play.disabled = true;
    try {
      if (current && captureId) await api("/capture/" + captureId + "/finish", {});
      else if (current) {
        // Older local servers lack the drain endpoint. Let the final capture batch arrive.
        setTimeout(() => current.abort(), 150);
      }
    } catch {
      current?.abort();
    }
  }
  async function capture() {
    if (session) { session.abort(); return; }
    const controller = new AbortController();
    session = controller;
    let signalReady;
    captureReady = new Promise(resolve => { signalReady = resolve; });
    toggle.textContent = linkedRun ? "Stop capture and replay" : "Stop capture";
    select.disabled = true;
    status.textContent = "Connecting";
    try {
      const response = await fetch("/capture", {method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({interface: select.value, sha256: (dbcAvailable && dbcEditor.captureSha) || undefined}), signal: controller.signal});
      if (!response.ok) throw Error((await response.json()).error || response.statusText);
      captureId = response.headers.get("X-Capture-Id");
      const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
      let buffer = "";
      while (true) {
        const {done, value} = await reader.read();
        if (done) throw Error("Capture connection closed");
        buffer += value;
        let newline;
        while ((newline = buffer.indexOf("\n")) >= 0) {
          const packet = JSON.parse(buffer.slice(0, newline));
          buffer = buffer.slice(newline + 1);
          if (packet.error) throw Error(packet.error);
          if (packet.finished) {
            status.textContent = "Stopped";
            return;
          }
          if (packet.ready) {
            trends?.reset();
            wheel = null; trace = []; groups = new Map(); pgnGroups = new Map(); previousValues.clear(); total = 0; dropped = 0; frozen = null;
            pause.disabled = false; pause.textContent = "Pause display";
            status.textContent = "Capturing on " + select.value;
            signalReady(null);
            render();
          } else addBatch(packet);
        }
      }
    } catch (error) {
      signalReady(error);
      status.textContent = controller.signal.aborted ? "Stopped" : error.message;
    } finally {
      controller.abort();
      trends?.pause(false);
      captureId = null;
      if (!reconnect) linkedRun = null;
      closingCapture = false;
      toggle.disabled = false;
      session = null; captureReady = null; wheel = null; select.disabled = false; frozen = null; pause.disabled = true;
      pause.textContent = "Pause display"; toggle.textContent = "Start capture"; render();
      if (!busy && stopReplay.disabled) play.disabled = source.value === "file" && !file.files.length;
      if (reconnect) {
        reconnect = false;
        capture();
      }
    }
  }
  toggle.onclick = () => {
    if (linkedRun) stopRun(linkedRun);
    else capture();
  };
  const panel = byId("replay-panel");
  const play = byId("replay-play");
  const stopReplay = byId("replay-stop");
  const source = byId("replay-source");
  const file = byId("replay-file");
  const report = byId("replay-status");
  let replayId = null, compactedRun = null, polling = null, busy = false;

  let lastReplayState = "";
  function showReplay(data) {
    lastReplayState = JSON.stringify([data.id, data.state, data.lap]);
    const playing = data.state === "Playing";
    if (linkedRun && (!playing || linkedRun !== data.id)) finishLinkedCapture();
    replayId = data.id;
    play.textContent = data.state === "Finished" ? "Play again on vcan0" : "Play on vcan0";
    play.disabled = busy || closingCapture || playing || (source.value === "file" && !file.files.length);
    if (!playing && !stopReplay.disabled) panel.open = true;
    stopReplay.disabled = !playing;
    stopReplay.hidden = !playing;
    if (playing && replayId !== compactedRun) {
      compactedRun = replayId;
      panel.open = false;
    }
    if (playing) {
      if (linkedRun === data.id) toggle.textContent = "Stop capture and replay";
      report.textContent = "Playing · " + data.name + " · " + data.duration.toFixed(1) + " s recording · pass " + data.lap;
      return;
    }
    report.textContent = data.state === "Idle" ? "" :
      "Replay " + data.state + " · " + data.name + " · " + data.sent + "/" + data.frames +
      " frames · " + data.ids + " IDs · " + data.duration.toFixed(2) + " s · pass " + data.lap +
      (data.error ? " · " + data.error : "");
  }
  let pollVersion = 0;
  async function refreshReplay(onlyIfChanged = false) {
    const version = ++pollVersion;
    clearTimeout(polling);
    if (panel.hidden || busy) return;
    try {
      const data = await api("/replay");
      if (version !== pollVersion || busy) return;
      if (onlyIfChanged !== true || lastReplayState !== JSON.stringify([data.id, data.state, data.lap])) showReplay(data);
      if (!byId("live-panel").hidden || linkedRun) {
        polling = setTimeout(() => refreshReplay(true), data.state === "Playing" ? 300 : 1500);
      }
    } catch (error) { report.textContent = error.message; }
  }
  panel.ontoggle = refreshReplay;
  source.onchange = () => {
    byId("replay-input").hidden = source.value !== "file";
    if (source.value === "file") byId("readings-only").checked = false;
    if (source.value === "file" && automaticJ1939) {
      automaticJ1939 = false;
      setJ1939(false, grouped);
    }
    refreshReplay();
  };
  file.onchange = refreshReplay;
  play.onclick = async () => {
    busy = true;
    ++pollVersion;
    clearTimeout(polling);
    play.disabled = true;
    const openingCapture = !session;
    toggle.disabled = true;
    try {
      if (session && select.value !== "vcan0") throw Error("Stop the current capture to switch to vcan0.");
      if (!session) {
        select.value = "vcan0";
        if (!select.value) throw Error("vcan0 is unavailable.");
        capture();
      }
      const captureError = await captureReady;
      if (captureError) throw captureError;
      const params = new URLSearchParams({loop: byId("replay-loop").checked ? "1" : "0"});
      const upload = file.files[0];
      if (source.value !== "file") params.set("sample", source.value);
      else if (upload) params.set("name", upload.name);
      else throw Error("Choose a recording.");
      const response = await fetch("/replay?" + params, {method: "POST", body: source.value === "file" ? upload : null});
      if (response.status === 413) throw Error("Choose a recording smaller than 16 MiB.");
      const data = await response.json();
      if (!response.ok) throw Error(data.error);
      if (openingCapture) linkedRun = data.id;
      if (source.value !== "file") {
        byId("readings-only").checked = true;
        if (trends) byId("trends").open = true;
        automaticJ1939 = !jMode.checked || automaticJ1939;
        setJ1939(true, true);
      }
      showReplay(data);
      busy = false;
      refreshReplay();
    } catch (error) {
      if (openingCapture) session?.abort();
      report.textContent = error.message;
      play.disabled = false;
      polling = setTimeout(() => refreshReplay(true), 1500);
    } finally {
      busy = false;
      toggle.disabled = closingCapture || !select.value;
    }
  };
  async function stopRun(id) {
    ++pollVersion;
    clearTimeout(polling);
    try { showReplay(await api("/replay/" + id + "/stop", {})); }
    catch (error) { report.textContent = error.message; }
    refreshReplay(true);
  }
  stopReplay.onclick = () => stopRun(replayId);
  pause.onclick = () => {
    frozen = frozen ? null : {trace: trace.slice(), groups: new Map(groups), pgnGroups: new Map(pgnGroups), total, dropped, wheel};
    trends?.pause(Boolean(frozen));
    pause.textContent = frozen ? "Resume display" : "Pause display";
    render();
  };
  function view(byGroup) {
    grouped = byGroup;
    byId("byte-legend").hidden = !grouped;
    byId("trace-view").setAttribute("aria-pressed", String(!grouped));
    byId("group-view").setAttribute("aria-pressed", String(grouped));
    render();
  }
  function setJ1939(enabled, showGroups) {
    jMode.checked = enabled;
    byId("trends").hidden = !(enabled && trends);
    trends?.refresh();
    byId("legend-context").textContent = enabled ?
      "changed since the previous frame with the same PGN and source (CAN ID and type for other traffic)." :
      "changed since the previous frame with the same CAN ID and frame type.";
    byId("group-view").textContent = enabled ? "By PGN and source" : "By CAN ID";
    byId("filter-label").textContent = enabled ? "Find message or signal" : "Filter CAN ID";
    byId("can-filter").placeholder = enabled ? "RPM, EEC1, 61444 or CAN ID" : "e.g. 7E8";
    view(showGroups);
  }
  jMode.onchange = () => {
    automaticJ1939 = false;
    setJ1939(jMode.checked, jMode.checked || grouped);
  };
  byId("trace-view").onclick = () => view(false);
  byId("group-view").onclick = () => view(true);
  byId("readings-only").onchange = render;
  byId("can-filter").oninput = render;
  window.addEventListener("pagehide", () => session?.abort());
})();
