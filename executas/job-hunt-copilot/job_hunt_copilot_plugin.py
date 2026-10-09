#!/usr/bin/env python3
"""job-hunt-copilot — Executa stdio tool plugin (single-dispatcher).

Actions (method="run", discriminator "action"):
  set_profile | score | tailor | cover | interview | track | extract_text

Protocol: JSON-RPC 2.0 over stdio. v2 handshake (initialize) advertises
sampling; every generating action first tries host sampling (llm.sample) for
quality, then falls back to deterministic local logic so the app works
everywhere (local harness, grant off, offline review).

Grounding contract (enforced in code, not just in the prompt): generated
résumé text may only re-use facts stated in the CV. Every generated line is
verified token-by-token against the CV; anything the CV does not state is
either dropped (bullet falls back to the CV original) or surfaced to the user
as an explicit "[ADD IF TRUE]" suggestion. See `grounding_report`.

Envelope contract: invoke always returns {"success": bool, "data"/"error"}.
Logs go to stderr only — stdout is strict JSON-RPC transport.
"""

from __future__ import annotations

import base64
import io
import json
import re
import sys
import time
import urllib.request
import uuid
from typing import Any

VERSION = "0.1.5"

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
                "cover | interview | track | extract_text. set_profile parses a "
                "CV once (paste text or a profile URL); score/tailor/cover/"
                "interview need profile_text + job_text; track manages the "
                "application list; extract_text pulls plain text out of an "
                "uploaded .pdf (base64 in file_b64)."
            ),
            "parameters": [
                {"name": "action", "type": "string",
                 "description": "One of: set_profile, score, tailor, cover, interview, track, extract_text.",
                 "required": True},
                {"name": "profile_text", "type": "string",
                 "description": "CV as plain text (or parsed profile summary). Required for score/tailor/cover/interview.",
                 "required": False, "default": ""},
                {"name": "profile_url", "type": "string",
                 "description": "Profile URL (portfolio / personal site / LinkedIn). Fetched when profile_text is empty; LinkedIn blocks bots and returns guidance instead.",
                 "required": False, "default": ""},
                {"name": "job_text", "type": "string",
                 "description": "Job description text. Alternatively pass job_url.",
                 "required": False, "default": ""},
                {"name": "job_url", "type": "string",
                 "description": "Job posting URL. Server-side fetch attempted; LinkedIn often blocks bots — paste text as fallback.",
                 "required": False, "default": ""},
                {"name": "company", "type": "string",
                 "description": "Company name (cover letter + tracker).", "required": False, "default": ""},
                {"name": "role", "type": "string",
                 "description": "Role title (cover letter + tracker).", "required": False, "default": ""},
                {"name": "apps_json", "type": "string",
                 "description": "JSON array of tracked applications (for track action).",
                 "required": False, "default": "[]"},
                {"name": "op", "type": "string",
                 "description": "track sub-op: add | update | remove | list | quiet. Default list.",
                 "required": False, "default": "list"},
                {"name": "app_ref", "type": "string",
                 "description": "JSON object of one application (for track add/update/remove).",
                 "required": False, "default": "{}"},
                {"name": "note", "type": "string",
                 "description": "Status note / outcome (for track update).", "required": False, "default": ""},
                {"name": "file_b64", "type": "string",
                 "description": "Base64 file bytes (extract_text action, .pdf).",
                 "required": False, "default": ""},
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
            if msg.get("error"):
                _log(f"sampling error: {json.dumps(msg['error'])[:300]}")
                return None
            res = msg.get("result", {}) or {}
            content = res.get("content", {})
            if isinstance(content, dict):
                text = content.get("text")
            elif isinstance(content, list):
                text = "".join(p.get("text", "") for p in content if isinstance(p, dict))
            else:
                text = str(content) if content else res.get("text")
            if not text:
                _log(f"sampling returned no text: {json.dumps(res)[:200]}")
            return text or None
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
# Grounding guard — the CV is the only source of truth for generated claims
# ---------------------------------------------------------------------------

WORD_RE = re.compile(r"[a-z0-9][a-z0-9+#./'_-]*")

STOP = {
    "the", "a", "an", "and", "or", "but", "if", "then", "than", "that", "this",
    "these", "those", "of", "in", "on", "at", "to", "for", "with", "by", "from",
    "into", "over", "under", "between", "as", "is", "are", "was", "were", "be",
    "been", "being", "it", "its", "we", "you", "your", "my", "our", "their",
    "they", "he", "she", "his", "her", "them", "us", "me", "so", "such", "not",
    "no", "do", "does", "did", "done", "have", "has", "had", "will", "would",
    "can", "could", "should", "may", "might", "must", "also", "about", "while",
    "when", "where", "which", "who", "whom", "whose", "what", "why", "how",
    "all", "any", "both", "each", "few", "more", "most", "other", "some",
    "only", "own", "same", "too", "very", "just", "up", "down", "out", "off",
    "again", "further", "once", "here", "there", "during", "before", "after",
    "above", "below", "per", "via", "etc", "ie", "eg", "am", "an", "im", "ive",
}

# Words that carry no claim about the candidate: safe to appear in a rewrite
# even when the CV does not literally contain them.
GENERIC_OK = {
    "experience", "year", "years", "team", "teams", "work", "working", "worked",
    "project", "projects", "role", "roles", "job", "company", "companies",
    "product", "products", "using", "use", "used", "built", "build", "building",
    "led", "lead", "leading", "manage", "managed", "managing", "management",
    "create", "created", "creating", "improve", "improved", "improving",
    "deliver", "delivered", "delivering", "delivery", "support", "supported",
    "help", "helped", "helping", "drive", "driven", "driving", "own", "owned",
    "owning", "process", "processes", "result", "results", "impact", "impacts",
    "grow", "grew", "growth", "success", "successful", "key", "core", "main",
    "new", "existing", "current", "previous", "relevant", "related", "strong",
    "solid", "great", "good", "best", "better", "across", "within", "including",
    "include", "included", "candidate", "applicant", "hiring", "responsibility",
    "responsibilities", "skill", "skills", "tool", "tools", "bigger", "small",
    "larger", "faster", "quickly", "directly", "owning", "end", "start",
    "beginning", "baseline", "hand", "hands", "handson", "first", "second",
    "third", "day", "days", "week", "weeks", "month", "months", "quarter",
    "yearlong", "timeline", "deadline", "deadlines", "feature", "features",
    "release", "releases", "shipping", "ship", "shipped", "launch", "launched",
    "launching", "version", "versions", "customer", "customers", "user",
    "users", "stakeholder", "stakeholders", "partner", "partners", "council",
}

# Vocabulary that asserts scope, technology, domain or seniority: if it is not
# in the CV, the line is an invented claim, not a rewrite.
RISK_WORDS = {
    "gtm", "infrastructure", "architecture", "churn", "retention", "enterprise",
    "complex", "scalable", "scalability", "optimization", "optimisation",
    "monetization", "compliance", "security", "migration", "kubernetes", "k8s",
    "docker", "terraform", "graphql", "grpc", "rest", "rag", "vector", "llm",
    "api", "sdk", "platform", "ecosystem", "revenue", "arr", "mrr", "b2b",
    "b2c", "saas", "vertical", "persona", "personas", "funnel", "roadmap",
    "okr", "okrs", "kpi", "kpis", "sla", "global", "international", "regional",
    "multiregion", "microservices", "distributed", "backend", "frontend",
    "fullstack", "devops", "analytics", "instrumentation", "mentorship",
    "coaching", "budget", "headcount", "procurement", "partnerships",
    "integration", "integrations", "gdpr", "soc", "hipaa", "pci", "postgres",
    "mysql", "mongodb", "redis", "kafka", "airflow", "spark", "snowflake",
    "dbt", "looker", "tableau", "amplitude", "mixpanel", "segment", "billing",
    "payments", "subscription", "marketplace", "onboarding", "activation",
    "referral", "expansion", "upsell", "crosssell", "csm", "outbound",
    "inbound", "enterprisegrade", "missioncritical", "highvolume",
    "hightraffic", "greenfield", "zerotoone", "data", "datascience",
    "experimentation", "a11y", "accessibility", "localization", "i18n",
}

# Multi-word phrases that assert scope even though every word is generic.
RISK_PHRASES = (
    "end-to-end", "end to end", "go-to-market", "go to market", "post-launch",
    "post launch", "pre-launch", "pre launch", "zero to one", "0 to 1",
    "cross-functional", "cross functional", "company-wide", "company wide",
    "org-wide", "at scale", "full-cycle", "full cycle", "first 90 days",
    "product led growth", "product-led growth", "user research", "user interview",
    "usability test", "design system", "a/b test", "ab test", "a b test",
    "tl;dr", "sla", "okrs",
)

IRREG = {
    "led": "lead", "leading": "lead", "leads": "lead", "grew": "grow",
    "grown": "grow", "growing": "grow", "growth": "grow", "drove": "drive",
    "driven": "drive", "driving": "drive", "built": "build", "building": "build",
    "made": "make", "making": "make", "ran": "run", "running": "run",
    "shipped": "ship", "shipping": "ship", "won": "win", "winning": "win",
    "cut": "cut", "cutting": "cut", "hit": "hit", "sped": "speed",
    "gained": "gain", "rose": "rise", "fell": "fall", "kept": "keep",
    "undertook": "undertake", "oversaw": "oversee", "rewrote": "rewrite",
    "taught": "teach", "thought": "think", "brought": "bring",
}

_SUFFIXES = ("ations", "ation", "izing", "izes", "ized", "ize", "ising",
             "ises", "ised", "ise", "ements", "ement", "ments", "ment",
             "ingly", "ings", "ing", "ives", "ive", "ities", "ity", "ies",
             "ied", "ers", "er", "ed", "es", "s", "ly")


def _stem(word: str) -> str:
    w = word.lower()
    if w in IRREG:
        w = IRREG[w]
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            w = w[: -len(suf)]
            break
    return w


def _key(word: str) -> str:
    """Comparison key: light stem, truncated to 5 chars for long words so
    inflectional variants (reduction/reduce, optimization/optimize) match."""
    s = _stem(word)
    return s[:5] if len(s) >= 6 else s


def tokens(text: str) -> list[str]:
    return WORD_RE.findall((text or "").lower())


def keys(text: str) -> set[str]:
    return {_key(t) for t in tokens(text) if t not in STOP and len(t) > 1}


def numbers_in(text: str) -> set[str]:
    """Digit runs, comma/decimal-normalised: 18,500 and 18.500 both → 18500."""
    out = set()
    for m in re.findall(r"\d[\d,.\s]*\d|\d", text or ""):
        digits = re.sub(r"[^\d]", "", m)
        if digits:
            out.add(str(int(digits)) if len(digits) < 12 else digits)
    return out


def risk_hits(text: str) -> list[tuple[str, list[str]]]:
    """Risk terms present in `text` as (label, tokens) pairs. Phrases are
    tokenised so a CV that already says 'A/B tests' does not count as novel."""
    low = (text or "").lower()
    hits: list[tuple[str, list[str]]] = []
    for t in tokens(low):
        if t in RISK_WORDS:
            hits.append((t, [t]))
    for p in RISK_PHRASES:
        if p in low:
            hits.append((p, [x for x in tokens(p) if x not in STOP]))
    seen, out = set(), []
    for label, toks in hits:
        if label not in seen:
            seen.add(label)
            out.append((label, toks))
    return out


def novel_risk(text: str, cv_text: str) -> list[str]:
    """Risk terms in `text` that the CV does not itself use."""
    cv_keys = keys(cv_text)
    out = []
    for label, toks in risk_hits(text):
        if not toks or not all(_key(t) in cv_keys for t in toks):
            out.append(label)
    return sorted(set(out))


def grounding_report(text: str, cv_text: str) -> dict[str, Any]:
    """Compare one generated line against the CV.

    hard  → the line states something the CV does not: must not be shown as a
            claim (caller replaces it with the CV original).
    soft  → one novel non-risk word: paraphrase, shown with a verify note.
    """
    cv_keys = keys(cv_text)
    cv_nums = numbers_in(cv_text)
    novel = sorted({t.strip(".,;:'\"-_/") for t in tokens(text)
                    if t not in STOP and len(t) > 1 and _key(t) not in cv_keys} - {""})
    risky = novel_risk(text, cv_text)
    new_nums = sorted(numbers_in(text) - cv_nums)
    hard = bool(risky) or bool(new_nums) or len(novel) >= 2
    return {
        "text": text,
        "novel": novel,
        "risk": risky,
        "new_numbers": new_nums,
        "hard": hard,
        "soft": (not hard) and bool(novel),
    }


def grounded_or_fallback(text: str, fallback: str, cv_text: str) -> tuple[str, dict[str, Any]]:
    rep = grounding_report(text, cv_text)
    if rep["hard"]:
        rep["replaced_with"] = fallback
        return (fallback if fallback else text), rep
    return text, rep


def jaccard(a: str, b: str) -> float:
    ka, kb = keys(a), keys(b)
    if not ka or not kb:
        return 0.0
    return len(ka & kb) / len(ka | kb)


def dedupe_lines(lines: list[str], threshold: float = 0.7) -> tuple[list[str], list[str]]:
    """Drop near-duplicate lines (the reviewer saw two near-identical bullets)."""
    kept: list[str] = []
    dropped: list[str] = []
    for ln in lines:
        if any(jaccard(ln, k) >= threshold for k in kept):
            dropped.append(ln)
        else:
            kept.append(ln)
    return kept, dropped


def strip_add_if_true(text: str) -> tuple[str, bool]:
    flag = bool(re.search(r"\[ADD IF TRUE\]", text or "", re.I))
    return re.sub(r"\[ADD IF TRUE\]\s*", "", text or "", flags=re.I).strip(), flag


def mark_add_if_true(text: str, added: list[str] | None = None) -> str:
    body, already = strip_add_if_true(text)
    if not body:
        return ""
    tag = "[ADD IF TRUE]"
    if not already:
        body = f"{tag} {body}"
    if added:
        body += f"  (not stated in your CV: {', '.join(added[:6])})"
    return body


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
    "rag", "vector db", "vector database", "prompt engineering", "gtm",
    "go-to-market", "developer platform", "api product", "roadmap",
    "analytics", "mixpanel", "ab testing",
]


