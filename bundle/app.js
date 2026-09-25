import { AnnaAppRuntime } from "/static/anna-apps/_sdk/latest/index.js";

const TOOL_ID =
  (typeof window !== "undefined" &&
    window.__ANNA_TOOL_IDS__ &&
    window.__ANNA_TOOL_IDS__["job-copilot"]) ||
  "tool-dev-job-hunt-copilot"; // local-dev fallback
const $ = (id) => document.getElementById(id);
const out = (t) => { $("out").textContent = t; };

let anna = null;
try { anna = await AnnaAppRuntime.connect(); } catch { /* standalone preview */ }

if (anna) {
  await anna.window.set_title({ title: "Job Hunt Copilot" });
  const cv = await anna.storage.get({ key: "jhc:cv" }).catch(() => null);
  if (cv?.value) $("cv").value = cv.value;
  renderApps();
} else {
  out("Standalone preview (no host). Open inside Anna to run.");
}

async function call(args) {
  if (!anna) throw new Error("No host — open inside Anna.");
  const res = await anna.tools.invoke({ tool_id: TOOL_ID, method: "run", args });
  // Production Nexus unwraps the {success,data} envelope and returns data
  // directly; standalone/scaffold paths may return the envelope as-is.
  if (res && typeof res === "object" && "success" in res) {
    if (!res.success) throw new Error(res.error || "tool_failed");
    return res.data ?? {};
  }
  if (!res) throw new Error("tool_failed");
  return res;
}

function profile() { return $("cv").value.trim(); }
function job() { return $("jd").value.trim(); }
function company() { return $("company").value.trim(); }
function role() { return $("role").value.trim(); }

async function getApps() {
  if (!anna) return [];
  const r = await anna.storage.get({ key: "jhc:apps" }).catch(() => null);
  try { return JSON.parse(r?.value || "[]"); } catch { return []; }
}
async function setApps(apps) {
  await anna.storage.set({ key: "jhc:apps", value: JSON.stringify(apps) });
  renderApps();
}
async function renderApps() {
  const apps = await getApps();
  $("apps").innerHTML = apps.length
    ? apps.map((a) => `• ${a.company || "?"} — ${a.role || "?"} [${a.status || "applied"}]`).join("<br>")
    : "Empty — score a job, then track it.";
}

$("cvfile").addEventListener("change", async (e) => {
  const f = e.target.files?.[0];
  if (!f) return;
  const text = await f.text();
  const printable = (text.match(/[ -~\n\t]/g) || []).length / Math.max(1, text.length);
  if (printable < 0.7) {
    out("That file looks binary (scanned PDF?). Paste the CV text into the box instead.");
    return;
  }
  $("cv").value = text.slice(0, 12000);
  out("File loaded into the CV box — review, then Save profile.");
});

$("btnProfile").addEventListener("click", async () => {
  try {
    out("Parsing profile…");
    let text = profile();
    const url = $("profileUrl").value.trim();
    const data = await call({ action: "set_profile", profile_text: text, job_url: url });
    if (anna) await anna.storage.set({ key: "jhc:cv", value: text });
    out(data.display_text);
  } catch (e) { out("Error: " + e.message); }
});

$("btnScore").addEventListener("click", async () => {
  try {
    out("Scoring…");
    const data = await call({ action: "score", profile_text: profile(), job_text: job(), job_url: $("jobUrl").value.trim(), company: company(), role: role() });
    out(data.display_text);
  } catch (e) { out("Error: " + e.message); }
});

for (const [id, action] of [["btnTailor", "tailor"], ["btnCover", "cover"], ["btnInterview", "interview"]]) {
  $(id).addEventListener("click", async () => {
    try {
      out("Working…");
      const data = await call({ action, profile_text: profile(), job_text: job(), job_url: $("jobUrl").value.trim(), company: company(), role: role() });
      out(data.display_text);
    } catch (e) { out("Error: " + e.message); }
  });
}

$("btnTrackAdd").addEventListener("click", async () => {
  try {
    const apps = await getApps();
    const data = await call({ action: "track", op: "add", apps_json: JSON.stringify(apps), app_ref: JSON.stringify({ company: company(), role: role() }) });
    await setApps(data.apps);
    out(data.display_text);
  } catch (e) { out("Error: " + e.message); }
});

$("btnQuiet").addEventListener("click", async () => {
  try {
    const apps = await getApps();
    const data = await call({ action: "track", op: "quiet", apps_json: JSON.stringify(apps) });
    out(data.display_text);
  } catch (e) { out("Error: " + e.message); }
});
