"use strict";
const dbcEditor = (() => {
  let catalog = null;
  let messages = [];
  let appliedSha = "";
  let yamlConfig = null;
  let yamlEdited = false;
  let captureSha = "";
  let previewVersion = 0;
  let previewTimer;
  let liveVersion = 0;
  let liveOverride = false;
  const select = el("dbc-message");
  const draft = el("draft");

  function message() {
    return catalog?.messages.find(item => item.name === select.value);
  }
  function hex(item) {
    return "0x" + Number(item.id).toString(16).toUpperCase().padStart(item.extended ? 8 : 3, "0");
  }
  async function upload(file) {
    const response = await fetch("/dbc/import?filename=" + encodeURIComponent(file.name),
      {method: "POST", headers: {"Content-Type": "application/octet-stream"}, body: file});
    const data = await response.json();
    if (!response.ok) throw Error(data.error || response.statusText);
    return data;
  }
  function setCapture(data) {
    const next = data?.sha256 || "";
    el("live-dbc-status").textContent = data ? "Decoding with " + data.filename :
      "Using the running profile decoder when available.";
    if (next === captureSha) return;
    captureSha = next;
    window.dispatchEvent(new Event("capture-dbc-change"));
  }
  function values() {
    const entries = [];
    for (const input of el("dbc-signals").querySelectorAll("input")) {
      const value = input.value.trim();
      if (!value) continue;
      const number = Number(value);
      const wideInteger = Number.isInteger(number) && !Number.isSafeInteger(number);
      entries.push([input.dataset.signal, Number.isFinite(number) && !wideInteger ? number : value]);
    }
    return Object.fromEntries(entries);
  }
  function refreshPreview() {
    const version = ++previewVersion;
    clearTimeout(previewTimer);
    const current = message();
    if (!current || current.tx_issue) return;
    el("dbc-raw").textContent = "Calculating…";
    const body = {text: draft.value, sha256: catalog.sha256, name: current.name, values: values()};
    previewTimer = setTimeout(async () => {
      try {
        const result = await api("/dbc/preview", body);
        if (version === previewVersion) el("dbc-raw").textContent = result.raw;
      } catch (error) {
        if (version === previewVersion) el("dbc-raw").textContent = error.message;
      }
    }, 140);
  }
  function showMessage() {
    const item = message();
    el("dbc-settings").hidden = !item || Boolean(item.tx_issue);
    el("dbc-details").textContent = item ? [hex(item), item.extended ? "Extended" : "Standard",
      item.dlc + " bytes", item.tx_issue && "Unavailable for simulation", item.tx_issue].filter(Boolean).join(" · ") : "No messages match this search. Clear the search to browse the file.";
    const readOnly = el("dbc-readonly-signals");
    readOnly.hidden = !item?.tx_issue || !item.signals.length;
    if (!readOnly.hidden) readOnly.textContent = item.signals.map(signal =>
      signal.name + (signal.unit ? " (" + signal.unit + ")" : "")).join(" · ");
    const saved = messages.find(entry => entry.name === item?.name);
    el("dbc-signals").replaceChildren();
    if (item && !item.tx_issue) {
      el("dbc-period").value = saved?.period_ms ?? Math.max(10, Math.min(60000, item.period_ms || 100));
      el("dbc-apply").textContent = saved ? "Update message in working copy" : "Add message to working copy";
      for (const signal of item.signals) {
        const label = document.createElement("label");
        const range = signal.min == null && signal.max == null ? "" :
          " [" + (signal.min ?? "…") + " to " + (signal.max ?? "…") + "]";
        const mux = signal.mux ? " · " + signal.mux + " = " + signal.mux_ids.join(", ") : "";
        label.append(signal.name + (signal.unit ? " (" + signal.unit + ")" : "") + range + mux);
        const input = document.createElement("input");
        input.dataset.signal = signal.name;
        input.placeholder = String(signal.default ?? "");
        input.value = saved && Object.hasOwn(saved.values, signal.name) ? saved.values[signal.name] : "";
        input.spellcheck = false;
        label.append(input);
        el("dbc-signals").append(label);
      }
    }
    refreshPreview();
  }
  function filter(preferred) {
    const query = el("dbc-search").value.trim().toLowerCase();
    const choices = catalog.messages.filter(item =>
      [item.name, hex(item), ...item.signals.map(signal => signal.name)]
        .some(value => value.toLowerCase().includes(query)));
    const groups = new Map();
    for (const label of ["Simulate signals", "Transmit fixed payload", "Unavailable for simulation"]) {
      const group = document.createElement("optgroup");
      group.label = label;
      groups.set(label, group);
    }
    for (const item of choices) {
      const label = item.tx_issue ? "Unavailable for simulation" :
        item.signals.length ? "Simulate signals" : "Transmit fixed payload";
      groups.get(label).append(new Option(item.name + " · " + hex(item), item.name));
    }
    select.replaceChildren(...Array.from(groups.values()).filter(group => group.children.length));
    select.disabled = choices.length === 0;
    if (choices.some(item => item.name === preferred)) select.value = preferred;
    showMessage();
  }
  function renderList() {
    const list = el("dbc-configured");
    list.replaceChildren();
    el("dbc-attach").hidden = messages.length > 0 || appliedSha === catalog.sha256;
    if (!messages.length) list.textContent = "No messages added for transmission.";
    for (const entry of messages) {
      const row = document.createElement("li");
      row.append(entry.name + " · " + entry.period_ms + " ms");
      const remove = document.createElement("button");
      remove.textContent = "Remove";
      remove.setAttribute("aria-label", "Remove " + entry.name);
      remove.onclick = () => apply(entry.name, null);
      row.append(remove);
      list.append(row);
    }
  }
  function install(data, entries) {
    catalog = data;
    messages = entries;
    el("dbc-status").textContent = "";
    el("dbc-editor").hidden = !catalog;
    if (!catalog) return;
    el("dbc-database").textContent = catalog.filename + " · " + catalog.messages.length + " messages";
    el("dbc-sha").textContent = catalog.sha256;
    const editable = catalog.messages.filter(item => !item.tx_issue && item.signals.length).length;
    el("dbc-guide").textContent = editable ?
      "Choose from " + editable + " messages with editable signals, or search the file below." :
      "This file has no messages with editable signals. Choose a message to inspect its details.";
    el("dbc-search").value = "";
    filter(messages[0]?.name);
    renderList();
  }
  async function load(config) {
    let data = null;
    let missing = "";
    if (config) {
      try { data = await api("/dbc/" + config.sha256 + "?filename=" + encodeURIComponent(config.filename)); }
      catch (error) { missing = config.filename + " · " + error.message; }
    }
    appliedSha = config?.sha256 || "";
    yamlConfig = config;
    yamlEdited = false;
    install(data, config?.messages || []);
    if (missing) el("dbc-status").textContent = missing;
    if (!liveOverride) setCapture(data);
  }
  function selection(config, name) {
    return config?.messages.find(item => item.name === name);
  }
  async function apply(name, entry) {
    busyEditor(true);
    try {
      let base = messages;
      if (yamlEdited) {
        const {dbc: current} = await api("/dbc/config", {text: draft.value});
        if (current?.sha256 !== yamlConfig?.sha256 || current?.filename !== yamlConfig?.filename ||
            JSON.stringify(selection(current, name)) !== JSON.stringify(selection(yamlConfig, name)) ||
            (!name && current?.messages.length)) {
          await load(current);
          el("dbc-status").textContent = "Loaded the DBC settings you edited in YAML. Review them before updating the working copy.";
          return;
        }
        if (current?.sha256 === catalog.sha256) base = current.messages;
      }
      if (appliedSha && appliedSha !== catalog.sha256 &&
          !confirm("Replace the working copy’s DBC and its selected messages?")) return;
      const next = base.filter(item => item.name !== name);
      if (entry) {
        const index = base.findIndex(item => item.name === name);
        if (index < 0) next.push(entry);
        else next.splice(index, 0, entry);
      }
      const config = {filename: catalog.filename, sha256: catalog.sha256, messages: next};
      const result = await api("/dbc/draft", {text: draft.value, dbc: config});
      draft.value = result.text;
      appliedSha = config.sha256;
      yamlConfig = config;
      yamlEdited = false;
      messages = next;
      renderList();
      showMessage();
      el("dbc-status").textContent = "Working copy updated. Use Save as to keep this profile.";
    } catch (error) {
      el("dbc-status").textContent = error.message;
    } finally { busyEditor(false); }
  }
  el("dbc-browse").onclick = () => el("dbc-file").click();
  el("dbc-file").onchange = async event => {
    const file = event.target.files[0];
    if (!file) return;
    busyEditor(true);
    try {
      const data = await upload(file);
      const retained = data.sha256 === catalog?.sha256 ? messages :
        data.sha256 === yamlConfig?.sha256 ? yamlConfig.messages : [];
      install(data, retained);
      if (!liveOverride) setCapture(data);
    } catch (error) { el("dbc-status").textContent = error.message; }
    finally { event.target.value = ""; busyEditor(false); }
  };
  el("live-dbc-file").onchange = async event => {
    const file = event.target.files[0];
    if (!file) return;
    const version = ++liveVersion;
    try {
      const data = await upload(file);
      if (version === liveVersion) {
        liveOverride = true;
        el("live-dbc-reset").hidden = false;
        setCapture(data);
      }
    } catch (error) {
      if (version === liveVersion) el("live-dbc-status").textContent = error.message +
        (captureSha ? " · Previous decoder is still selected." : "");
    } finally { if (version === liveVersion) event.target.value = ""; }
  };
  el("live-dbc-reset").onclick = () => {
    ++liveVersion;
    liveOverride = false;
    el("live-dbc-reset").hidden = true;
    setCapture(catalog);
  };
  el("dbc-attach").onclick = () => apply(null, null);
  el("dbc-search").oninput = () => filter(select.value);
  select.onchange = showMessage;
  el("dbc-signals").oninput = refreshPreview;
  draft.addEventListener("input", () => {
    yamlEdited = true;
    refreshPreview();
  });
  el("dbc-apply").onclick = () => {
    if (!el("dbc-period").reportValidity()) return;
    const item = {name: select.value, period_ms: Number(el("dbc-period").value), values: values()};
    apply(item.name, item);
  };
  function confirmReview(review) {
    const dialog = el("dbc-review");
    el("review-target").textContent = "Start periodic transmission on isolated simulator bench " + review.interface + "?";
    el("review-messages").replaceChildren(...review.messages.map(item => {
      const row = document.createElement("li");
      const title = document.createElement("strong");
      title.textContent = item.name + " · " + hex(item) + (item.extended ? " extended" : " standard") +
        " · " + item.dlc + " bytes every " + item.period_ms + " ms";
      const settings = document.createElement("p");
      settings.textContent = Object.entries(item.values).map(([key, value]) => key + " = " + value).join(" · ");
      const raw = document.createElement("code");
      raw.textContent = item.raw;
      row.append(title, settings, raw);
      return row;
    }));
    el("review-conflicts").textContent = review.conflicts.length ? review.conflicts.join(" · ") :
      "No matching IDs observed during the " + review.observation_ms + " ms check.";
    dialog.returnValue = "cancel";
    return new Promise(resolve => {
      dialog.addEventListener("close", () => resolve(dialog.returnValue === "start"), {once: true});
      dialog.showModal();
    });
  }
  return {load, refreshPreview, confirmReview, get captureSha() { return captureSha; }};
})();
