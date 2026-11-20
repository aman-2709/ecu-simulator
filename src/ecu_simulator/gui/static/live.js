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
  let session = null, frozen = null, grouped = false, renderTimer = null;
  let catalogVersion = 0;

  async function showTab(live) {
    byId("live-panel").hidden = !live;
    byId("sim-panel").hidden = live;
    byId("live-tab").setAttribute("aria-pressed", String(live));
    byId("sim-tab").setAttribute("aria-pressed", String(!live));
    const version = ++catalogVersion;
    if (!live) return;
    render();
    if (session) return;
    try {
      const catalog = await api("/profiles");
      if (version !== catalogVersion || session) return;
      const previous = select.value;
      select.replaceChildren(...catalog.interfaces.map(name => new Option(name, name)));
      for (const name of [previous, run.interface, byId("interface").value, "vcan0"]) {
        if (catalog.interfaces.includes(name)) { select.value = name; break; }
      }
      toggle.disabled = !select.value;
      if (!select.value) status.textContent = "No CAN interfaces found";
    } catch (error) {
      if (version === catalogVersion) status.textContent = error.message;
    }
  }
  byId("sim-tab").onclick = () => showTab(false);
  byId("live-tab").onclick = () => showTab(true);

  function addBatch(batch) {
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
      groups.delete(key);
      groups.set(key, {...frame, changed, count: (previous?.count || 0) + 1});
      if (groups.size > limit) groups.delete(groups.keys().next().value);
      trace.push(frame);
    }
    total += batch.frames.length;
    if (trace.length > limit) trace.splice(0, trace.length - limit);
    if (!frozen && !byId("live-panel").hidden && !renderTimer) {
      renderTimer = setTimeout(() => { renderTimer = null; render(); }, 150);
    }
  }

  // Interpret only recognized OBD single frames on the conventional 11-bit IDs.
  // Unknown traffic keeps its original bytes and neutral styling.
  function describe(frame) {
    if (frame.kind === "ERR") return {name: "CAN controller error", label: "Error", role: "error"};
    if (frame.kind === "RTR") return {label: "Remote"};
    const [length, service, pid, a, b] = frame.bytes;
    if (length > frame.bytes.length - 1 || frame.id.length !== 3) return {};
    const id = parseInt(frame.id, 16);
    const speed = pid === 0x0D;
    if (!speed && pid !== 0x10) return {};
    const name = speed ? "Vehicle speed" : "Mass airflow";
    if (service === 0x01 && length === 2 && id >= 0x7DF && id <= 0x7E7) {
      return {name, label: "Request", role: "request", end: length + 1};
    }
    if (service === 0x41 && length === (speed ? 3 : 4) && id >= 0x7E8 && id <= 0x7EF) {
      const value = speed ? a + " km/h" : ((256 * a + b) / 100).toFixed(2) + " g/s";
      return {name, value, label: "Reply", role: "reply", end: length + 1};
    }
    return {};
  }

  function render() {
    const snapshot = frozen || {trace, groups, total, dropped};
    const headings = grouped ? ["CAN ID", "Count", "Type", "Signal", "Value", "Length", "Latest bytes"] : ["Time (UTC)", "CAN ID", "Type", "Signal", "Value", "Length", "Bytes"];
    byId("frame-head").replaceChildren(...headings.map(label => {
      const th = document.createElement("th"); th.textContent = label; return th;
    }));
    const filter = byId("can-filter").value.trim().replace(/^0x/i, "").toUpperCase();
    const frames = grouped ? [...snapshot.groups.values()].sort((a, b) => a.id.localeCompare(b.id)) : snapshot.trace;
    const rows = document.createDocumentFragment();
    for (const frame of frames) {
      if (filter && (!/^[0-9A-F]{1,8}$/.test(filter) || parseInt(frame.id, 16) !== parseInt(filter, 16))) continue;
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
      const info = frame.info;
      row.className = info.role || "";
      const type = row.insertCell();
      if (info.label) {
        const label = document.createElement("span");
        label.className = "frame-type";
        label.textContent = info.label;
        type.append(label);
      } else type.textContent = "—";
      row.insertCell().textContent = info.name || "—";
      const value = row.insertCell();
      if (info.value !== undefined) {
        const reading = document.createElement(info.changed ? "mark" : "strong");
        reading.textContent = info.value;
        if (info.changed) reading.title = "Changed since the last reply for this signal";
        value.append(reading);
      } else value.textContent = "—";
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
      (grouped ? " IDs shown" : " frames shown") + (snapshot.dropped ? " · " + snapshot.dropped + " socket drops" : "");
    if (follow && !grouped) scroll.scrollTop = scroll.scrollHeight;
  }

  async function capture() {
    if (session) { session.abort(); return; }
    const controller = new AbortController();
    session = controller;
    toggle.textContent = "Stop capture";
    select.disabled = true;
    status.textContent = "Connecting";
    try {
      const response = await fetch("/capture", {method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({interface: select.value}), signal: controller.signal});
      if (!response.ok) throw Error((await response.json()).error || response.statusText);
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
          if (packet.ready) {
            trace = []; groups = new Map(); previousValues.clear(); total = 0; dropped = 0; frozen = null;
            pause.disabled = false; pause.textContent = "Pause display";
            status.textContent = "Capturing on " + select.value;
            render();
          } else addBatch(packet);
        }
      }
    } catch (error) {
      status.textContent = controller.signal.aborted ? "Stopped" : error.message;
    } finally {
      controller.abort();
      session = null; select.disabled = false; frozen = null; pause.disabled = true;
      pause.textContent = "Pause display"; toggle.textContent = "Start capture"; render();
    }
  }
  toggle.onclick = capture;
  pause.onclick = () => {
    frozen = frozen ? null : {trace: trace.slice(), groups: new Map(groups), total, dropped};
    pause.textContent = frozen ? "Resume display" : "Pause display";
    render();
  };
  function view(byGroup) {
    grouped = byGroup;
    byId("trace-view").setAttribute("aria-pressed", String(!grouped));
    byId("group-view").setAttribute("aria-pressed", String(grouped));
    render();
  }
  byId("trace-view").onclick = () => view(false);
  byId("group-view").onclick = () => view(true);
  byId("can-filter").oninput = render;
  window.addEventListener("pagehide", () => session?.abort());
})();
