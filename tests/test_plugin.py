#!/usr/bin/env python3
"""Reviewer-feedback regression suite for the job-hunt-copilot Executa.

Runs the plugin in-process (no transport) and asserts every fix that came out of
the Anna review of 2026-10-09, using the reviewer's own CV/JD inputs.

    python3 tests/test_plugin.py            # deterministic paths + mock LLM
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "executas", "job-hunt-copilot"))

import job_hunt_copilot_plugin as P  # noqa: E402

FIX = os.path.join(ROOT, "fixtures")
CV = open(os.path.join(FIX, "reviewer-cv.txt"), encoding="utf-8").read()
JD = open(os.path.join(FIX, "reviewer-jd.txt"), encoding="utf-8").read()

FAILS: list[str] = []
CHECKS = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILS.append(name)


def run(action: str, /, **kw) -> dict:
    args = {"action": action}
    args.update(kw)
    return P.do_run(args, {})


def run_llm(action: str, llm_payload, /, **kw) -> dict:
    """Run with a stub host LLM that returns `llm_payload` (str or obj)."""
    text = llm_payload if isinstance(llm_payload, str) else json.dumps(llm_payload)
    orig = P.try_sample
    P.try_sample = lambda *a, **k: text
    try:
        return run(action, **kw)
    finally:
        P.try_sample = orig


# ---------------------------------------------------------------------------
# 1. Interview pack / STAR: plain text, never raw dicts
# ---------------------------------------------------------------------------
print("\n[1] Interview pack + STAR render as plain text")

# exactly the shape the reviewer saw, including the raw-dict fields
BAD_INTERVIEW = {
    "questions": [
        {"question": "How have you used RAG and vector DBs?",
         "candidate_proof": "Led launch of AI assistant, grew to 18,500 MAU in 90 days",
         "gap_flag": True,
         "suggested_answer": "Led the end-to-end GTM launch of complex SaaS infrastructure"},
        "Why this company and this role?",
        {"q": "Tell me about a missed deadline.", "proof": "Cut onboarding drop-off by 22% via A/B tests",
         "answer": "Reduced churn from initial user research to post-launch optimization"},
    ],
    "star": [
        {"title": "Led launch of AI assistant",
         "situation": "The product had high user churn",
         "task": "Own the launch",
         "action": "Led launch of AI assistant",
         "result": "grew to 18,500 MAU in 90 days"},
        "Situation→Task→Action→Result for: Cut onboarding drop-off by 22% via A/B tests",
    ],
    "company_checks": ["Latest funding/news + why now"],
}
r = run_llm("interview", BAD_INTERVIEW, profile_text=CV, job_text=JD,
            company="Nexus Labs", role="Senior AI Product Manager")
d = r["data"]
dt = d["display_text"]
print("---- interview display (LLM mock) ----")
print(dt)
check("no python dict repr in output", "{'" not in dt and '{"' not in dt)
check("Question label present", "Q1." in dt and "Your proof:" in dt and "Suggested answer:" in dt)
check("STAR labels present", all(f"{f}:" in dt for f in ("Situation", "Task", "Action", "Result")))
check("no raw dict rendered", "gap_flag" not in dt and "candidate_proof" not in dt)
check("invented GTM claim flagged, not asserted",
      "Led the end-to-end GTM launch of complex SaaS infrastructure" not in dt
      or "[ADD IF TRUE]" in dt)
check("invented churn background flagged",
      not re.search(r"(?<!ADD IF TRUE\] )The product had high user churn", dt))
check("string question normalised to a question object",
      any("Why this company" in q["question"] for q in d["questions"]))
check("flat string STAR entry normalised", any("Cut onboarding" in s["headline"] for s in d["star"]))

# deterministic path (no host LLM): still plain text, still S/T/A/R
r0 = run("interview", profile_text=CV, job_text=JD, company="Nexus Labs",
         role="Senior AI Product Manager")
dt0 = r0["data"]["display_text"]
check("no-LLM interview pack has no dict repr", "{'" not in dt0)
check("no-LLM interview pack has S/T/A/R", all(f"{f}:" in dt0 for f in ("Situation", "Task", "Action", "Result")))

# ---------------------------------------------------------------------------
# 2. Mirror bullets: no invented facts; extras only as [ADD IF TRUE]
# ---------------------------------------------------------------------------
print("\n[2] Mirror bullets are grounded in the CV")

BAD_TAILOR = {
    "bullets": [
        {"text": "Led the end-to-end GTM launch of complex SaaS infrastructure",
         "source_span": "Led launch of AI assistant, grew to 18,500 MAU in 90 days"},
        {"text": "Drove adoption from initial user research to post-launch optimization",
         "source_span": "Led launch of AI assistant, grew to 18,500 MAU in 90 days"},
        {"text": "Cut onboarding drop-off by 22% via A/B tests",
         "source_span": "Cut onboarding drop-off by 22% via A/B tests"},
        {"text": "Cut onboarding drop-off by 22% with A/B tests",  # near-duplicate
         "source_span": "Cut onboarding drop-off by 22% via A/B tests"},
        {"text": "Led launch of AI assistant, grew to 18,500 MAU in 90 days",
         "source_span": "Led launch of AI assistant, grew to 18,500 MAU in 90 days"},
    ],
    "bridges": ["GTM launch experience"],
}
r = run_llm("tailor", BAD_TAILOR, profile_text=CV, job_text=JD,
            company="Nexus Labs", role="Senior AI Product Manager")
d = r["data"]
print("---- mirror bullets display (LLM mock) ----")
print(d["display_text"])
bullets = d["bullets"]
joined = " ".join(bullets).lower()
for banned in ("end-to-end", "gtm", "complex saas infrastructure", "post-launch optimization",
               "high user churn", "user research", "complex"):
    check(f"invented '{banned}' not asserted in bullets", banned not in joined)
check("grounded bullets kept", any("18,500 MAU" in b for b in bullets))
check("near-duplicate dropped", len(d["dropped_duplicates"]) >= 1)
check("reverted ungrounded rewrites", d["replaced_ungrounded"] >= 2)
check("[ADD IF TRUE] suggestions present", any("[ADD IF TRUE]" in b for b in d["bridges"]))
check("bridges list the JD gaps", any("gtm" in b.lower() or "ai" in b.lower() for b in d["bridges"]))
check("fact-check footer present", "Fact check:" in d["display_text"] and "0 invented claims" in d["display_text"])
check("bullets trace to CV sources", all(
    P.jaccard(b, s) > 0.3 for b, s in zip(bullets, d["sources"]) if s))

# invented metric must not survive
r2 = run_llm("tailor", {"bullets": [{"text": "Scaled the AI assistant to 250,000 MAU in 90 days",
                                     "source_span": "Led launch of AI assistant, grew to 18,500 MAU in 90 days"}],
                        "bridges": []}, profile_text=CV, job_text=JD)
check("invented number rejected", "250,000" not in " ".join(r2["data"]["bullets"]))

# no-LLM path: bullets are the CV's own lines
r3 = run("tailor", profile_text=CV, job_text=JD)
check("no-LLM bullets come from the CV", all(
    b in CV for b in r3["data"]["bullets"]), str(r3["data"]["bullets"]))
check("no-LLM bridges flagged", all(b.startswith("[ADD IF TRUE]") for b in r3["data"]["bridges"]))

# ---------------------------------------------------------------------------
# 3. Pipeline: one click = one entry, no duplicates
# ---------------------------------------------------------------------------
print("\n[3] Pipeline de-duplication")
apps: list = []
a1 = run("track", op="add", apps_json=json.dumps(apps),
         app_ref=json.dumps({"company": "Nexus Labs", "role": "Senior AI Product Manager"}))
apps = a1["data"]["apps"]
check("first add creates one entry", len(apps) == 1)
check("first add reports one entry", "1 entry" in a1["data"]["display_text"])

a2 = run("track", op="add", apps_json=json.dumps(apps),
         app_ref=json.dumps({"company": "Nexus Labs", "role": "Senior AI Product Manager"}))
check("second add does not duplicate", len(a2["data"]["apps"]) == 1)
check("second add explains itself", "Already tracked" in a2["data"]["message"])
rows = [ln for ln in a2["data"]["display_text"].splitlines() if ln.strip().startswith("•")]
check("pipeline list shows a single row", len(rows) == 1, str(rows))

# case/whitespace variant of the same job
a3 = run("track", op="add", apps_json=json.dumps(a2["data"]["apps"]),
         app_ref=json.dumps({"company": "nexus labs ", "role": "senior ai product manager"}))
check("case-insensitive dedupe", len(a3["data"]["apps"]) == 1)

# pre-existing duplicates get collapsed
dupes = [{"company": "Nexus Labs", "role": "Senior AI Product Manager", "status": "applied",
          "applied_at": 1776000000, "id": "aaa"},
         {"company": "Nexus Labs", "role": "Senior AI Product Manager", "status": "applied",
          "applied_at": 1776000000, "id": "bbb"}]
a4 = run("track", op="add", apps_json=json.dumps(dupes),
         app_ref=json.dumps({"company": "Nexus Labs", "role": "Senior AI Product Manager"}))
check("existing duplicates removed", len(a4["data"]["apps"]) == 1)
check("duplicate removal reported", "duplicate" in a4["data"]["message"].lower())

# missing company/role is derived from the JD instead of showing "?"
a5 = run("track", op="add", apps_json="[]", app_ref="{}", job_text=JD)
row = a5["data"]["apps"][0]
check("company/role derived from JD", row.get("company") == "Nexus Labs" and
      "Senior AI Product Manager" in (row.get("role") or ""), str(row))

# quiet check still works
a6 = run("track", op="quiet", apps_json=json.dumps([
    {"company": "Nexus Labs", "role": "Senior AI Product Manager", "status": "applied",
     "applied_at": int(__import__("time").time()) - 20 * 86400}]))
check("quiet nudge works", "Needs a nudge" in a6["data"]["display_text"])

# ---------------------------------------------------------------------------
# 4. Profile: LinkedIn URL gives instructions, not a dead button
# ---------------------------------------------------------------------------
print("\n[4] Profile URL handling")
r = run("set_profile", profile_text="", profile_url="https://www.linkedin.com/in/alex-chen")
check("linkedin url returns a failure, not silence", r["success"] is False)
check("linkedin message is actionable", "Save to PDF" in r["error"] and "paste" in r["error"].lower())

r = run("set_profile", profile_text="", profile_url="https://127.0.0.1:9/nope")
check("bad non-linkedin url returns actionable error",
      r["success"] is False and "paste" in r["error"].lower())

r = run("set_profile", profile_text=CV)
check("pasting the CV still saves the profile", r["success"] and r["data"]["profile"]["skills"])

# ---------------------------------------------------------------------------
# 5. PDF text extraction via the tool (fallback path for the upload control)
# ---------------------------------------------------------------------------
print("\n[5] PDF extraction (tool-side)")


def make_pdf(lines: list[str], compress: bool = True) -> bytes:
    def esc(s: str) -> str:
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    body = ["BT /F1 11 Tf 40 760 Td 14 TL"]
    for ln in lines:
        body.append(f"({esc(ln)}) Tj T*")
    body.append("ET")
    content = "\n".join(body).encode("latin-1")
    if compress:
        stream = zlib.compress(content)
        extra = b"/Filter /FlateDecode "
    else:
        stream = content
        extra = b""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Length " + str(len(stream)).encode() + b" " + extra + b">>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n").encode()
    return bytes(out)


pdf = make_pdf(CV.splitlines()[:14])
b64 = base64.b64encode(pdf).decode()
r = run("extract_text", file_b64=b64)
check("compressed PDF text extracted", r["success"] and "Alex Chen" in r["data"]["text"],
      str(r)[:200])
r = run("extract_text", file_b64=base64.b64encode(make_pdf(CV.splitlines()[:14], compress=False)).decode())
check("uncompressed PDF text extracted", r["success"] and "Alex Chen" in r["data"]["text"])
r = run("extract_text", file_b64=base64.b64encode(make_pdf([], compress=False)).decode())
check("PDF with no text gives a clear message", r["success"] is False and
      ("selectable" in r["error"] or "scan" in r["error"]), str(r)[:200])
r = run("extract_text", file_b64=base64.b64encode(b"%PDF-1.4\n" + b"\x00" * 400).decode())
check("broken PDF gives a clear message", r["success"] is False and
      ("paste" in r["error"].lower() or "couldn't read" in r["error"].lower()), str(r)[:200])
r = run("extract_text", file_b64=base64.b64encode(b"GIF89a" + b"\x00" * 400).decode())
check("non-PDF gives a clear message", r["success"] is False and "PDF" in r["error"])
r = run("extract_text", file_b64=base64.b64encode(b"").decode())
check("empty upload gives a clear message", r["success"] is False)

# ---------------------------------------------------------------------------
# 6. Score / cover sanity + no raw envelopes
# ---------------------------------------------------------------------------
print("\n[6] Score + cover")
r = run("score", profile_text=CV, job_text=JD, company="Nexus Labs", role="Senior AI Product Manager")
check("score returns a number and a verdict", isinstance(r["data"]["fit_score"], int)
      and r["data"]["verdict"])
check("score display is plain text", "Missing:" in r["data"]["display_text"])
r = run("cover", profile_text=CV, job_text=JD, company="Nexus Labs", role="Senior AI Product Manager")
check("cover letter generated", "Dear Hiring Manager" in r["data"]["cover_letter"])
r = run_llm("cover", "Dear Hiring Manager,\n\nI led the end-to-end GTM launch of a 250,000-user "
                     "enterprise platform and cut churn by 40%.\n\nBest regards,\nAlex",
            profile_text=CV, job_text=JD, company="Nexus Labs", role="Senior AI Product Manager")
check("cover letter flags non-CV claims",
      "aren't in your CV" in r["data"]["cover_letter"] or "not in your CV" in r["data"]["cover_letter"])

# ---------------------------------------------------------------------------
# 7. Grounding primitives
# ---------------------------------------------------------------------------
print("\n[7] Grounding guard units")
g = P.grounding_report("Led the end-to-end GTM launch", CV)
check("risk phrase + risk word caught", g["hard"] and "end-to-end" in g["risk"] and "gtm" in g["risk"])
g = P.grounding_report("Led launch of AI assistant, grew to 18,500 MAU in 90 days", CV)
check("verbatim CV bullet passes", not g["hard"])
g = P.grounding_report("Grew the AI assistant to 25,000 MAU", CV)
check("new number caught", g["hard"] and "25000" in g["new_numbers"])
g = P.grounding_report("Cut onboarding drop-off by 22% via A/B tests", CV)
check("CV A/B test bullet passes", not g["hard"], str(g))
g = P.grounding_report("Managed roadmap for 3 squads", CV)
check("another CV bullet passes", not g["hard"], str(g))
g = P.grounding_report("Analytics instrumentation for the funnel", CV)
check("JD-only tech words caught", g["hard"])
checks = P.normalize_questions([{"q": "Why us?", "proof": "x", "bridge": "y", "gap": True}])
check("question key aliases normalised", checks[0]["question"] == "Why us?" and
      checks[0]["answer"] == "y" and checks[0]["gap"] is True)
stars = P.normalize_star([{"title": "T", "star": {"s": "sit", "t": "task", "a": "act", "r": "res"}}])
check("nested star keys normalised", stars[0]["situation"] == "sit" and stars[0]["result"] == "res")

print(f"\n{CHECKS - len(FAILS)}/{CHECKS} checks passed")
if FAILS:
    print("FAILED: " + ", ".join(FAILS))
    sys.exit(1)
print("ALL GREEN")
