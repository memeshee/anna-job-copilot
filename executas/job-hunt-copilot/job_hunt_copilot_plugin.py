#!/usr/bin/env python3
"""job-hunt-copilot — Executa stdio tool plugin (single-dispatcher).

Actions (method="run", discriminator "action"):
  set_profile | score | tailor | cover | interview | track

Protocol: JSON-RPC 2.0 over stdio. v2 handshake (initialize) advertises
sampling; every action first tries host sampling (llm.sample) for quality,
then falls back to deterministic local logic so the app works everywhere
(local harness, grant off, offline review).

Envelope contract: invoke always returns {"success": bool, "data"/"error"}.
Logs go to stderr only — stdout is strict JSON-RPC transport.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
import uuid
from typing import Any

VERSION = "0.1.2"

MANIFEST: dict[str, Any] = {
    "display_name": "Job Hunt Copilot",
    "version": VERSION,
    "description": (
        "Turns a 60-minute tailor into 10 minutes: parses your CV, scores fit "
        "against a job post, mirrors bullets, drafts a cover letter, builds an "
        "interview pack, and tracks every application with follow-up nudges."
    ),
    "author": "kiter",
    "homepage": "https://dorahacks.io/hackathon/2349/buidl",
    "license": "MIT",
    "tags": ["jobs", "resume", "productivity", "anna-app"],
    "host_capabilities": ["llm.sample"],
    "tools": [
        {
            "name": "run",
            "description": (
                "Job-search pipeline. action: set_profile | score | tailor | "
                "cover | interview | track. set_profile parses a CV once; "
                "score/tailor/cover/interview need profile_text + job_text; "
                "track manages the application list."
            ),
            "parameters": [
                {"name": "action", "type": "string",
                 "description": "One of: set_profile, score, tailor, cover, interview, track.",
                 "required": True},
                {"name": "profile_text", "type": "string",
                 "description": "CV作为纯文本 (or parsed profile summary). Required for score/tailor/cover/interview.",
                 "required": False, "default": ""},
                {"name": "job_text", "type": "string",
                 "description": "Job description text. Alternatively pass job_url.",
                 "required": False, "default": ""},
                {"name": "job_url", "type": "string",
                 "description": "Job posting or LinkedIn URL. Server-side fetch attempted; LinkedIn often blocks bots — paste text as fallback.",
                 "required": False, "default": ""},
                {"name": "company", "type": "string",
                 "description": "Company name (cover letter + tracker).", "required": False, "default": ""},
                {"name": "role", "type": "string",
                 "description": "Role title (cover letter + tracker).", "required": False, "default": ""},
                {"name": "apps_json", "type": "string",
                 "description": "JSON array of tracked applications (for track action).",
                 "required": False, "default": "[]"},
                {"name": "op", "type": "string",
                 "description": "track sub-op: add | update | list | quiet. Default list.",
                 "required": False, "default": "list"},
                {"name": "app_ref", "type": "string",
                 "description": "JSON object of one application (for track add/update).",
                 "required": False, "default": "{}"},
                {"name": "note", "type": "string",
                 "description": "Status note / outcome (for track update).", "required": False, "default": ""},
            ],
        }
    ],
    "runtime": {"type": "uv", "min_version": "0.1.0"},
}

# ---------------------------------------------------------------------------
# Sampling (v2 reverse-RPC) with graceful fallback
# ---------------------------------------------------------------------------

_v2_live = False
_pending: dict[str, Any] = {}
_queued: list[dict[str, Any]] = []


def _send(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _log(msg: str) -> None:
    print(f"[job-hunt-copilot] {msg}", file=sys.stderr)


def try_sample(ctx: dict[str, Any], system: str, user: str,
               max_tokens: int = 1500,
               response_format: dict[str, Any] | None = None) -> str | None:
    """Ask the host LLM. Returns text or None when unavailable."""
    if not _v2_live or not ctx.get("sampling_token"):
        return None
    rid = uuid.uuid4().hex
    params: dict[str, Any] = {
        "messages": [{"role": "user", "content": {"type": "text", "text": user}}],
        "maxTokens": max_tokens,
        "systemPrompt": system,
        "temperature": 0.3,
        "includeContext": "none",
        "metadata": {"executa_invoke_id": ctx.get("invoke_id", "")},
    }
    if response_format:
        params["responseFormat"] = response_format
        params["onUnsupported"] = "json_object"
    _send({"jsonrpc": "2.0", "id": rid, "method": "sampling/createMessage",
           "params": params})
    deadline = time.time() + 55
    while time.time() < deadline:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "method" in msg:
            _queued.append(msg)  # host request arrived mid-sample; handle after
            continue
        if msg.get("id") == rid:
            res = msg.get("result", {})
            content = res.get("content", {})
            return content.get("text") if isinstance(content, dict) else str(content)
        # unrelated response; stash
        _pending[msg.get("id")] = msg
    _log("sampling timed out, using local fallback")
    return None


def as_json(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


# ---------------------------------------------------------------------------
# Local deterministic core (always available)
# ---------------------------------------------------------------------------

SKILLS = [
    "python", "javascript", "typescript", "go", "java", "rust", "c++", "sql",
    "react", "vue", "angular", "next.js", "node.js", "nodejs", "django",
    "flask", "fastapi", "spring", "kubernetes", "k8s", "docker", "aws",
    "gcp", "azure", "terraform", "ci/cd", "graphql", "rest", "grpc",
    "postgres", "mysql", "mongodb", "redis", "kafka", "rabbitmq",
    "machine learning", "deep learning", "llm", "nlp", "pytorch",
    "tensorflow", "data pipeline", "etl", "airflow", "spark", "figma",
    "product management", "agile", "scrum", "a/b testing", "seo",
    "content", "sales", "crm", "customer success", "support",
    "accounting", "excel", "financial modeling", "marketing", "copywriting",
    "design", "illustration", "video editing", "devops", "sre",
    "linux", "bash", "git", "testing", "pytest", "jest", "cypress",
    "microservices", "system design", "distributed systems", "security",
    "auth", "oauth", "payments", "stripe", "mobile", "ios", "android",
    "flutter", "react native", "blockchain", "solidity", "evm",
    "ux", "ui", "wireframing", "prototyping", "user research",
    "usability testing", "user interviews", "design systems", "webflow",
    "html/css", "motion", "after effects", "personas", "journey mapping",
]


def find_skills(text: str) -> list[str]:
    low = text.lower()
    out = set()
    for s in SKILLS:
        if re.search(r"(?<![a-z])" + re.escape(s) + r"(?![a-z])", low):
            out.add(s)
    return sorted(out)


def split_bullets(text: str) -> list[str]:
    lines = [ln.strip(" •-*–\t") for ln in text.splitlines()]
    return [ln for ln in lines if len(ln) > 25][:60]


def guess_years(text: str) -> float:
    years = [int(n) for n in re.findall(r"(\d{1,2})\+?\s*(?:years?|yrs?)", text.lower())]
    ranges = re.findall(r"((?:19|20)\d{2})\s*[—–\-]\s*((?:19|20)\d{2}|present|now)", text.lower())
    span = 0.0
    for a, b in ranges:
        try:
            end = 2026 if b in ("present", "now") else int(b)
            span = max(span, end - int(a))
        except ValueError:
            pass
    if years:
        return float(max(max(years), span))
    return span


def jd_required_years(jd: str) -> float:
    m = re.findall(r"(\d{1,2})\+?\s*(?:years?|yrs?)", jd.lower())
    return float(max([int(x) for x in m])) if m else 0.0


def parse_profile(text: str) -> dict[str, Any]:
    email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
    name = ""
    for ln in text.splitlines()[:6]:
        ln = ln.strip()
        if 2 <= len(ln.split()) <= 4 and "@" not in ln and not re.search(r"\d{4}", ln):
            name = ln
            break
    return {
        "name": name, "email": email.group(0) if email else "",
        "years": guess_years(text),
        "skills": find_skills(text),
        "bullets": split_bullets(text),
        "chars": len(text),
    }


FIT_SCHEMA = {"type": "json_schema",
              "json_schema": {"name": "fit", "strict": False, "schema": {
                  "type": "object",
                  "properties": {
                      "fit_score": {"type": "number"},
                      "verdict": {"type": "string"},
                      "matched": {"type": "array", "items": {"type": "string"}},
                      "missing": {"type": "array", "items": {"type": "string"}},
                      "reasons": {"type": "array", "items": {"type": "string"}},
                  },
                  "required": ["fit_score", "verdict", "matched", "missing", "reasons"],
              }}}


def local_score(profile: dict[str, Any], jd: str) -> dict[str, Any]:
    jd_skills = find_skills(jd)
    mine = set(profile["skills"])
    matched = [s for s in jd_skills if s in mine]
    missing = [s for s in jd_skills if s not in mine]
    cov = (len(matched) / len(jd_skills)) if jd_skills else 0.5
    req = jd_required_years(jd)
    exp_factor = 1.0 if req <= 0 or profile["years"] >= req else max(0.4, profile["years"] / req)
    score = round(100 * (0.7 * cov + 0.3 * exp_factor))
    verdict = ("Strong fit — apply now" if score >= 70 else
               "Decent fit — tailor hard" if score >= 45 else
               "Stretch — apply only if mission-excited")
    reasons = [
        f"{len(matched)}/{len(jd_skills)} JD keywords covered" if jd_skills else "No explicit keywords in JD — scored on experience",
        f"Your ~{profile['years']:g}y vs required ~{req:g}y" if req else f"~{profile['years']:g}y experience parsed",
    ]
    return {"fit_score": score, "verdict": verdict, "matched": matched,
            "missing": missing, "reasons": reasons}


def fetch_url(url: str) -> tuple[str, str]:
    """Returns (text, error). LinkedIn almost always blocks bots — honest fallback."""
    if not url:
        return "", ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode("utf-8", "ignore")
        txt = re.sub(r"<script.*?</script>", " ", raw, flags=re.S | re.I)
        txt = re.sub(r"<style.*?</style>", " ", txt, flags=re.S | re.I)
        txt = re.sub(r"<[^>]+>", "\n", txt)
        txt = re.sub(r"\n+", "\n", txt)
        if len(txt.strip()) < 300:
            return "", "Page returned almost no readable text (likely login wall — LinkedIn blocks bots). Paste the job description text instead."
        return txt.strip()[:8000], ""
    except Exception as e:  # noqa: BLE001
        return "", f"Could not fetch URL ({e}). Paste the job description text instead."


def rank_bullets(bullets: list[str], jd_skills: list[str]) -> list[tuple[int, str]]:
    scored = []
    for b in bullets:
        low = b.lower()
        hits = sum(1 for s in jd_skills if s in low)
        scored.append((hits, b))
    scored.sort(key=lambda x: -x[0])
    return scored


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

SYS = ("You are Job Hunt Copilot. Mirror, never invent: use only claims from "
       "the candidate profile. Gaps get honest bridge lines, never fake experience.")


def do_run(a: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    action = (a.get("action") or "").strip()

    if action == "set_profile":
        text = a.get("profile_text", "")
        if len(text.strip()) < 50:
            return {"success": False,
                    "error": "Paste your CV text (or upload the file) — need at least a few lines."}
        prof = parse_profile(text)
        llm = try_sample(ctx, SYS,
                         f"Extract JSON {{\"name\",\"email\",\"headline\",\"years\",\"skills\":[],\"top_bullets\":[]}} from this CV:\n{text[:4000]}",
                         800, {"type": "json_object"})
        if (j := as_json(llm)) and isinstance(j, dict):
            prof.update({k: j[k] for k in ("name", "email", "headline", "years", "skills", "top_bullets") if k in j})
        display = (f"Profile locked: {prof.get('name') or 'candidate'} · "
                   f"~{prof.get('years', 0):g}y · {len(prof['skills'])} skills "
                   f"({', '.join(prof['skills'][:8])}).\nNow paste a job description to score fit.")
        return {"success": True, "data": {"profile": prof, "display_text": display}}

    if action in ("score", "tailor", "cover", "interview"):
        ptext, jtext = a.get("profile_text", ""), a.get("job_text", "")
        if a.get("job_url") and not jtext:
            jtext, err = fetch_url(a["job_url"])
            if err and len(ptext.strip()) < 50:
                return {"success": False, "error": err}
        if len(ptext.strip()) < 50:
            return {"success": False, "error": "Set your profile first (paste CV)."}
        if len(jtext.strip()) < 50:
            return {"success": False, "error": "Paste the job description (or a fetchable URL)."}
        prof = parse_profile(ptext)
        jd_skills = find_skills(jtext)

        if action == "score":
            llm = try_sample(ctx, SYS,
                             f"Score fit 0-100 as JSON {{\"fit_score\":n,\"verdict\":s,\"matched\":[],\"missing\":[],\"reasons\":[]}}.\nCV:\n{ptext[:3500]}\nJD:\n{jtext[:3500]}",
                             800, FIT_SCHEMA)
            if (j := as_json(llm)) and "fit_score" in j:
                out = j
            else:
                out = local_score(prof, jtext)
            co, ro = a.get("company", ""), a.get("role", "")
            head = f"{ro + ' @ ' + co if (ro or co) else 'Fit'}: {out['fit_score']}/100 — {out['verdict']}"
            body = (f"Matched: {', '.join(out['matched']) or '—'}\n"
                    f"Missing: {', '.join(out['missing']) or '—'}\n" +
                    "\n".join(f"• {r}" for r in out["reasons"]))
            return {"success": True, "data": {**out, "display_text": f"{head}\n{body}"}}

        if action == "tailor":
            ranked = rank_bullets(prof["bullets"], jd_skills)
            top = [b for _, b in ranked[:6]]
            missing = [s for s in jd_skills if s not in set(prof["skills"])][:5]
            llm = try_sample(ctx, SYS,
                             f"Mirror these CV bullets toward the JD keywords ({', '.join(jd_skills[:12])}). "
                             f"Return the 6 best rewritten bullets + up to 4 '[ADD IF TRUE]' bridge lines. "
                             f"JSON {{\"bullets\":[],\"bridges\":[]}}.\nCV:\n{ptext[:3500]}\nJD:\n{jtext[:2500]}",
                             1200, {"type": "json_object"})
            if (j := as_json(llm)) and j.get("bullets"):
                bullets, bridges = j["bullets"][:6], j.get("bridges", [])[:4]
            else:
                bullets, bridges = top, [f"[ADD IF TRUE] Hands-on {s} exposure" for s in missing[:4]]
            display = ("Mirrored bullets (paste-ready):\n" +
                       "\n".join(f"• {b}" for b in bullets) +
                       ("\n\nBridge lines — keep only if true:\n" + "\n".join(f"• {b}" for b in bridges) if bridges else ""))
            return {"success": True, "data": {"bullets": bullets, "bridges": bridges,
                                              "missing": missing, "display_text": display}}

        if action == "cover":
            co, ro = a.get("company") or "the team", a.get("role") or "this role"
            name = prof.get("name") or "Applicant"
            strengths = ", ".join(prof["skills"][:4]) or "relevant experience"
            letter = (f"Dear Hiring Manager at {co},\n\nI'm applying for {ro} with ~{prof.get('years', 0):g} years "
                      f"built around {strengths}. ")
            llm = try_sample(ctx, SYS,
                             f"Write a 150-word cover letter, forward-looking, no invented jobs. "
                             f"Company={co}, Role={ro}.\nCV:\n{ptext[:3500]}\nJD:\n{jtext[:2500]}",
                             600)
            body = llm.strip() if llm and len(llm.strip()) > 80 else (
                letter + (f"What caught me: {', '.join(jd_skills[:3])} — exactly where I've delivered. "
                           if jd_skills else "Your stack matches where I've delivered. ") +
                "I'd welcome a conversation about what I can ship in the first 90 days.\n\n" +
                f"Best regards,\n{name}")
            return {"success": True, "data": {"cover_letter": body, "display_text": body}}

        # interview
        qs: list[str] = []
        for s in jd_skills[:6]:
            qs.append(f"Walk me through your hands-on experience with {s} — what did you ship?")
        qs += ["Why this company and this role — what specifically?", "Tell me about a conflict or missed deadline and what you changed after."]
        llm = try_sample(ctx, SYS,
                         f"Build an interview prep pack as JSON {{\"questions\":[],\"star_prompts\":[],\"company_checks\":[]}} "
                         f"from this JD + CV. Map questions to candidate proof, flag gaps with bridge answers.\nCV:\n{ptext[:3000]}\nJD:\n{jtext[:3000]}",
                         1200, {"type": "json_object"})
        pack: dict[str, Any] = {"questions": qs,
                                "star_prompts": [f"Situation→Task→Action→Result for: {b[:80]}" for _, b in rank_bullets(prof["bullets"], jd_skills)[:3]],
                                "company_checks": ["Latest funding/news + why now", "Interviewer backgrounds", "Team structure + first-90-day expectations"]}
        if (j := as_json(llm)) and j.get("questions"):
            pack = j
        display = ("Interview pack:\n" + "\n".join(f"{i+1}. {q}" for i, q in enumerate(pack["questions"][:10])) +
                   "\n\nSTAR proofs:\n" + "\n".join(f"• {s}" for s in pack.get("star_prompts", [])[:4]))
        return {"success": True, "data": {**pack, "display_text": display}}

    if action == "track":
        try:
            apps = json.loads(a.get("apps_json", "[]"))
        except json.JSONDecodeError:
            return {"success": False, "error": "apps_json is not valid JSON."}
        op = (a.get("op") or "list").strip()
        now = time.time()
        if op == "add":
            try:
                ref = json.loads(a.get("app_ref", "{}"))
            except json.JSONDecodeError:
                return {"success": False, "error": "app_ref is not valid JSON."}
            ref.setdefault("id", uuid.uuid4().hex[:8])
            ref.setdefault("applied_at", int(now))
            ref.setdefault("status", "applied")
            apps.append(ref)
        elif op == "update":
            try:
                ref = json.loads(a.get("app_ref", "{}"))
            except json.JSONDecodeError:
                return {"success": False, "error": "app_ref is not valid JSON."}
            for e in apps:
                if e.get("id") == ref.get("id") or (e.get("company") == ref.get("company") and e.get("role") == ref.get("role")):
                    e.update({k: v for k, v in ref.items() if v != ""})
                    if a.get("note"):
                        e["status"] = a["note"]
                    e["updated_at"] = int(now)
                    break
        quiet = [e for e in apps if now - e.get("applied_at", now) > 10 * 86400
                 and e.get("status") in ("applied", "followup1")]
        if op == "quiet":
            lines = [f"• {e.get('company','?')} — {e.get('role','?')} ({int((now - e.get('applied_at', now))//86400)}d quiet)" for e in quiet]
            return {"success": True, "data": {"quiet": quiet, "display_text":
                    "Needs a nudge (drafts only, never auto-send):\n" + ("\n".join(lines) if lines else "Nothing quiet. Pipeline is fresh.")}}
        lines = [f"• {e.get('company','?')} — {e.get('role','?')} [{e.get('status','applied')}]" for e in apps]
        return {"success": True, "data": {"apps": apps, "quiet_count": len(quiet),
                "display_text": f"{len(apps)} tracked · {len(quiet)} quiet >10d\n" + ("\n".join(lines) if lines else "Empty — add your first application.")}}

    return {"success": False,
            "error": f"Unknown action '{action}'. Use set_profile | score | tailor | cover | interview | track."}


# ---------------------------------------------------------------------------
# Transport loop
# ---------------------------------------------------------------------------

def handle(req: dict[str, Any]) -> dict[str, Any]:
    global _v2_live
    m = req.get("method")
    if m == "initialize":
        _v2_live = True
        return {"protocolVersion": "2.0",
                "server_info": {"name": "job-hunt-copilot", "version": VERSION},
                "capabilities": {"sampling": {}}}
    if m == "describe":
        return MANIFEST
    if m == "health":
        return {"status": "ready"}
    if m == "invoke":
        p = req.get("params", {})
        ctx = p.get("context", {}) or {}
        try:
            return do_run(p.get("arguments", {}) or {}, ctx)
        except Exception as e:  # noqa: BLE001
            _log(f"invoke error: {e}")
            return {"success": False, "error": str(e)}
    raise ValueError(f"unknown rpc: {m}")


def main() -> None:
    # Windows consoles default to cp1252/cp936 — force UTF-8 so non-ASCII
    # payloads (e.g. CJK in parameter descriptions) never break the transport.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    while True:
        if _queued:
            req = _queued.pop(0)
        else:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                continue
        try:
            _send({"jsonrpc": "2.0", "id": req.get("id"), "result": handle(req)})
        except Exception as e:  # noqa: BLE001
            _send({"jsonrpc": "2.0", "id": req.get("id"),
                   "error": {"code": -32601, "message": str(e)}})


if __name__ == "__main__":
    main()
