# Privacy Policy — Job Hunt Copilot

Effective: 2026-10-05. Contact: https://github.com/memeshee/anna-job-copilot/issues

Job Hunt Copilot runs **inside your Anna agent** (local or your own cloud
agent). The developer operates **no server, no account system, no analytics,
no tracking**.

## What stays with you

- Your CV/profile text and uploaded files, job descriptions you paste, fit
  scores, generated bullets / cover letters / interview packs, and your
  application pipeline are stored in **Anna's on-agent storage** (the app's
  sandboxed `storage` API). They never leave your agent except as described
  below. Uninstalling/removing the app's data deletes them.

## Network requests the app makes (only when you ask)

- **Job-post URL:** if you paste a job-post link and hit Score, the app
  fetches that page (with a normal browser user-agent) to extract the job
  description text. That request goes to the site you chose, carrying no
  personal data beyond a standard page view.
- **LinkedIn / portfolio URL:** same deal — best-effort fetch of a page you
  supplied, to prefill your profile. LinkedIn usually blocks bots, so paste
  fallback is offered.
- No other network calls. No ads, no trackers, no third-party SDKs.

## What we never do

- No sale, rental, or sharing of your data with anyone.
- No training of models on your data (all matching/scoring is local,
  deterministic code — no external AI API is called, ever).
- No cross-app data access beyond Anna's own permission model.

## Changes

Material changes land here in the public repo with a new effective date.
Continued use after a change means acceptance; stop using the app if you
disagree.
