#!/usr/bin/env python3
"""Transport-level test of the LLM (sampling) path.

Runs the real plugin as a subprocess over the real JSON-RPC stdio transport with
a host stub that answers `sampling/createMessage`. The canned answers are the
*bad* shapes from the Anna review (dict-shaped interview/STAR items, bullets that
invent facts) — the plugin must come back with plain text and no invented claim.

    python3 tests/test_llm_path.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLUGIN = os.path.join(ROOT, "executas", "job-hunt-copilot", "job_hunt_copilot_plugin.py")
PY = os.path.join(ROOT, "executas", "job-hunt-copilot", ".venv", "bin", "python")
if not os.path.exists(PY):
    PY = sys.executable
CV = open(os.path.join(ROOT, "fixtures", "reviewer-cv.txt"), encoding="utf-8").read()
JD = open(os.path.join(ROOT, "fixtures", "reviewer-jd.txt"), encoding="utf-8").read()

FAILS: list[str] = []
CHECKS = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f"  {detail}"))
    if not cond:
        FAILS.append(name)


# --- canned host answers, keyed by a phrase in the prompt -------------------
FIXTURE = [(json.loads(ln),)
           for ln in open(os.path.join(ROOT, "fixtures", "mock-llm.jsonl"), encoding="utf-8")
           if ln.strip() and not ln.startswith("#")]
ANSWERS = [(e["match"]["contentIncludes"],
            e["result"]["text"] if isinstance(e["result"], dict) else json.dumps(e["result"]))
           for (e,) in FIXTURE]


class Host:
    """Minimal sampling host: initialize → invoke, answering sampling requests."""

    def __init__(self) -> None:
        self.proc = subprocess.Popen([PY, PLUGIN], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, encoding="utf-8", bufsize=1)
        self.n = 0
        self.sampling_calls: list[str] = []

    def _read(self):
        return json.loads(self.proc.stdout.readline())

    def _send(self, obj):
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def handshake(self):
        self._send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        return self._read()

    def invoke(self, args: dict):
        self.n += 1
        rid = f"inv{self.n}"
        self._send({"jsonrpc": "2.0", "id": rid, "method": "invoke",
                    "params": {"arguments": args, "context": {"sampling_token": "tok"}}})
        while True:
            msg = self._read()
            if msg.get("id") == rid:
                return msg.get("result")
            if msg.get("method") == "sampling/createMessage":
                content = json.dumps(msg.get("params", {}).get("messages", ""))
                self.sampling_calls.append(content[:120])
                answer = next((a for needle, a in ANSWERS if needle in content),
                              "(mock) no fixture matched")
                self._send({"jsonrpc": "2.0", "id": msg["id"],
                            "result": {"role": "assistant", "model": "mock",
                                       "content": {"type": "text", "text": answer}}})
                continue

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            self.proc.kill()


h = Host()
init = h.handshake()
print("handshake:", json.dumps(init)[:120])
check("v2 handshake advertises sampling", "sampling" in json.dumps(init))

# 1. profile from the LLM (name + skills must land in the response)
r = h.invoke({"action": "set_profile", "profile_text": CV})
check("profile uses the host LLM", r["data"]["profile"].get("name") == "Alex Chen", str(r)[:200])
check("profile display text is plain", "Profile saved: Alex Chen" in r["data"]["display_text"])

# 2. score from the LLM
r = h.invoke({"action": "score", "profile_text": CV, "job_text": JD,
              "company": "Nexus Labs", "role": "Senior AI Product Manager"})
check("score uses the host LLM", r["data"]["fit_score"] == 58, str(r["data"])[:200])

# 3. tailor: invented bullets must not survive
r = h.invoke({"action": "tailor", "profile_text": CV, "job_text": JD})
dd = r["data"]
joined = " ".join(dd["bullets"]).lower()
print("\n--- tailor bullets (LLM path) ---")
for b in dd["bullets"]:
    print("   •", b)
check("no dict repr in tailor output", "{'" not in dd["display_text"])
check("invented GTM bullet dropped", "gtm" not in joined and "end-to-end" not in joined)
check("invented scale number dropped", "250,000" not in joined)
check("near-duplicate dropped", len(dd["dropped_duplicates"]) >= 1)
check("ungrounded rewrites reverted", dd["replaced_ungrounded"] >= 3)
check("real CV numbers kept", any("18,500" in b for b in dd["bullets"]))
check("[ADD IF TRUE] bridges for the JD gaps", any("[ADD IF TRUE]" in b for b in dd["bridges"]))

# 4. cover letter: invented claims are flagged at the bottom
r = h.invoke({"action": "cover", "profile_text": CV, "job_text": JD,
              "company": "Nexus Labs", "role": "Senior AI Product Manager"})
letter = r["data"]["cover_letter"]
check("cover letter comes from the LLM", "Dear Hiring Manager at Nexus Labs" in letter)
check("cover letter warns about non-CV claims", "aren't in your CV" in letter, letter[-200:])

# 5. interview pack: the reviewer's raw-dict shape
r = h.invoke({"action": "interview", "profile_text": CV, "job_text": JD,
              "company": "Nexus Labs", "role": "Senior AI Product Manager"})
pack = r["data"]
dt = pack["display_text"]
print("\n--- interview pack (LLM path) ---")
print("\n".join(dt.splitlines()[:22]))
check("no dict repr in interview output", "{'" not in dt and "gap_flag" not in dt)
check("reviewer's RAG question survives as text",
      any("RAG or vector-DB" in q["question"] for q in pack["questions"]))
check("reviewer's fabricated GTM answer is flagged, not asserted",
      "Led the end-to-end GTM launch of complex SaaS infrastructure" not in dt.replace(
          "[ADD IF TRUE] Led the end-to-end GTM launch of complex SaaS infrastructure", ""))
check("STAR background 'high user churn' flagged",
      "Situation: The product had high user churn" not in dt)
check("STAR labels present", all(f"{x}:" in dt for x in ("Situation", "Task", "Action", "Result")))
check("STAR keeps the CV's own result", "grew to 18,500 MAU in 90 days" in dt)
check("questions carry Your proof / Suggested answer",
      "Your proof:" in dt and "Suggested answer:" in dt)

check("sampling was actually used for every generating action", len(h.sampling_calls) >= 5,
      str(len(h.sampling_calls)))
h.close()

print(f"\n{CHECKS - len(FAILS)}/{CHECKS} checks passed")
if FAILS:
    print("FAILED: " + ", ".join(FAILS))
    sys.exit(1)
print("ALL GREEN")
