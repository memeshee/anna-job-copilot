import { AnnaAppRuntime } from "/static/anna-apps/_sdk/latest/index.js";

const DEV_TOOL_ID = "tool-dev-job-hunt-copilot"; // local-dev fallback
const TOOL_ID =
  (typeof window !== "undefined" &&
    window.__ANNA_TOOL_IDS__ &&
    window.__ANNA_TOOL_IDS__["job-copilot"]) ||
  DEV_TOOL_ID;

const $ = (id) => document.getElementById(id);
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let anna = null;
try {
  anna = await AnnaAppRuntime.connect();
} catch {
  /* standalone preview */
}

// ---------------------------------------------------------------------------
// storage helpers (tolerate a missing host so the page still renders)
// ---------------------------------------------------------------------------
const KEYS = {
  cv: "jhc:cv", jd: "jhc:jd", company: "jhc:company", role: "jhc:role",
  profileUrl: "jhc:profileUrl", jobUrl: "jhc:jobUrl", apps: "jhc:apps",
};
async function sget(key, fallback = "") {
  if (!anna) return fallback;
  try {
    const r = await anna.storage.get({ key });
    return r && r.value != null ? r.value : fallback;
  } catch {
    return fallback;
  }
}
async function sset(key, value) {
  if (!anna) return;
  try {
    await anna.storage.set({ key, value: String(value ?? "") });
  } catch {
    /* storage is best-effort */
  }
}

// ---------------------------------------------------------------------------
// tool call plumbing
// ---------------------------------------------------------------------------
async function invokeTool(toolId, args) {
  if (!anna) throw new Error("No host — open this app inside Anna.");
  return anna.tools.invoke({ tool_id: toolId, method: "run", args });
}

function unwrap(res) {
  // Production Nexus unwraps the {success,data} envelope and returns data
  // directly; scaffold paths may return the envelope as-is.
  if (res && typeof res === "object" && "success" in res) {
    if (!res.success) {
      const e = new Error(res.error || "tool_failed");
      e.data = res.data || null;
      throw e;
    }
    return res.data ?? {};
  }
  if (!res) throw new Error("tool_failed");
  return res;
}

async function call(args) {
  const tried = new Set();
  const candidates = [TOOL_ID, "bundled:job-copilot", DEV_TOOL_ID].filter(
    (id) => id && !tried.has(id) && (tried.add(id), true));
  let lastErr = null;
  for (const id of candidates) {
    try {
      return unwrap(await invokeTool(id, args));
    } catch (e) {
      lastErr = e;
      if (!/whitelist|permission_denied|not found|unknown tool|not available/i.test(e.message)) break;
    }
  }
  throw lastErr;
}

// ---------------------------------------------------------------------------
// UI state
// ---------------------------------------------------------------------------
const state = { profileSaved: false, scored: false, busy: false, apps: [] };
const profile = () => $("cv").value.trim();
const job = () => $("jd").value.trim();
const company = () => $("company").value.trim();
const role = () => $("role").value.trim();

function banner(msg, kind = "") {
  const b = $("banner");
  if (!msg) { b.className = "banner"; b.textContent = ""; return; }
  b.className = "banner show " + kind;
  b.textContent = msg;
}
function note(el, msg, kind = "") { el.className = "note " + kind; el.textContent = msg; }
function setOut(text, title) {
  $("out").textContent = text;
  if (title) $("resultTitle").textContent = title;
}

function gate(btn, hintEl, enabled, hintText, okText) {
  btn.disabled = !enabled || state.busy;
  btn.setAttribute("aria-disabled", String(btn.disabled));
  btn.title = btn.disabled ? hintText : okText || "";
  if (hintEl) {
    hintEl.textContent = btn.disabled && !state.busy ? hintText : (okText || "");
    hintEl.className = "hint" + (btn.disabled && !state.busy ? "" : " ok");
  }
}