def find_skills(text: str) -> list[str]:
    low = text.lower()
    out = set()
    for s in SKILLS:
        if re.search(r"(?<![a-z])" + re.escape(s) + r"(?![a-z])", low):
            out.add(s)
    return sorted(out)


PUA_RE = re.compile(r"[\ue000-\uf8ff]+")  # icon-font glyphs (LinkedIn PDF exports)


def clean_text(text: str) -> str:
    """Normalise extracted text: drop icon-font glyphs, collapse the whitespace
    mess PDF extraction leaves behind."""
    t = PUA_RE.sub(" ", text or "")
    t = t.replace("\u00a0", " ").replace("\u200b", "")
    t = "\n".join(re.sub(r"[ \t]{2,}", " ", ln).strip() for ln in t.splitlines())
    return re.sub(r"\n{3,}", "\n\n", t).strip()


SECTION_RE = re.compile(
    r"(?i)^\s*(summary|profile|objective|skills|experience|work experience|education|"
    r"projects?|certifications?|awards?|languages?|interests?|contact|about)\b\s*[:\-–—]")


def split_bullets(text: str) -> list[str]:
    """Résumé bullets only — section headers, the name line and job-title lines are
    skipped, and PDF line-wraps are re-joined so a 'mirrored bullet' is never a
    half sentence."""
    text = clean_text(text)
    lines = [(raw.strip(" \t"), raw.strip(" •-*–—\t")) for raw in text.splitlines()[1:]]
    markers = sum(1 for raw, _ in lines if raw[:1] in ("•", "-", "*", "–", "—"))
    use_markers = markers >= 2
    out: list[str] = []
    for raw, ln in lines:
        if not ln or SECTION_RE.match(ln):
            continue
        is_marker = raw[:1] in ("•", "-", "*", "–", "—")
        wraps = bool(out) and not re.search(r"[.:;!?]$", out[-1]) and ln[:1].islower()
        if wraps:  # continuation of the previous bullet
            out[-1] = f"{out[-1]} {ln}".strip()
            continue
        if len(ln) <= 25 or (use_markers and not is_marker):
            continue
        if any(ln.lower().startswith(w) for w in JUNK_PREFIX):
            continue
        out.append(ln)
    return out[:60]


