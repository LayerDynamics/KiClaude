// ki-mcp-pcb viewer — vanilla JS, no build step.

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

// ── tabs ─────────────────────────────────────────────────────────
$$(".tab").forEach((tab) => {
  tab.addEventListener("click", (e) => {
    e.preventDefault();
    $$(".tab").forEach((t) => t.classList.remove("active"));
    $$(".tab-pane").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById(`tab-${tab.dataset.tab}`).classList.add("active");
  });
});

// ── version tag ──────────────────────────────────────────────────
fetch("/api/version").then((r) => r.json()).then((v) => {
  $("#version-tag").textContent =
    `core v${v.core_version} · CIR v${v.cir_version}`;
});

// ── drag/drop + file upload ──────────────────────────────────────
const dropZone = $("#drop-zone");
const fileInput = $("#file-input");

["dragenter", "dragover"].forEach((evt) =>
  dropZone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropZone.classList.add("over");
  }),
);
["dragleave", "drop"].forEach((evt) =>
  dropZone.addEventListener(evt, () => dropZone.classList.remove("over")),
);
dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  if (e.dataTransfer.files.length) uploadAndValidate(e.dataTransfer.files[0]);
});
fileInput.addEventListener("change", () => {
  if (fileInput.files.length) uploadAndValidate(fileInput.files[0]);
});

async function uploadAndValidate(file) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/api/validate", { method: "POST", body: form });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    renderError(detail.detail || "validation failed");
    return;
  }
  const data = await res.json();
  renderResults(data);
}

function renderError(msg) {
  $("#validate-results").classList.remove("hidden");
  $("#validate-results").innerHTML =
    `<div class="banner error">${escapeHtml(msg)}</div>`;
}

function renderResults(data) {
  $("#validate-results").classList.remove("hidden");
  renderBoardSummary(data.board);
  renderValidationReport(data.validation);
  renderComponents(data.board.components);
  renderNets(data.board.nets);
  renderBom(data.bom);
  renderSourcing(data.sourcing);
}

// ── renderers ────────────────────────────────────────────────────
function renderBoardSummary(b) {
  const ok = b.signoff;
  $("#board-summary").innerHTML = `
    <div class="section-header">
      <h2>${escapeHtml(b.name)}</h2>
      <span class="count">CIR v${escapeHtml(b.cir_version)}</span>
    </div>
    ${b.description ? `<p class="muted">${escapeHtml(b.description)}</p>` : ""}
    <table>
      <tr><td>Fab target</td><td><code>${escapeHtml(b.fab.name)}</code></td>
          <td>${b.stackup.layer_count}-layer · ${b.stackup.finished_thickness_mm} mm</td></tr>
      <tr><td>Components</td><td>${b.components.length}</td>
          <td>Nets: ${b.nets.length}</td></tr>
      <tr><td>Sign-off</td><td>
        rf: ${pill(ok.rf_reviewed ? "ok" : "info", ok.rf_reviewed)}
        ddr: ${pill(ok.ddr_reviewed ? "ok" : "info", ok.ddr_reviewed)}
        bga: ${pill(ok.bga_fanout_reviewed ? "ok" : "info", ok.bga_fanout_reviewed)}
      </td><td>${ok.reviewer ? "by " + escapeHtml(ok.reviewer) : ""}</td></tr>
    </table>
  `;
}

function renderValidationReport(report) {
  const issues = report.issues || [];
  const errors = issues.filter((i) => i.severity === "error").length;
  const warnings = issues.filter((i) => i.severity === "warning").length;
  const banner = errors === 0
    ? `<div class="banner ok">Validation clean — ${warnings} warning${warnings === 1 ? "" : "s"}.</div>`
    : `<div class="banner error">Validation failed — ${errors} error${errors === 1 ? "" : "s"}, ${warnings} warning${warnings === 1 ? "" : "s"}.</div>`;
  const rows = issues.map((i) => `
    <tr>
      <td>${pill(i.severity, i.severity)}</td>
      <td><code>${escapeHtml(i.code)}</code></td>
      <td>${escapeHtml(i.where || "")}</td>
      <td>${escapeHtml(i.message)}</td>
    </tr>
  `).join("");
  $("#validation-report").innerHTML = `
    <div class="section-header"><h2>Validation</h2><span class="count">${issues.length} issue(s)</span></div>
    ${banner}
    ${issues.length === 0 ? "" : `<table>
      <thead><tr><th>Severity</th><th>Code</th><th>Where</th><th>Message</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`}
  `;
}

