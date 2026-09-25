# Anna Job Hunt Copilot — Build Spec (Dora 2349)

## Thesis
Job seekers drown past 30 apps. ChatGPT forgets your resume and hallucinates. Copilot remembers: profile + every JD + what you claimed per role, and turns 60-min tailoring into 10-min.

## Why this wins (MAU game, not judges)
- Daily loop with deadline pressure (apply → follow-up → interview prep). 200 MAU = ~7 real users/day.
- Port core from MadsLorentzen/ai-job-search (43.9k★ MIT, master): fit-score → tailor CV/cover → interview drill → outcome tracker. Strip Danish scrapers, LaTeX, Claude Code dependency — keep the pipeline prompts + tracker schema.
- Review-safe: UI + 1 Tool dispatcher + APS memory + sampling. No API keys required on hosted runs.

## Core loop (each = Qualified Run)
1. Upload master CV (or paste) → stored in APS files/KV.
2. Paste JD → `score` returns fit % + gaps + verdict (sampling, json_schema).
3. `tailor` → mirror CV bullets + cover letter draft (display-block verbatim).
4. `interview` → prep pack from archived CV+JD (questions mapped to STARs, no invented experience).
5. `track` → Kanban of apps, follow-up nudges (10-day quiet rule), outcome log.

## Tech wedge vs ChatGPT
- Persistent memory: profile + per-app archive (CV sent, JD, cover, status). "What did I tell Acme?" recall.
- Honest bridge: gaps get bridge answers, never invented jobs (from source SECURITY + interview protocol).
- Follow-up engine: drafts only, max 2/app, only claims from submitted materials.

## MVP boundary (ship Sept 27)
Must: CV upload/paste, JD paste, score + tailor + cover, tracker list, display-blocks, error/empty states, `anna-app dev` + validate pass.
Should: interview drill, follow-up drafter, history recall.
Stretch: web.fetch JD from URL, Notion sync, LaTeX export.
Non-goals: auto-scrape portals, auto-apply/send, salary lookup.

## Review-risk fixes (from Anna gaps brief)
- 1 dispatcher tool (`action: score|tailor|cover|interview|track`), canonical parameters[], stdin loop, stderr-only logs, root manifest.json, minimal host_capabilities [llm.sample, host.upload], storage/files grants.
- Real screenshots of own flow, truthful ≤160ch tagline, self-install before submit-review.
- Bundle init→PUT→finalize, re-submit after every version cut.

## Growth to 200 MAU
- Post where job pain lives: r/jobs, Telegram job groups, Discord, LinkedIn ("I got hired with this, now it's 1-click"). Shareable win = interview invite.
- Allowed: normal marketing, free access. Banned: paying for runs, bots, self-farm. Server logs decide.

## Execution
- Now→Sept 26: scaffold anna-app (fork focus-flow), dispatcher + 3 actions + UI drop zone.
- Sept 27: local dev pass, submit for review + Dora BUIDL draft (GitHub + demo <5min).
- Sept 28-30: review fixes, publish, MAU push. Monthly update after (required to keep grant).