JUNK_PREFIX = ("potential candidate", "follow", "skills", "languages &", "operating systems",
               "databases", "servers", "framework", "cloud platforms", "interests",
               "introduction", "email:", "phone:", "gender:", "date of birth", "address:",
               "objective", "job title", "main responsibilities", "accomplishments:",
               "technologies:", "project description", "project:")


EDU_HINT = re.compile(r"(?i)\b(education|university|college|bachelor|master|bsc|msc|phd|"
                      r"diploma|gpa|high school|certification)\b")


def guess_years(text: str) -> float:
    """Years of experience: explicit 'N years' mention, else the union span of
    dated *work* roles (earliest start → latest end — education ranges don't
    count, which is what recruiters mean by experience)."""
    clean = clean_text(text)
    low = clean.lower()
    years = [int(n) for n in re.findall(r"(\d{1,2})\+?\s*(?:years?|yrs?)", low)]
    lines = clean.splitlines()
    work_lines = []
    for i, ln in enumerate(lines):
        window = " ".join(lines[max(0, i - 4):i + 5])  # education blocks are labelled nearby
        if not EDU_HINT.search(window):
            work_lines.append(ln)
    work = "\n".join(work_lines).lower()
    ranges = re.findall(
        r"((?:19|20)\d{2})\s*[—–\-]\s*(?:[a-z]{3,9}\.?\s+)?((?:19|20)\d{2}|present|now)", work)
    starts, ends = [], []
    for a, b in ranges:
        try:
            starts.append(int(a))
            ends.append(2026 if b in ("present", "now") else int(b))
        except ValueError:
            pass
    span = float(max(ends) - min(starts)) if starts and ends else 0.0
    return float(max([*years, span])) if years else span


def jd_required_years(jd: str) -> float:
    m = re.findall(r"(\d{1,2})\+?\s*(?:years?|yrs?)", jd.lower())
    return float(max([int(x) for x in m])) if m else 0.0


