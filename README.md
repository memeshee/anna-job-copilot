# 🎯 Job Hunt Copilot — Anna AI OS App

Paste your CV once, score any job in 30 seconds. Tailored bullets, cover letter,
interview pack, application pipeline with follow-up nudges.

Built for the [Anna AI App Builder Program](https://dorahacks.io/hackathon/2349/buidl)
(DoraHacks 2349) — port of the proven pipeline from
[MadsLorentzen/ai-job-search](https://github.com/MadsLorentzen/ai-job-search) (43k★ MIT).

## Demo

`assets/demo.mp4` — 43s end-to-end (profile → score → mirror → cover → interview → track).

## How it works

Single-dispatcher Executa tool (`run` action):
`set_profile | score | tailor | cover | interview | track`.
Host LLM sampling (`llm.sample`) when granted, deterministic local fallback otherwise.
UI persists profile + pipeline via host storage. Honest by design: gaps become
`[ADD IF TRUE]` bridge lines — never invented experience.

## Develop

```bash
anna-app validate
anna-app dev --no-llm   # offline harness at http://localhost:5180/
anna-app dev --llm real # against production Nexus (needs anna-app login)
```