function updateUI() {
  const hasProfile = state.profileSaved || profile().length >= 20;
  const hasJob = job().length >= 50;
  const hasPipeline = state.apps.length > 0;

  $("step1").className = "step " + (state.profileSaved ? "done" : hasProfile ? "on" : "");
  $("step2").className = "step " + (state.profileSaved && hasJob ? "on" : "");
  $("step3").className = "step " + (state.scored ? "on" : "");

  gate($("btnProfile"), $("hintProfile"),
    profile().length >= 20 || $("profileUrl").value.trim().length > 8,
    "Paste your CV text, pick a file, or enter a profile URL.",
    state.profileSaved ? "Profile saved — re-save if you edit the text." : "Reads your CV and locks in your skills.");

  const jobHint = "Save your profile and score a job first.";
  gate($("btnScore"), $("hintScore"), hasProfile && hasJob,
    hasProfile ? "Paste the job description (a few lines at least)." : "Save your profile first (step 1).",
    "Scores this job against your CV and lists matched / missing keywords.");
  for (const id of ["btnTailor", "btnCover", "btnInterview"]) gate($(id), null, hasProfile && hasJob, jobHint);
  $("hintTools").textContent = hasProfile && hasJob
    ? "Mirror bullets quotes your CV back with the job's keywords; the fact check tells you what wasn't in your CV."
    : jobHint;
  $("hintTools").className = "hint" + (hasProfile && hasJob ? " ok" : "");

  gate($("btnTrackAdd"), $("hintTrack"), company().length > 0 || role().length > 0 || hasJob,
    "Fill in company and role (step 2) first — one click adds exactly one entry.",
    "Adds this job to the pipeline once; tracking it again won't duplicate it.");
  gate($("btnQuiet"), null, hasPipeline,
    "Track an application first.", "Lists applications that have been quiet for more than 10 days.");
}

function setBusy(on, label) {
  state.busy = on;
  if (on) banner(label + " …");
  updateUI();
}

// ---------------------------------------------------------------------------
// file upload — PDF text extraction (bundled pdf.js, then the tool, then help)
// ---------------------------------------------------------------------------
async function fileToB64(file) {
  const buf = new Uint8Array(await file.arrayBuffer());
  let bin = "";
  for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
  return btoa(bin);
}

function goodText(text) {
  if (!text) return false;
  const letters = (text.match(/[A-Za-z]/g) || []).length;
  return text.trim().length >= 120 && letters >= 80;
}

async function pdfViaBundle(file) {
  const pdfjs = await import("./vendor/pdf.mjs");
  if (!globalThis.pdfjsWorker) globalThis.pdfjsWorker = await import("./vendor/pdf.worker.mjs");
  const doc = await pdfjs.getDocument({
    data: new Uint8Array(await file.arrayBuffer()),
    isEvalSupported: false, disableFontFace: true, useSystemFonts: false, verbosity: 0,
  }).promise;
  const pages = Math.min(doc.numPages, 20);
  const lines = [];
  for (let p = 1; p <= pages; p++) {
    const tc = await (await doc.getPage(p)).getTextContent();
    let line = [], lastY = null;
    const flush = (blank) => {
      const t = line.join("").trim();
      if (t) lines.push(t);
      else if (blank && lines.length && lines[lines.length - 1] !== "") lines.push("");
      line = [];
    };
    for (const it of tc.items) {
      const s = it.str ?? "";
      const y = it.transform ? it.transform[5] : null;
      const h = it.height || (it.transform ? Math.abs(it.transform[3]) : 0);
      if (lastY !== null && y !== null && Math.abs(y - lastY) > Math.max(2, h * 0.6)) {
        flush(Math.abs(y - lastY) > Math.max(4, h * 1.7)); // big gap ⇒ paragraph break
      }
      line.push(s);
      if (it.hasEOL) flush(false);
      lastY = y;
    }
    flush(false);
    if (pages > 1) lines.push("");
  }
  return lines.join("\n").replace(/[ \t]+/g, " ").replace(/\n{3,}/g, "\n\n")
    .replace(/[\uE000-\uF8FF]+/g, " ")   // icon-font glyphs from LinkedIn exports
    .replace(/[ \t]{2,}/g, " ").trim();
}

async function pdfViaTool(file) {
  const d = await call({ action: "extract_text", file_b64: await fileToB64(file) });
  return d.text || "";
}