def parse_profile(text: str) -> dict[str, Any]:
    text = clean_text(text)
    email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
    name = ""
    for raw in text.splitlines()[:8]:
        for seg in re.split(r"[|\u00b7\u2013\u2014\t]| - ", raw):
            ln = seg.strip(" ,;:")
            words = ln.split()
            if not (2 <= len(words) <= 4) or "@" in ln or re.search(r"\d", ln):
                continue
            if not ln[:1].isupper() or any(c.islower() for c in []):
                continue
            low = ln.lower()
            if low.startswith(JUNK_PREFIX) or any(w in low for w in
                                                   ("candidate", "follow", "developer", "engineer",
                                                    "manager", "resume", "curriculum", "profile")):
                continue
            name = ln
            break
        if name:
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


LINKEDIN_HELP = (
    "LinkedIn blocks automated fetching (it answers bots with a login wall), so a "
    "profile URL alone can't be read. Two things that work:\n"
    "• Desktop: open your profile → More → Save to PDF → upload that PDF here.\n"
    "• Mobile: Profile → Share → Save to PDF, then upload the file.\n"
    "Or simply open your profile, select the text of your CV and paste it into the box."
)


def is_linkedin(url: str) -> bool:
    return "linkedin." in (url or "").lower()


def fetch_url(url: str) -> tuple[str, str]:
    """Returns (text, error). LinkedIn almost always blocks bots — honest fallback."""
    if not url:
        return "", ""
    if is_linkedin(url):
        return "", LINKEDIN_HELP
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode("utf-8", "ignore")
        txt = re.sub(r"<script.*?</script>", " ", raw, flags=re.S | re.I)
        txt = re.sub(r"<style.*?</style>", " ", txt, flags=re.S | re.I)
        txt = re.sub(r"<[^>]+>", "\n", txt)
        txt = re.sub(r"\n+", "\n", txt)
        if len(txt.strip()) < 300:
            return "", ("That page returned almost no readable text (login wall or "
                        "JavaScript-only page). Open it, copy the text, and paste it here.")
        return txt.strip()[:12000], ""
    except Exception as e:  # noqa: BLE001
        return "", (f"Could not fetch that URL ({e}). Open it in a browser, copy the text, "
                    "and paste it into the box.")


def rank_bullets(bullets: list[str], jd_skills: list[str]) -> list[tuple[int, str]]:
    scored = []
    for b in bullets:
        low = b.lower()
        hits = sum(1 for s in jd_skills if s in low)
        scored.append((hits, b))
    scored.sort(key=lambda x: -x[0])
    return scored


# ---------------------------------------------------------------------------
# Normalisers — LLM output never reaches the user as a raw object
# ---------------------------------------------------------------------------

def _pick(d: dict[str, Any], *names: str) -> str:
    for n in names:
        v = d.get(n)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return str(v)
    return ""


def _as_text(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, (int, float)):
        return str(item)
    if isinstance(item, dict):
        parts = [f"{k}: {v}" for k, v in item.items() if isinstance(v, (str, int, float)) and str(v).strip()]
        return " · ".join(parts)
    if isinstance(item, list):
        return "; ".join(_as_text(i) for i in item)
    return ""


def normalize_questions(raw: Any) -> list[dict[str, Any]]:
    """LLM may return strings or dicts with assorted key names — flatten to
    {question, proof, answer, gap} with plain strings."""
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, str):
            q = item.strip()
            if q:
                out.append({"question": q, "proof": "", "answer": "", "gap": False})
            continue
        if not isinstance(item, dict):
            txt = _as_text(item)
            if txt:
                out.append({"question": txt, "proof": "", "answer": "", "gap": False})
            continue
        q = _pick(item, "question", "q", "question_text", "text", "prompt", "title", "ask")
        proof = _pick(item, "candidate_proof", "proof", "your_proof", "evidence",
                      "candidate_evidence", "cv_evidence", "source")
        ans = _pick(item, "suggested_answer", "answer", "bridge_answer", "bridge",
                    "sample_answer", "response", "how_to_answer", "guidance")
        gap = item.get("gap_flag", item.get("gap", item.get("is_gap", False)))
        if q or proof or ans:
            out.append({"question": q or "(question missing)", "proof": proof,
                        "answer": ans, "gap": bool(gap)})
    return out


STAR_KEYS = {
    "situation": ("situation", "s", "context", "background", "setting"),
    "task": ("task", "t", "challenge", "objective", "goal"),
    "action": ("action", "a", "actions", "what_i_did", "did"),
    "result": ("result", "r", "outcome", "results", "impact", "metrics"),
}


def normalize_star(raw: Any) -> list[dict[str, Any]]:
    """STAR proofs: accept {situation,task,action,result} dicts, nested
    {'star': {...}} or plain strings; always emit the four named fields."""
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        if isinstance(raw, dict):
            raw = [raw]
        else:
            return out
    for item in raw:
        if isinstance(item, str):
            out.append({"headline": item.strip(), "situation": "", "task": "",
                        "action": "", "result": ""})
            continue
        if not isinstance(item, dict):
            continue
        src = item
        for nest in ("star", "star_proof", "proof", "example"):
            inner = item.get(nest)
            if isinstance(inner, dict):
                src = {**item, **inner}
                break
        headline = _pick(src, "title", "headline", "bullet", "source_bullet",
                         "question", "prompt", "label", "topic", "name")
        headline = re.sub(r"^Situation\s*→?\s*Task\s*→?\s*Action\s*→?\s*Result\s*(for:)?\s*",
                          "", headline, flags=re.I).strip()
        if headline.startswith(":"):
            headline = headline[1:].strip()
        row: dict[str, Any] = {"headline": headline}
        for field, names in STAR_KEYS.items():
            row[field] = _pick(src, *names)
        if not any(row[f] for f in STAR_KEYS):
            flat = _as_text(item)
            row["situation"] = flat
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

SYS = ("You are Job Hunt Copilot, a résumé-tailoring assistant. The candidate's CV is the "
       "ONLY source of truth. Never add a fact, tool, technology, metric, number or scope "
       "word that is not literally in the CV — not even a plausible one (no 'end-to-end', no "
       "'GTM', no 'complex infrastructure', no invented churn or user numbers). Prefer the "
       "CV's own vocabulary; reorder and rephrase only. Anything the CV lacks goes in an "
       "[ADD IF TRUE] list, addressed to the candidate, never into a claim about them.")


