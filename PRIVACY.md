# Privacy Policy — Job Hunt Copilot

Effective: 2026-10-09. Contact: https://github.com/memeshee/anna-job-copilot/issues

Job Hunt Copilot runs **inside your Anna agent** (local or your own cloud
agent). The developer operates **no server, no account system, no analytics,
no tracking**. Nothing you enter is sent to the developer or any third party.

## What stays with you

- Your CV/profile text, uploaded CV files, job descriptions you paste, fit
  scores, generated bullets / cover letters / interview packs, and your
  application pipeline are stored in **Anna's on-agent storage** (the app's
  sandboxed `storage` API) and nowhere else. Uninstalling the app or clearing
  its data deletes them.
- CV files you upload (.pdf/.txt/.md) are read **locally**: a PDF is parsed in
  the app's own sandbox (bundled pdf.js), and if that fails it is handed to the
  bundled Executa tool running on your agent (pypdf). The bytes never leave
  your machine/agent.

## What the app calls on your machine or agent

- **Your agent's LLM (Anna sampling).** When your agent exposes the LLM
  sampling capability, the bundled tool asks *your own agent* for the wording
  of the fit verdict, mirrored bullets, cover letter and interview pack. This
  is Anna's own model call under your account's policy — the app ships no API
  key, calls no developer endpoint, and stores no prompt history of its own.
  If sampling is unavailable, the app falls back to fully local, deterministic
  text generation and says so.
- **One optional outbound fetch, to a URL you supply.** If you paste a
  job-post URL, the tool fetches that exact page to extract the job
  description text. That request goes to the site you chose and carries no
  personal data beyond a standard page view.
- **LinkedIn URLs are never fetched.** LinkedIn answers automated requests with
  a login wall, so the app tells you to export your profile as a PDF (or paste
  the text) instead of pretending it can read the page.

## What we never do

- No sale, rental, or sharing of your data with anyone.
- No third-party analytics, ads, trackers or SDKs.
- No training of any model on your data by the developer.
- No cross-app data access beyond Anna's own permission model.

## Changes

Material changes land here in the public repo with a new effective date.
Continued use after a change means acceptance; stop using the app if you
disagree.
