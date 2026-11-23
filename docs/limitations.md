# HAR2MCP Known Limitations

HAR2MCP generates Model Context Protocol (MCP) servers by analyzing browser network traffic captures (`.har`). While HAR2MCP accurately extracts API routes, schemas, and credential mechanisms, certain security architectures and dynamic protocols cannot be replayed statically.

---

## 1. Cryptographic Request Signing & HMAC

### Limitation
Certain APIs do not accept static authentication tokens or cookies. Instead, they require clients to calculate a dynamic cryptographic signature for every single outbound request:
- **AWS Signature Version 4 (SigV4)**: Uses HMAC-SHA256 computed over the HTTP method, canonical URI, query parameters, headers, and request payload digest, coupled with an `X-Amz-Date` timestamp.
- **OAuth 1.0a (e.g. Twitter/X)**: Requires HMAC-SHA1 signatures calculated across nonces and request parameters.
- **Financial & Crypto Exchange APIs (Coinbase, Binance)**: Require request timestamping and HMAC signing over the request body.

### Workaround
Generated MCP servers inject credentials via standard headers, cookies, or query parameters. If the target API requires custom HMAC signing, developers must hook into the generated HTTP client (`httpx` transport or client middleware) to compute signatures at runtime.

---

## 2. Interactive OAuth 2.0 & Browser Redirects

### Limitation
OAuth 2.0 flows that require browser-based interaction cannot be autonomously executed by the generated MCP server:
- **Authorization Code Flow with PKCE**: Involves interactive login pages, multi-factor authentication (MFA) prompts, and 302 redirects to client callback URLs.
- **Single Sign-On (SSO)**: SAML or OIDC redirects across enterprise identity providers (Okta, Google Workspace, Azure AD).

### Workaround
HAR2MCP follows a strict principle: **do NOT automate logins**.
Generated servers read access tokens and session credentials directly from environment variables (e.g. `HAR2MCP_AUTH_TOKEN`). Users obtain a valid token or session via their standard browser/app and supply it to the environment.

---

## 3. Anti-Bot, WAF Challenges & Fingerprinting

### Limitation
Web Application Firewalls (WAFs) and bot-detection vendors inspect traffic beyond standard HTTP payloads:
- **Challenges**: Cloudflare Turnstile, DataDome, Akamai Bot Manager, PerimeterX / HUMAN Security, and Google reCAPTCHA.
- **Browser Fingerprinting**: TLS Client Hello (JA3/JA4) fingerprinting, HTTP/2 frame negotiation patterns, and Canvas/WebGL device telemetry.

When an API endpoint is protected by active bot mitigation, replaying the request from a Python `httpx` or FastMCP process will be intercepted by challenge pages (e.g. 403 Forbidden or 503 Service Unavailable).

### Workaround
- Capture HAR recordings from official API endpoints or mobile app endpoints rather than web scraping endpoints protected by anti-bot challenge scripts.
- Use exported browser session cookies and headers that have already cleared WAF validation.

---

## 4. Single-Use Nonces & Short-Lived Tokens

### Limitation
- Some security models enforce strict single-use anti-replay tokens (e.g. state-changing CSRF tokens invalidated immediately upon execution).
- If an API invalidates a CSRF token after a single POST request, subsequent calls with the same environment variable value will fail with a 403 or 419 status code.

### Workaround
- When generating servers for APIs with rotating CSRF tokens, a dedicated fetch step or token-refresh hook must be added to obtain a fresh CSRF token prior to mutation calls.

---

## 5. IP & Geolocation Pinning

### Limitation
- Web applications occasionally bind session cookies or tokens to the specific client IP address or subnet that initiated the session.
- If a HAR file is recorded on one network (e.g. home broadband) and the generated MCP server is run on a remote server, container, or cloud instance with a different IP address, the server may immediately invalidate the session.

### Workaround
Ensure the generated MCP server runs on the same network environment or with updated credentials valid for the running host's IP address.