async function handleFile(file) {
  if (!file) return;
  const name = file.name || "file";
  const isPdf = /\.pdf$/i.test(name) || file.type === "application/pdf";
  note($("fileMsg"), `Reading ${name} …`);

  if (!isPdf) {
    if (/\.(docx?|rtf|odt|pages)$/i.test(name)) {
      note($("fileMsg"), "Word/RTF files can’t be read directly — export to PDF, or copy the text and paste it above.", "warn");
      return;
    }
    try {
      const text = await file.text();
      const printable = (text.match(/[ -~\n\t]/g) || []).length / Math.max(1, text.length);
      if (printable < 0.7 || !goodText(text)) {
        note($("fileMsg"), "That file has no readable text (it may be an image or a scan). Paste your CV text above.", "warn");
        return;
      }
      $("cv").value = text.slice(0, 20000);
      note($("fileMsg"), `Loaded ${name} into the CV box — check it, then press “Save profile”.`, "ok");
      updateUI();
    } catch (e) {
      note($("fileMsg"), `Couldn’t read ${name} (${e.message}). Paste your CV text above instead.`, "err");
    }
    return;
  }

  let problem = "";
  try {
    const text = await pdfViaBundle(file);
    if (goodText(text)) {
      $("cv").value = text.slice(0, 20000);
      note($("fileMsg"), `Read ${name} (on-device) — check the text, then press “Save profile”.`, "ok");
      updateUI();
      return;
    }
    problem = "It has no selectable text";
  } catch (e) {
    problem = e.message || "the reader failed";
  }

  try {
    const text = await pdfViaTool(file);
    if (goodText(text)) {
      $("cv").value = text.slice(0, 20000);
      note($("fileMsg"), `Read ${name} — check the text, then press “Save profile”.`, "ok");
      updateUI();
      return;
    }
  } catch (e) {
    problem = e.message || problem;
  }

  note($("fileMsg"),
    `Couldn’t get text out of ${name} (${problem}). Open the PDF, select all (Ctrl/Cmd+A), copy and paste it into the box above — or export a text version.`, "err");
}

$("cvfile").addEventListener("change", (e) => handleFile(e.target.files?.[0]));
{
  const drop = $("drop");
  for (const ev of ["dragenter", "dragover"]) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("hover"); });
  for (const ev of ["dragleave", "drop"]) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("hover"); });
  drop.addEventListener("drop", (e) => handleFile(e.dataTransfer?.files?.[0]));
}

// ---------------------------------------------------------------------------
// actions
// ---------------------------------------------------------------------------
async function guard(label, fn) {
  setBusy(true, label);
  try {
    await fn();
  } catch (e) {
    const msg = e.message || "Something went wrong.";
    banner(msg, "err");
    setOut(`${label} failed.\n${msg}`.trim(), label);
    if (/linkedin/i.test(msg)) {
      note($("fileMsg"), "LinkedIn blocks bots: save your profile as PDF (More → Save to PDF), then drop it in the box above — or paste the CV text.", "warn");
    }
  } finally {
    setBusy(false, "");
    updateUI();
  }
}

$("btnProfile").addEventListener("click", () => guard("Saving profile", async () => {
  const data = await call({
    action: "set_profile", profile_text: profile(),
    profile_url: $("profileUrl").value.trim(),
  });
  if (data.text) $("cv").value = data.text;
  state.profileSaved = true;
  state.scored = false;
  await sset(KEYS.cv, profile());
  await sset(KEYS.profileUrl, $("profileUrl").value.trim());
  setOut(data.display_text, "Profile");
  note($("fileMsg"), "Profile saved. Next: paste the job description and press “Score fit”.", "ok");
  banner("Profile saved.", "ok");
}));

$("btnScore").addEventListener("click", () => guard("Scoring fit", async () => {
  const data = await call({
    action: "score", profile_text: profile(), job_text: job(),
    job_url: $("jobUrl").value.trim(), company: company(), role: role(),
  });
  state.scored = true;
  await saveJobInputs();
  setOut(data.display_text, "Fit score");
  updateUI();
}));

for (const [id, action, title] of [
  ["btnTailor", "tailor", "Mirrored bullets"],
  ["btnCover", "cover", "Cover letter"],
  ["btnInterview", "interview", "Interview pack"],
]) {
  $(id).addEventListener("click", () => guard(title, async () => {
    const data = await call({
      action, profile_text: profile(), job_text: job(),
      job_url: $("jobUrl").value.trim(), company: company(), role: role(),
    });
    setOut(data.display_text, title);
    const footnote = action === "tailor"
      ? "Bold? Every line above traces to a line in your CV — [ADD IF TRUE] items are yours to confirm."
      : action === "interview"
        ? "[ADD IF TRUE] fields are placeholders for true detail — the questions and STAR seeds come from your CV."
        : "Read it over before sending — nothing is auto-sent.";
    banner(footnote, "ok");
  }));
}