def _bridges_from_missing(missing: list[str], jd: str, limit: int = 4) -> list[str]:
    out = []
    for s in missing[:limit]:
        out.append(f"[ADD IF TRUE] {s} — the JD asks for this and your CV doesn't mention it. "
                   f"Add a line only if you've really used it (name the project and the number).")
    return out


def _fact_footer(bullets_grounded: int, bullets_total: int, dups: int, bridges: int,
                 replaced: int, verify: list[str]) -> str:
    bits = [f"{bullets_grounded}/{bullets_total} bullets trace to your CV",
            f"{bridges} addition(s) listed as [ADD IF TRUE]",
            "0 invented claims"]
    if dups:
        bits.append(f"{dups} near-duplicate(s) dropped")
    if replaced:
        bits.append(f"{replaced} rewrite(s) reverted to your CV wording (facts not in CV)")
    line = "Fact check: " + " · ".join(bits)
    if verify:
        line += "\nVerify wording (paraphrase, not in your CV verbatim): " + ", ".join(verify[:6])
    return line


def do_tailor(a: dict[str, Any], ctx: dict[str, Any], ptext: str, jtext: str,
              prof: dict[str, Any], jd_skills: list[str]) -> dict[str, Any]:
    cv_bullets = prof["bullets"] or split_bullets(ptext)
    ranked = rank_bullets(cv_bullets, jd_skills)
    missing = [s for s in jd_skills if s not in set(prof["skills"])][:5]

    llm = try_sample(ctx, SYS,
                     "Rewrite the CV bullets so the job's own keywords surface, using ONLY words "
                     "already present in the CV. Rules: keep every number exactly as written; "
                     "never introduce a technology, metric, scope word or domain the CV lacks "
                     "(no 'end-to-end', no 'GTM', no 'complex infrastructure'); if a rewrite would "
                     "need new words, keep the CV wording. For each bullet include "
                     "source_span = the exact substring of the CV it comes from.\n"
                     'Return JSON {"bullets":[{"text":"..","source_span":".."}],'
                     '"bridges":["[ADD IF TRUE] .."]}\n'
                     f"JD keywords: {', '.join(jd_skills[:12])}\n"
                     f"CV:\n{ptext[:3500]}\nJD:\n{jtext[:2000]}",
                     1200, {"type": "json_object"})

    candidates: list[tuple[str, str]] = []  # (text, source fallback)
    bridges: list[str] = []
    if (j := as_json(llm)) and isinstance(j.get("bullets"), list):
        for item in j["bullets"][:8]:
            if isinstance(item, dict):
                text = _pick(item, "text", "bullet", "rewritten", "line")
                span = _pick(item, "source_span", "source", "original", "cv_quote", "quote")
            else:
                text, span = _as_text(item), ""
            if not text:
                continue
            src = span if span and len(span) > 15 else ""
            if src:
                norm_src = re.sub(r"\s+", " ", src.lower())
                if norm_src not in re.sub(r"\s+", " ", ptext.lower()):
                    src = ""
            if not src:  # best-matching CV bullet as the grounding fallback
                src = max(cv_bullets, key=lambda b: jaccard(text, b)) if cv_bullets else text
            candidates.append((text, src))
        raw_bridges = j.get("bridges") or []
        if isinstance(raw_bridges, list):
            for b in raw_bridges[:4]:
                txt = _as_text(b).strip()
                if txt:
                    bridges.append(mark_add_if_true(txt))

    replaced, verify_terms = 0, []
    bullets: list[tuple[str, str]] = []
    if candidates:
        for text, src in candidates:
            final, rep = grounded_or_fallback(text, src, ptext)
            if rep["hard"]:
                replaced += 1
            elif rep["soft"]:
                verify_terms.extend(rep["novel"][:2])
            bullets.append((final, src))
    else:
        bullets = [(b, b) for _, b in ranked[:6]]

    # keep at most 6, no near-duplicates
    texts = [b for b, _ in bullets]
    kept, dups = dedupe_lines(texts, 0.7)
    src_of = {b: s for b, s in bullets}
    kept = kept[:6]

    if not bridges:
        bridges = _bridges_from_missing(missing, jtext)
    else:
        have = " ".join(bridges).lower()
        for extra in _bridges_from_missing(missing, jtext):
            skill = extra.split("—")[0].replace("[ADD IF TRUE]", "").strip().lower()
            if skill and skill not in have:
                bridges.append(extra)
    bridges, _ = dedupe_lines(bridges, 0.8)

    lines = ["Mirrored bullets — every line traces back to a line in your CV:"]
    for i, b in enumerate(kept, 1):
        lines.append(f"{i}. {b}")
        src = src_of.get(b, "")
        if src and src.strip() and jaccard(b, src) < 0.999:
            lines.append(f"   ↳ from your CV: “{src.strip()}”")
    if bridges:
        lines.append("")
        lines.append("[ADD IF TRUE] — not in your CV, add only if you actually did it:")
        lines += [f"• {b}" for b in bridges]
    lines.append("")
    lines.append(_fact_footer(len(kept), len(kept), len(dups), len(bridges), replaced, verify_terms))
    display = "\n".join(lines)

    return {"success": True, "data": {
        "bullets": kept, "sources": [src_of.get(b, "") for b in kept],
        "bridges": bridges, "missing": missing, "dropped_duplicates": dups,
        "replaced_ungrounded": replaced, "verify": sorted(set(verify_terms)),
        "display_text": display}}


