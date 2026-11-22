// One capture clock for the three J1939 signals; keep senders separate.
export const liveTrends = (() => {
  const panel = document.getElementById("trends");
  const target = document.getElementById("trend-chart");
  const source = document.getElementById("trend-source");
  const notice = document.getElementById("trend-status");
  const choices = [...panel.querySelectorAll("input[type=checkbox]")];
  const signals = ["rpm", "pedal", "load"];
  const colors = ["#00655d", "#914600", "#5b48aa"];
  let samples = [], latest = null, origin = null, boundary = 0, paused = null;
  let plot = null, selectedSource = "", timer = null;

  function reset() {
    samples = [];
    latest = origin = paused = null;
    boundary = 0;
    selectedSource = "";
    refresh();
  }

  function observe(batch) {
    const passStart = batch.pass_start_us || 0;
    for (const frame of batch.frames) {
      const t = frame.time;
      if (origin === null) origin = t;
      if (t >= passStart && passStart > boundary) {
        boundary = passStart;
        if (origin < passStart) {
          samples = [];
          origin = passStart;
        }
      }
      latest = t;
      if (frame.j?.measurements) {
        samples.push({t, sa: frame.j.sa, values: frame.j.measurements});
      }
    }
    if (latest !== null) {
      let remove = Math.max(0, samples.length - 8192);
      while (remove < samples.length && samples[remove].t < latest - 30e6) remove++;
      if (remove) samples.splice(0, remove);
    }
    if (!paused) schedule();
  }

  function schedule() {
    if (panel.hidden || !panel.open || !panel.getClientRects().length || timer !== null) return;
    timer = setTimeout(() => { timer = null; refresh(); }, 150);
  }

  function refresh() {
    if (!panel.open || panel.hidden || !target.clientWidth) return;
    const data = paused || {samples, origin};
    const ids = [...new Set(data.samples.map(sample => sample.sa))].sort((a, b) => a - b).map(String);
    if ([...source.options].map(option => option.value).join() !== ids.join()) {
      source.replaceChildren(...ids.map(id => new Option("0x" + Number(id).toString(16).toUpperCase().padStart(2, "0"), id)));
    }
    source.value = ids.includes(selectedSource) ? selectedSource : (ids[0] ?? "");
    const rows = data.samples.filter(sample => String(sample.sa) === source.value);
    const aligned = [[], [], [], []];
    const last = [null, null, null];
    for (const sample of rows) {
      const t = (sample.t - data.origin) / 1e6;
      signals.forEach((signal, i) => {
        if (Object.hasOwn(sample.values, signal)) last[i] = sample.values[signal];
      });
      if (aligned[0].at(-1) === t) aligned.forEach(column => column.pop());
      aligned[0].push(t);
      last.forEach((value, i) => aligned[i + 1].push(value));
    }
    const enabled = choices.map(choice => choice.checked);
    notice.textContent = !rows.length ? "Waiting for EEC1 or EEC2 frames." :
      !enabled.some(Boolean) ? "Select a signal to plot." :
      enabled.every((show, i) => !show || aligned[i + 1].every(value => value === null)) ?
        "Selected signals unavailable from this source." : "";
    if (!plot) {
      const labels = ["Engine speed", "Accelerator pedal", "Engine load"];
      plot = new uPlot({
        width: target.clientWidth, height: 245, legend: {show: false},
        cursor: {drag: {x: false, y: false}, points: {show: false}},
        scales: {x: {time: false}, rpm: {range: (u, min, max) => [0, Math.max(1000, Math.ceil((max || 0) / 500) * 500)]},
          pct: {range: (u, min, max) => [0, Math.max(100, Math.ceil((max || 0) / 25) * 25)]}},
        axes: [{label: "Elapsed time (s)", labelSize: 20},
          {scale: "rpm", label: "Engine speed (rpm)", labelSize: 18, size: 54},
          {scale: "pct", side: 1, label: "Pedal / load (%)", labelSize: 18, size: 40, grid: {show: false}}],
        series: [{}, ...labels.map((label, i) => ({
          label, scale: i === 0 ? "rpm" : "pct", stroke: colors[i], width: 2,
          paths: uPlot.paths.stepped({align: 1}), points: {show: false}, spanGaps: false,
        }))],
      }, [[], [], [], []], target);
    }
    if (plot.width !== target.clientWidth) plot.setSize({width: target.clientWidth, height: 245});
    enabled.forEach((show, i) => {
      if (plot.series[i + 1].show !== show) plot.setSeries(i + 1, {show});
      const value = last[i];
      choices[i].parentElement.querySelector("output").textContent =
        value == null ? "—" : value + (i === 0 ? " rpm" : "%");
    });
    plot.setData(aligned);
  }

  source.onchange = () => { selectedSource = source.value; refresh(); };
  choices.forEach(choice => { choice.onchange = refresh; });
  panel.ontoggle = refresh;
  new ResizeObserver(schedule).observe(target);
  return {reset, observe, refresh, pause(active) {
    paused = active ? {samples: samples.slice(), origin} : null;
    refresh();
  }};
})();