async function saveJobInputs() {
  await sset(KEYS.jd, $("jd").value);
  await sset(KEYS.company, company());
  await sset(KEYS.role, role());
  await sset(KEYS.jobUrl, $("jobUrl").value.trim());
}

$("btnTrackAdd").addEventListener("click", () => guard("Tracking", async () => {
  const data = await call({
    action: "track", op: "add", apps_json: JSON.stringify(state.apps),
    app_ref: JSON.stringify({ company: company(), role: role(), job_url: $("jobUrl").value.trim() }),
    job_text: job(),
  });
  state.apps = data.apps || state.apps;
  await sset(KEYS.apps, JSON.stringify(state.apps));
  renderApps();
  setOut(data.display_text, "Pipeline");
  banner(data.message || "Tracked.", "ok");
}));

$("btnQuiet").addEventListener("click", () => guard("Checking pipeline", async () => {
  const data = await call({ action: "track", op: "quiet", apps_json: JSON.stringify(state.apps) });
  if (data.apps) state.apps = data.apps;
  setOut(data.display_text, "Needs a nudge");
}));

$("btnCopy").addEventListener("click", async () => {
  const text = $("out").textContent || "";
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); } catch { /* ignore */ }
    ta.remove();
  }
  const b = $("btnCopy");
  b.textContent = "Copied ✓";
  setTimeout(() => (b.textContent = "Copy"), 1400);
});

// ---------------------------------------------------------------------------
// pipeline rendering
// ---------------------------------------------------------------------------
function renderApps() {
  const box = $("apps");
  if (!state.apps.length) {
    box.innerHTML = "Empty — score a job, then track it.";
    updateUI();
    return;
  }
  const now = Math.floor(Date.now() / 1000);
  box.innerHTML = state.apps
    .map((a, i) => {
      const days = Math.max(0, Math.floor((now - (a.applied_at || now)) / 86400));
      return `<div class="approw">
        <div class="who"><b>${esc(a.company || "?")}</b> — ${esc(a.role || "?")}</div>
        <span class="tag">${esc(a.status || "applied")}</span>
        <span class="tag">${days}d</span>
        <button class="mini" data-i="${i}" title="Remove from pipeline">Remove</button>
      </div>`;
    })
    .join("");
  box.querySelectorAll("button.mini").forEach((b) =>
    b.addEventListener("click", () => removeApp(Number(b.dataset.i))));
  updateUI();
}

async function removeApp(i) {
  const ref = state.apps[i];
  if (!ref) return;
  await guard("Removing", async () => {
    const data = await call({
      action: "track", op: "remove", apps_json: JSON.stringify(state.apps),
      app_ref: JSON.stringify({ id: ref.id, company: ref.company, role: ref.role }),
    });
    state.apps = data.apps || state.apps.filter((_, j) => j !== i);
    await sset(KEYS.apps, JSON.stringify(state.apps));
    renderApps();
    setOut(data.display_text, "Pipeline");
  });
}

// ---------------------------------------------------------------------------
// boot
// ---------------------------------------------------------------------------
if (anna) {
  try { await anna.window.set_title({ title: "Job Hunt Copilot" }); } catch { /* optional */ }
  $("cv").value = await sget(KEYS.cv, "");
  $("jd").value = await sget(KEYS.jd, "");
  $("company").value = await sget(KEYS.company, "");
  $("role").value = await sget(KEYS.role, "");
  $("profileUrl").value = await sget(KEYS.profileUrl, "");
  $("jobUrl").value = await sget(KEYS.jobUrl, "");
  state.profileSaved = profile().length >= 50;
  try { state.apps = JSON.parse((await sget(KEYS.apps, "[]")) || "[]"); } catch { state.apps = []; }
  if (!Array.isArray(state.apps)) state.apps = [];
} else {
  banner("Standalone preview — open this app inside Anna to run the pipeline.", "err");
}

for (const id of ["cv", "jd", "company", "role", "profileUrl", "jobUrl"]) {
  $(id).addEventListener("input", updateUI);
}
renderApps();
updateUI();