def do_interview(a: dict[str, Any], ctx: dict[str, Any], ptext: str, jtext: str,
                 prof: dict[str, Any], jd_skills: list[str]) -> dict[str, Any]:
    cv_bullets = prof["bullets"] or split_bullets(ptext)
    company, role = a.get("company", ""), a.get("role", "")
    header = " · ".join(x for x in (company, role) if x) or "Interview pack"

    llm = try_sample(
        ctx, SYS,
        "Build an interview prep pack from this JD + CV as JSON:\n"
        '{"questions":[{"question":"..","candidate_proof":"..","suggested_answer":"..","gap_flag":false}],'
        '"star":[{"title":"..","situation":"..","task":"..","action":"..","result":".."}],'
        '"company_checks":[".."]}\n'
        "Rules: candidate_proof and every STAR field must reuse the CV's own words; if the CV "
        "has nothing for a field, write it as a short bracketed instruction starting with "
        "[ADD IF TRUE] instead of a claim. Questions may mention JD requirements the CV lacks "
        "(those are gap questions): set gap_flag true and give a suggested_answer that is an "
        "honest bridge, prefixed [ADD IF TRUE] where it states anything the CV does not.\n"
        f"CV:\n{ptext[:3000]}\nJD:\n{jtext[:3000]}",
        1200, {"type": "json_object"})

    raw = as_json(llm) or {}
    questions = normalize_questions(raw.get("questions"))
    star = normalize_star(raw.get("star") or raw.get("star_prompts") or raw.get("star_proofs"))
    checks = raw.get("company_checks")
    checks = [c.strip() for c in checks if isinstance(c, str) and c.strip()] if isinstance(checks, list) else []

    # --- local fallbacks so the pack is never empty and never raw ---
    if not questions:
        questions = []
        for s in jd_skills[:4]:
            covered = s in prof["skills"]
            questions.append({
                "question": f"Walk me through your hands-on experience with {s} — what did you ship?",
                "proof": "" if not covered else f"Your CV: {', '.join(b for b in cv_bullets if s in b.lower())[:200]}",
                "answer": "" if covered else f"Bridge honestly: name your closest real experience "
                                              f"with {s}, or say you haven't shipped it yet and how you'd ramp.",
                "gap": not covered, "local": True})
        questions.append({"question": "Why this company and this role — what specifically?",
                          "proof": "", "answer": "One sentence on their product + one on your own project.",
                          "gap": False, "local": True})
        questions.append({"question": "Tell me about a conflict or missed deadline and what you changed after.",
                          "proof": "", "answer": "Pick a real incident; end on the process change.",
                          "gap": False, "local": True})
    if not star:
        star = [{"headline": b, "situation": "", "task": "", "action": b, "result": ""}
                for _, b in rank_bullets(cv_bullets, jd_skills)[:3]]
    if not checks:
        checks = ["Latest funding/news + why now", "Interviewer backgrounds",
                  "Team structure + first-90-day expectations"]

    # --- grounding pass: proofs, answers and STAR fields ---
    flagged: list[str] = []
    for q in questions:
        local_q = bool(q.get("local"))
        proof_txt, proof_flag = strip_add_if_true(q["proof"])
        if proof_txt and not local_q:
            final, rep = grounded_or_fallback(proof_txt, "", ptext)
            if rep["hard"] or proof_flag:
                q["proof"] = mark_add_if_true(proof_txt, rep.get("risk") or rep.get("novel"))
                flagged.append(proof_txt)
            else:
                q["proof"] = proof_txt
        ans_txt, ans_flag = strip_add_if_true(q["answer"])
        if ans_txt and local_q:
            q["answer"] = f"[ADD IF TRUE] {ans_txt}" if ans_flag is False else q["answer"]
        elif ans_txt:
            final, rep = grounded_or_fallback(ans_txt, "", ptext)
            if rep["hard"] or ans_flag:
                q["answer"] = mark_add_if_true(ans_txt, rep.get("risk") or rep.get("novel"))
            else:
                q["answer"] = ans_txt
        if not q["proof"]:
            q["proof"] = "Not in your CV — this is the gap the interviewer will probe."
            q["gap"] = True
    gap_count = sum(1 for q in questions if q["gap"])

    for s in star:
        for field in ("situation", "task", "action", "result"):
            txt, flag = strip_add_if_true(s.get(field, ""))
            if not txt:
                s[field] = "[ADD IF TRUE] " + {
                    "situation": "one line on the state of things before you started",
                    "task": "what you were accountable for",
                    "action": "what you personally did (your CV bullet is the seed)",
                    "result": "the measurable outcome"}[field]
                if field == "result" and s.get("headline"):
                    s[field] = f"[ADD IF TRUE] the number behind “{s['headline'][:60]}”"
                continue
            _, rep = grounded_or_fallback(txt, "", ptext)
            if rep["hard"] or flag:
                s[field] = mark_add_if_true(txt, rep.get("risk") or rep.get("novel"))
            else:
                s[field] = txt

    lines = [f"Interview pack — {header}",
             f"({gap_count} gap question(s) marked; [ADD IF TRUE] = fill in with your own true detail)"]
    for i, q in enumerate(questions[:10], 1):
        lines.append("")
        lines.append(f"Q{i}. {q['question']}")
        lines.append(f"    Your proof: {q['proof'] or '— none in your CV'}")
        lines.append(f"    Suggested answer: {q['answer'] or '— answer from your CV bullet above.'}")
    if star:
        lines.append("")
        lines.append("STAR proofs (Situation / Task / Action / Result)")
        for i, s in enumerate(star[:4], 1):
            lines.append("")
            lines.append(f"STAR {i} — {s.get('headline') or 'from your CV'}")
            for field, label in (("situation", "Situation"), ("task", "Task"),
                                 ("action", "Action"), ("result", "Result")):
                lines.append(f"    {label}: {s[field]}")
    if checks:
        lines.append("")
        lines.append("Before the call, check:")
        lines += [f"• {c}" for c in checks[:5]]

    return {"success": True, "data": {
        "questions": questions, "star": star, "company_checks": checks,
        "gap_count": gap_count, "flagged": sorted(set(flagged)),
        "display_text": "\n".join(lines)}}