function renderComponents(components) {
  const rows = components.map((c) => `
    <tr>
      <td><code>${escapeHtml(c.refdes)}</code></td>
      <td>${escapeHtml(c.mpn)}</td>
      <td>${escapeHtml(c.value || "")}</td>
      <td>${escapeHtml(c.footprint || "—")}</td>
      <td>${c.partition ? pill("info", c.partition) : ""}
          ${c.is_bridge ? pill("warning", "bridge") : ""}
          ${c.bga_pitch_mm ? pill("info", c.bga_pitch_mm + "mm BGA") : ""}</td>
    </tr>
  `).join("");
  $("#components").innerHTML = `
    <div class="section-header"><h2>Components</h2><span class="count">${components.length}</span></div>
    <table>
      <thead><tr><th>Refdes</th><th>MPN</th><th>Value</th><th>Footprint</th><th>Tags</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function renderNets(nets) {
  const rows = nets.map((n) => `
    <tr>
      <td><code>${escapeHtml(n.name)}</code></td>
      <td>${pill(netClassPill(n.net_class), n.net_class)}</td>
      <td>${n.members.length}</td>
      <td>${n.target_impedance_ohm ? n.target_impedance_ohm + " Ω" : ""}
          ${n.length_match_group ? pill("info", "group: " + n.length_match_group) : ""}
          ${n.diff_pair_with ? pill("info", "diff w/ " + n.diff_pair_with) : ""}
          ${n.topology ? pill("info", n.topology) : ""}</td>
    </tr>
  `).join("");
  $("#nets").innerHTML = `
    <div class="section-header"><h2>Nets</h2><span class="count">${nets.length}</span></div>
    <table>
      <thead><tr><th>Name</th><th>Class</th><th>Members</th><th>Constraints</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function renderBom(bom) {
  const rows = bom.map((r) => `
    <tr>
      <td>${escapeHtml(r.comment)}</td>
      <td><code>${escapeHtml(r.designator)}</code></td>
      <td>${escapeHtml(r.footprint)}</td>
      <td>${escapeHtml(r.mpn)}</td>
      <td>${escapeHtml(r.lcsc || "")}</td>
      <td>${r.quantity}</td>
    </tr>
  `).join("");
  $("#bom").innerHTML = `
    <div class="section-header"><h2>BOM</h2><span class="count">${bom.length} unique part(s)</span></div>
    <table>
      <thead><tr><th>Comment</th><th>Designator</th><th>Footprint</th><th>MPN</th><th>LCSC</th><th>Qty</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function renderSourcing(entries) {
  const rows = entries.map((e) => `
    <tr>
      <td><code>${escapeHtml(e.refdes)}</code></td>
      <td>${escapeHtml(e.mpn)}</td>
      <td>${pill(e.status === "missing" ? "error" : e.status === "in_stock_jlc" ? "ok" : "warning", e.status)}</td>
      <td>${escapeHtml(e.lcsc || "")}</td>
      <td>${e.unit_price_usd !== null && e.unit_price_usd !== undefined ? "$" + e.unit_price_usd.toFixed(4) : ""}</td>
      <td>${e.stock !== null && e.stock !== undefined ? e.stock : ""}</td>
    </tr>
  `).join("");
  $("#sourcing").innerHTML = `
    <div class="section-header"><h2>Sourcing</h2><span class="count">${entries.length}</span></div>
    <table>
      <thead><tr><th>Refdes</th><th>MPN</th><th>Status</th><th>LCSC</th><th>Price</th><th>Stock</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

// ── impedance tab ────────────────────────────────────────────────
$("#impedance-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/api/impedance", { method: "POST", body: form });
  const data = await res.json();
  const rows = data.rows.map((r) => {
    const dev = r.achieved_ohm !== null
      ? Math.abs(r.achieved_ohm - r.target_ohm) / r.target_ohm * 100
      : null;
    const status = dev === null ? pill("warning", "no geom")
      : dev > 20 ? pill("error", dev.toFixed(0) + "% off")
      : dev > 10 ? pill("warning", dev.toFixed(0) + "% off")
      : pill("ok", "in band");
    return `<tr>
      <td><code>${escapeHtml(r.net)}</code></td>
      <td>${r.target_ohm} Ω</td>
      <td>${r.achieved_ohm !== null ? r.achieved_ohm.toFixed(1) + " Ω" : "—"}</td>
      <td>${status}</td>
      <td>${r.trace_width_mm ?? "—"} / ${r.trace_spacing_mm ?? "—"} mm</td>
      <td>${r.cpwg_gap_mm ? "CPWG gap " + r.cpwg_gap_mm + " mm" : r.diff_pair_with ? "diff w/ " + r.diff_pair_with : "single"}</td>
    </tr>`;
  }).join("");
  $("#impedance-results").innerHTML = data.rows.length === 0
    ? `<p class="muted">No nets declare <code>target_impedance_ohm</code>.</p>`
    : `<table>
        <thead><tr><th>Net</th><th>Target</th><th>Achieved</th><th>Status</th><th>w / s</th><th>Mode</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
});

// ── diff tab ─────────────────────────────────────────────────────
$("#diff-go").addEventListener("click", async () => {
  const left = $("#diff-left").files[0];
  const right = $("#diff-right").files[0];
  if (!left || !right) {
    $("#diff-results").innerHTML = `<p class="muted">Pick two files.</p>`;
    return;
  }
  const form = new FormData();
  form.append("left", left);
  form.append("right", right);
  const res = await fetch("/api/diff", { method: "POST", body: form });
  const d = await res.json();
  const rows = [];
  d.components_added.forEach((r) => rows.push(`<div class="diff-row added">+ component <code>${escapeHtml(r)}</code></div>`));
  d.components_removed.forEach((r) => rows.push(`<div class="diff-row removed">- component <code>${escapeHtml(r)}</code></div>`));
  d.component_changes.forEach((c) => rows.push(`<div class="diff-row changed">~ <code>${escapeHtml(c.refdes)}.${escapeHtml(c.field)}</code>: ${escapeHtml(c.left)} → ${escapeHtml(c.right)}</div>`));
  d.nets_added.forEach((n) => rows.push(`<div class="diff-row added">+ net <code>${escapeHtml(n)}</code></div>`));
  d.nets_removed.forEach((n) => rows.push(`<div class="diff-row removed">- net <code>${escapeHtml(n)}</code></div>`));
  d.net_changes.forEach((c) => rows.push(`<div class="diff-row changed">~ net <code>${escapeHtml(c.name)}.${escapeHtml(c.field)}</code>: ${escapeHtml(c.left)} → ${escapeHtml(c.right)}</div>`));

  $("#diff-results").innerHTML = `
    <div class="banner ${d.identical ? "ok" : "error"}">${escapeHtml(d.summary)}</div>
    ${rows.join("")}
  `;
});

// ── PCB preview tab ──────────────────────────────────────────────
$("#pcb-load").addEventListener("click", () => {
  const url = $("#pcb-url").value.trim();
  if (!url) return;
  // Lazy-load KiCanvas from CDN
  if (!window.__kicanvas_loaded) {
    const s = document.createElement("script");
    s.type = "module";
    s.src = "https://kicanvas.org/kicanvas/kicanvas.js";
    document.head.appendChild(s);
    window.__kicanvas_loaded = true;
  }
  $("#pcb-canvas-host").innerHTML = `
    <kicanvas-embed src="${escapeAttr(url)}" controls="full"></kicanvas-embed>
  `;
});

// ── helpers ──────────────────────────────────────────────────────
function pill(cls, text) {
  return `<span class="pill ${cls}">${escapeHtml(String(text))}</span>`;
}

function netClassPill(cls) {
  switch (cls) {
    case "ground":       return "info";
    case "power":        return "info";
    case "high_speed":
    case "differential":
    case "rf":           return "warning";
    default:             return "info";
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}
function escapeAttr(s) { return escapeHtml(s); }
