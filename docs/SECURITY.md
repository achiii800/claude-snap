# claude-snap PWA — security model

This document is the threat model for the static PWA hosted at
`https://achiii800.github.io/claude-snap/`.

## TL;DR

- **No backend.** The app is static HTML/JS/CSS served by GitHub Pages over HTTPS. There is no server holding state, no database, no analytics.
- **No third-party scripts, libraries, fonts, or analytics.** Every byte loaded by the page is in this repo. Audit is direct.
- **Your `.snap.jsonl` is parsed in your browser.** It is never uploaded anywhere.
- **Your Anthropic API key is stored in `localStorage` of this origin only.** It is sent only to `https://api.anthropic.com`, only via the `x-api-key` header, only when you press *Ask Claude*. It is never logged, never put on the URL, never written to a cookie.
- **The only network destination is `api.anthropic.com`.** This is enforced by a Content-Security-Policy `connect-src` allowlist set in the HTML `<meta>` tag.

## What can go wrong, and what stops it

### XSS-induced credential theft

**Risk.** A malicious `.snap.jsonl` (or pasted text) tries to inject a `<script>` or an event handler when the conversation is rendered, then exfiltrates `localStorage['claude-snap.api-key.v1']`.

**Mitigations.**
- All conversation rendering uses `textContent`, never `innerHTML`. There is no path from user-controlled content into the DOM as HTML.
- Strict CSP: `default-src 'none'; script-src 'self'; connect-src https://api.anthropic.com`. Even if a script element were somehow injected, the browser refuses to execute it because it isn't from `'self'`.
- No `eval`, no `new Function`, no inline event handlers. CSP enforces this.

### Supply-chain attack

**Risk.** A compromised CDN or third-party library gets pulled in and exfiltrates your data.

**Mitigations.**
- **Zero third-party runtime dependencies.** No CDN scripts, no Google Fonts, no analytics, no error reporting. Everything served from this repo.
- The codec is a single `codec.js` file in this repo. You can read it.
- The service worker (`sw.js`) caches only same-origin assets and never intercepts API traffic.

### Network manipulation (MITM)

**Risk.** A network attacker injects malicious responses or strips HTTPS.

**Mitigations.**
- GitHub Pages serves only over HTTPS.
- HSTS header (`Strict-Transport-Security: max-age=63072000`) set via meta refresh — the browser refuses HTTP after first visit.
- `upgrade-insecure-requests` directive promotes any accidental HTTP request to HTTPS.

### Origin pollution on `*.github.io`

**Risk.** `localStorage` on `achiii800.github.io` is shared across all of `achiii800`'s GitHub Pages projects. A bug or compromise in another project on the same origin could read the API key.

**Mitigations.**
- Documented limitation. A user concerned about this should run the PWA from a custom domain (or self-host, or run locally — `python3 -m http.server` in `docs/` works fine).
- The key is stored under a versioned namespaced key (`claude-snap.api-key.v1`) so we can rotate the storage location if needed.
- Future: use the Web Locks / OPFS APIs or per-origin partitioned storage when widely available.

### Hostile API responses

**Risk.** An attacker controls the response from `api.anthropic.com` (e.g., compromise on Anthropic's side or a network attacker if HTTPS is somehow defeated). Could the response break out of the bubble and execute code?

**Mitigations.**
- Responses are rendered with `textContent`. No HTML rendering of responses.
- The CSP forbids `script-src` from any non-self origin. Even an injected `<script>` tag with code from `api.anthropic.com` would be blocked.

### Lost/stolen device

**Risk.** Someone with physical access to your unlocked device can open the PWA and see the API key (and use it).

**Mitigations.**
- Don't check *Remember in this browser* on a shared device.
- Press *Forget key* to remove it from `localStorage`.
- The key never leaves the browser, but the browser is the trust boundary. Treat the device as the credential.

## What we deliberately don't do

- **No server-side proxy.** We do not run a server that speaks to Anthropic on your behalf. Adding one would add a credential-bearing middlebox you'd have to trust. Direct browser → `api.anthropic.com` is the most paranoid possible architecture.
- **No analytics.** Not Google, not Plausible, not anything. We don't know who uses this.
- **No error reporting.** Errors stay in the browser console.
- **No service-worker caching of API traffic.** Only the static shell is cached.

## Self-hosting / running locally

If you don't trust GitHub Pages or the `*.github.io` origin model, the PWA runs locally:

```bash
git clone https://github.com/achiii800/claude-snap.git
cd claude-snap/docs
python3 -m http.server 8080
# open http://localhost:8080
```

Same code, same behavior, your own origin.

## Reporting issues

If you find a security issue, open a GitHub issue or email the maintainer (see repo README). Please don't disclose anything that could compromise other users of the hosted PWA before a fix is in place.