def extract_pdf_text(b64: str) -> dict[str, Any]:
    """Pull text out of a PDF (base64). pypdf is bundled in the binary."""
    raw = b64.split(",")[-1] if b64.startswith("data:") else b64
    try:
        data = base64.b64decode(raw, validate=False)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"Uploaded file could not be decoded ({e})."}
    if len(data) < 200:
        return {"success": False, "error": "That file looks empty. Pick the right file?"}
    if not data[:5].startswith(b"%PDF") and b"%PDF" not in data[:1024]:
        return {"success": False,
                "error": "That doesn't look like a PDF. Upload a .pdf, .txt or .md file, or paste the CV text."}
    try:
        import pypdf  # noqa: PLC0415  (bundled with the binary distribution)
    except Exception as e:  # noqa: BLE001
        return {"success": False,
                "error": f"PDF support is unavailable in this tool build ({e}). Paste the CV text instead."}
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:  # noqa: BLE001
                return {"success": False,
                        "error": "This PDF is password-protected and can't be read. Remove the "
                                 "password (or paste the CV text)."}
        pages = min(len(reader.pages), 20)
        chunks = []
        for i in range(pages):
            try:
                chunks.append(reader.pages[i].extract_text() or "")
            except Exception:  # noqa: BLE001
                chunks.append("")
        text = "\n".join(chunks)
    except Exception as e:  # noqa: BLE001
        return {"success": False,
                "error": f"Couldn't read that PDF ({e}). Paste the CV text instead."}
    text = clean_text(text)
    letters = len(re.findall(r"[A-Za-z]", text))
    if len(text) < 120 or letters < 80:
        return {"success": False,
                "error": ("That PDF has no selectable text — it's a scan/image. Open it, copy the "
                          "text, or type it into the box (there's no OCR).")}
    return {"success": True, "data": {"text": text[:20000], "pages": pages,
                                      "chars": len(text)}}


