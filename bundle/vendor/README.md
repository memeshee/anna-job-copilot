# Vendored dependencies

`pdf.mjs` and `pdf.worker.mjs` are the legacy ES-module builds of
[pdf.js](https://github.com/mozilla/pdf.js) (npm `pdfjs-dist@5.4.624`),
Apache-2.0 licensed — see `LICENSE-pdfjs.txt`.

They are vendored into the app bundle (no CDN, no external origin) so the CV
upload works offline, inside the agent, with every Anna CSP:

- `app.js` sets `globalThis.pdfjsWorker` from `pdf.worker.mjs` before calling
  `getDocument`, which makes pdf.js run its worker on the main thread
  (`LoopbackPort`) instead of `new Worker(...)` — no `worker-src` dependency.
- `isEvalSupported: false` keeps it free of `new Function` (no `unsafe-eval`).
- The modules are dynamically imported only when a PDF is actually picked.

If either module fails to load (or the PDF has no text layer), `app.js` falls
back to the Executa's `extract_text` action (pypdf, bundled in the binary) and
finally to a copy-the-text instruction.