def do_run(a: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    action = (a.get("action") or "").strip()

    if action == "extract_text":
        return extract_pdf_text(a.get("file_b64", ""))

    if action == "set_profile":
        text = clean_text(a.get("profile_text") or "")
        url = (a.get("profile_url") or a.get("job_url") or "").strip()
        fetched_from = ""
        url_note = ""
        if len(text) < 50 and url:
            fetched, err = fetch_url(url)
            if err:
                return {"success": False, "error": err, "data": {"hint": err, "url": url}}
            text, fetched_from = fetched, url
        elif url and len(text) >= 50:
            url_note = " (used the text you provided — the URL wasn't fetched)"
        if len(text.strip()) < 50:
            return {"success": False,
                    "error": ("Paste your CV text, upload a CV file, or give a profile URL — "
                              "need at least a few lines.")}
        prof = parse_profile(text)
        llm = try_sample(ctx, SYS,
                         f"Extract JSON {{\"name\",\"email\",\"headline\",\"years\",\"skills\":[],\"top_bullets\":[]}} "
                         f"from this CV — copy facts only, invent nothing:\n{text[:4000]}",
                         800, {"type": "json_object"})
        if (j := as_json(llm)) and isinstance(j, dict):
            prof.update({k: j[k] for k in ("name", "email", "headline", "years", "skills", "top_bullets") if k in j})
        where = f" (read from {fetched_from})" if fetched_from else url_note
        display = (f"Profile saved{where}: {prof.get('name') or 'candidate'} · "
                   f"~{prof.get('years', 0):g}y · {len(prof['skills'])} skills "
                   f"({', '.join(prof['skills'][:8])}).\n"
                   f"Next: paste the job description and press “Score fit”.")
        return {"success": True, "data": {"profile": prof, "text": text[:12000],
                                          "fetched_from": fetched_from,
                                          "display_text": display}}

    if action in ("score", "tailor", "cover", "interview"):
        ptext = clean_text(a.get("profile_text", ""))
        jtext = clean_text(a.get("job_text", ""))
        if a.get("job_url") and len(jtext.strip()) < 50:
            jtext, _err = fetch_url(a["job_url"])
        if len(ptext.strip()) < 50:
            return {"success": False, "error": "Save your profile first (step 1)."}
        if len(jtext.strip()) < 50:
            return {"success": False,
                    "error": "Paste the job description (or a fetchable job URL) — that's step 2."}
        prof = parse_profile(ptext)
        jd_skills = find_skills(jtext)

        if action == "score":
            llm = try_sample(ctx, SYS,
                             f"Score fit 0-100 as JSON {{\"fit_score\":n,\"verdict\":s,\"matched\":[],\"missing\":[],\"reasons\":[]}}.\n"
                             f"CV:\n{ptext[:3500]}\nJD:\n{jtext[:3500]}",
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
            return do_tailor(a, ctx, ptext, jtext, prof, jd_skills)

        if action == "cover":
            co, ro = a.get("company") or "the team", a.get("role") or "this role"
            name = prof.get("name") or "Applicant"
            strengths = ", ".join(prof["skills"][:4]) or "relevant experience"
            llm = try_sample(ctx, SYS,
                             f"Write a 150-word cover letter, forward-looking, no invented jobs or "
                             f"achievements — only facts stated in the CV. Company={co}, Role={ro}.\n"
                             f"CV:\n{ptext[:3500]}\nJD:\n{jtext[:2500]}",
                             600)
            if llm and len(llm.strip()) > 80:
                body = llm.strip()
            else:
                bullets = [b for _, b in rank_bullets(prof["bullets"], jd_skills)[:2]]
                body = (f"Dear Hiring Manager at {co},\n\n"
                        f"I'm applying for {ro}. My background in {strengths} lines up with what you "
                        f"described: {', '.join(jd_skills[:3]) if jd_skills else 'the role'}.\n\n"
                        + "\n".join(f"• {b}" for b in bullets) +
                        f"\n\nI'd welcome a conversation about what I can ship in the first 90 days.\n\n"
                        f"Best regards,\n{name}")
            rep = grounding_report(body, ptext)
            if rep["risk"] or rep["new_numbers"]:
                body += ("\n\n— Check before sending: these aren't in your CV — "
                         + ", ".join(sorted(set(rep["risk"] + rep["new_numbers"]))[:8])
                         + ". Reword or delete them.")
            return {"success": True, "data": {"cover_letter": body, "display_text": body}}

        # interview
        return do_interview(a, ctx, ptext, jtext, prof, jd_skills)

    if action == "track":
        try:
            apps = json.loads(a.get("apps_json", "[]"))
        except json.JSONDecodeError:
            return {"success": False, "error": "apps_json is not valid JSON."}
        if not isinstance(apps, list):
            apps = []
        apps = [e for e in apps if isinstance(e, dict)]
        op = (a.get("op") or "list").strip()
        now = time.time()

        # collapse any pre-existing duplicates (the user may already have them)
        seen: dict[tuple[str, str], dict[str, Any]] = {}
        cleaned: list[dict[str, Any]] = []
        merged = 0
        for e in apps:
            k = app_key(e)
            if k != ("", "") and k in seen:
                prev = seen[k]
                prev["applied_at"] = min(prev.get("applied_at", now), e.get("applied_at", now))
                if e.get("status") and e["status"] != "applied":
                    prev["status"] = e["status"]
                merged += 1
                continue
            if k != ("", ""):
                seen[k] = e
            cleaned.append(e)
        apps = cleaned

        if op == "add":
            try:
                ref = json.loads(a.get("app_ref", "{}"))
            except json.JSONDecodeError:
                return {"success": False, "error": "app_ref is not valid JSON."}
            if not isinstance(ref, dict):
                return {"success": False, "error": "app_ref must be a JSON object."}
            if not (ref.get("company") or ref.get("role")):
                co, ro = guess_company_role(a.get("job_text", ""))
                ref.setdefault("company", co)
                ref.setdefault("role", ro)
            k = app_key(ref)
            existing = next((e for e in apps if k != ("", "") and app_key(e) == k), None)
            if existing:
                existing["last_seen_at"] = int(now)
                msg = (f"Already tracked — {existing.get('company') or '?'} · "
                       f"{existing.get('role') or '?'} [{existing.get('status', 'applied')}], "
                       f"added {int((now - existing.get('applied_at', now)) // 86400)}d ago. "
                       f"No duplicate added.")
            else:
                ref.setdefault("id", uuid.uuid4().hex[:8])
                ref.setdefault("applied_at", int(now))
                ref.setdefault("status", "applied")
                apps.append(ref)
                n = len(apps)
                msg = (f"Tracked: {ref.get('company') or '?'} · {ref.get('role') or '?'} "
                       f"[applied] — {n} entr{'y' if n == 1 else 'ies'} in the pipeline now.")
            if merged:
                msg += f" (removed {merged} duplicate(s) already in the pipeline)"
        elif op in ("remove", "delete"):
            try:
                ref = json.loads(a.get("app_ref", "{}"))
            except json.JSONDecodeError:
                return {"success": False, "error": "app_ref is not valid JSON."}
            ref = ref if isinstance(ref, dict) else {}
            before = len(apps)
            apps = [e for e in apps
                    if not ((ref.get("id") and e.get("id") == ref["id"]) or
                            (app_key(e) == app_key(ref) and app_key(ref) != ("", "")))]
            msg = f"Removed {before - len(apps)} entry."
        elif op == "update":
            try:
                ref = json.loads(a.get("app_ref", "{}"))
            except json.JSONDecodeError:
                return {"success": False, "error": "app_ref is not valid JSON."}
            ref = ref if isinstance(ref, dict) else {}
            msg = "Nothing matched — nothing updated."
            for e in apps:
                if e.get("id") == ref.get("id") or (app_key(e) == app_key(ref) and app_key(ref) != ("", "")):
                    e.update({k: v for k, v in ref.items() if v != ""})
                    if a.get("note"):
                        e["status"] = a["note"]
                    e["updated_at"] = int(now)
                    msg = (f"Updated {e.get('company') or '?'} · {e.get('role') or '?'} "
                           f"[{e.get('status', 'applied')}]")
                    break
        elif op == "quiet":
            quiet = [e for e in apps if now - e.get("applied_at", now) > 10 * 86400
                     and e.get("status") in ("applied", "followup1")]
            lines = [f"• {e.get('company', '?')} — {e.get('role', '?')} "
                     f"({int((now - e.get('applied_at', now)) // 86400)}d quiet)" for e in quiet]
            return {"success": True, "data": {"apps": apps, "quiet": quiet,
                    "display_text": "Needs a nudge (drafts only, never auto-send):\n"
                    + ("\n".join(lines) if lines else "Nothing quiet. Pipeline is fresh.")}}
        else:
            quiet = [e for e in apps if now - e.get("applied_at", now) > 10 * 86400
                     and e.get("status") in ("applied", "followup1")]
            msg = (f"{len(apps)} tracked · {len(quiet)} quiet >10d"
                   + (f" · {merged} duplicate(s) removed" if merged else ""))

        lines = [f"• {e.get('company', '?')} — {e.get('role', '?')} [{e.get('status', 'applied')}]"
                 f" · {int((now - e.get('applied_at', now)) // 86400)}d"
                 for e in apps]
        display = msg + "\n" + ("\n".join(lines) if lines else "Empty — score a job, then track it.")
        quiet = [e for e in apps if now - e.get("applied_at", now) > 10 * 86400
                 and e.get("status") in ("applied", "followup1")]
        return {"success": True, "data": {"apps": apps, "message": msg,
                                          "quiet_count": len(quiet), "display_text": display}}

    return {"success": False,
            "error": f"Unknown action '{action}'. Use set_profile | score | tailor | cover | interview | track | extract_text."}


def app_key(e: dict[str, Any]) -> tuple[str, str]:
    co = re.sub(r"[^a-z0-9]+", "", (e.get("company") or "").lower())
    ro = re.sub(r"[^a-z0-9]+", "", (e.get("role") or "").lower())
    if not co and not ro and e.get("job_url"):
        return ("url:" + re.sub(r"[^a-z0-9]+", "", str(e["job_url"]).lower())[:80], "")
    return (co, ro)


def guess_company_role(jd: str) -> tuple[str, str]:
    """Cheap fallback so a tracked entry never shows '?' — pulls
    'Role - Company' / 'Company — Role' out of the JD's first lines."""
    for line in [ln.strip() for ln in (jd or "").splitlines() if ln.strip()][:4]:
        m = re.search(r"\b(?:company|at)\s*[:\-]\s*([^,;\n]{2,60})", line, re.I)
        if m:
            company = m.group(1).strip()
            role_m = re.search(r"\b(?:role|position|title)\s*[:\-]\s*([^,;\n]{2,60})", line, re.I)
            if role_m:
                return company, role_m.group(1).strip()
        for sep in ("—", "–", " - ", " | ", " @ "):
            if sep in line:
                a, b = [p.strip() for p in line.split(sep, 1)]
                if 2 < len(a) < 80 and 2 < len(b) < 80:
                    return b, a  # "Senior AI PM - Nexus Labs" → role, company
    return "", ""


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
