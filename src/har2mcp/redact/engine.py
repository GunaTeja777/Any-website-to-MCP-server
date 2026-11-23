"""Deterministic redaction engine for stripping credentials before LLM access."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from har2mcp.parser.models import (
    Cookie,
    Entry,
    Header,
    PostData,
    PostDataParam,
    QueryParam,
    Request,
    Response,
    ResponseContent,
)
from har2mcp.redact.models import (
    RedactionResult,
    SecretCategory,
    SecretLocation,
    SecretMetadata,
)

# JWT Regex pattern: header.payload.signature
JWT_PATTERN: re.Pattern[str] = re.compile(
    r"ey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+"
)

# CSRF header names (case-insensitive)
CSRF_HEADER_NAMES: frozenset[str] = frozenset(
    {
        "x-csrf-token",
        "x-xsrf-token",
        "csrf-token",
        "xsrf-token",
        "anti-csrf-token",
        "x-csrftoken",
        "x-anti-forgery-token",
        "authenticity-token",
        "csrftoken",
    }
)

# API key query parameter names (case-insensitive)
API_KEY_PARAM_NAMES: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "key",
        "token",
        "access_token",
        "auth",
        "secret",
        "app_key",
        "client_secret",
        "x-api-key",
        "auth_token",
    }
)

# Sensitive JSON field key regex
SENSITIVE_FIELD_REGEX: re.Pattern[str] = re.compile(
    r"(?i)^(.*_)?(password|passwd|token|secret|session|credential|api_key|apikey|"
    r"access_token|refresh_token|private_key|client_secret|auth)(_.*)?$"
)


class RedactionEngine:
    """Stateful redaction processor ensuring placeholder stability across entries."""

    def __init__(self) -> None:
        """Initialize counters and placeholder maps."""
        self._counts: dict[SecretCategory, int] = {
            SecretCategory.TOKEN: 0,
            SecretCategory.COOKIE: 0,
            SecretCategory.CSRF: 0,
            SecretCategory.API_KEY: 0,
            SecretCategory.SECRET: 0,
        }
        self._secret_to_placeholder: dict[str, str] = {}
        self._placeholder_to_secret: dict[str, str] = {}
        self._metadata: list[SecretMetadata] = []

    def get_or_register(
        self,
        value: str,
        category: SecretCategory,
        location: SecretLocation,
        key_name: str,
    ) -> str:
        """Retrieve existing placeholder or assign a new stable placeholder.

        Args:
            value: Original secret text.
            category: Classification category.
            location: Source location.
            key_name: Associated header/param/key identifier.

        Returns:
            Assigned placeholder string, e.g. '<TOKEN_1>'.
        """
        trimmed = value.strip()
        if not trimmed:
            return value

        if trimmed in self._secret_to_placeholder:
            return self._secret_to_placeholder[trimmed]

        self._counts[category] += 1
        num = self._counts[category]
        placeholder = f"<{category}_{num}>"

        clean_key = re.sub(r"[^A-Z0-9_]", "_", key_name.upper())
        env_var_name = f"HAR2MCP_{category}_{clean_key}_{num}"

        self._secret_to_placeholder[trimmed] = placeholder
        self._placeholder_to_secret[placeholder] = trimmed

        meta = SecretMetadata(
            placeholder=placeholder,
            original_value=trimmed,
            category=category,
            location=location,
            key_name=key_name,
            env_var_name=env_var_name,
        )
        self._metadata.append(meta)
        return placeholder

    def _redact_auth_header(self, value: str, key_name: str) -> str:
        """Sanitize Authorization header value while preserving scheme prefix."""
        parts = value.strip().split(None, 1)
        if len(parts) == 2 and parts[0].lower() in {"bearer", "basic", "token"}:
            scheme, token = parts
            placeholder = self.get_or_register(
                value=token,
                category=SecretCategory.TOKEN,
                location=SecretLocation.HEADER,
                key_name=key_name,
            )
            return f"{scheme} {placeholder}"

        return self.get_or_register(
            value=value,
            category=SecretCategory.TOKEN,
            location=SecretLocation.HEADER,
            key_name=key_name,
        )

    def _redact_cookie_header_value(self, header_value: str) -> str:
        """Sanitize Cookie header string (name=val; name2=val2)."""
        pairs = header_value.split(";")
        new_pairs: list[str] = []
        for pair in pairs:
            if "=" in pair:
                cname, cval = pair.split("=", 1)
                placeholder = self.get_or_register(
                    value=cval.strip(),
                    category=SecretCategory.COOKIE,
                    location=SecretLocation.HEADER,
                    key_name=cname.strip(),
                )
                new_pairs.append(f"{cname}={placeholder}")
            else:
                new_pairs.append(pair)
        return ";".join(new_pairs)

    def _redact_set_cookie_header_value(self, header_value: str) -> str:
        """Sanitize Set-Cookie header while preserving directives (Path, HttpOnly, etc.)."""
        parts = header_value.split(";")
        if not parts:
            return header_value

        first = parts[0]
        if "=" in first:
            cname, cval = first.split("=", 1)
            placeholder = self.get_or_register(
                value=cval.strip(),
                category=SecretCategory.COOKIE,
                location=SecretLocation.HEADER,
                key_name=cname.strip(),
            )
            parts[0] = f"{cname}={placeholder}"

        return ";".join(parts)

    def redact_headers(self, headers: list[Header]) -> list[Header]:
        """Redact Authorization, Cookie, and CSRF headers."""
        new_headers: list[Header] = []
        for h in headers:
            lower_name = h.name.lower()
            val = h.value

            if lower_name in {"authorization", "proxy-authorization"}:
                val = self._redact_auth_header(val, h.name)
            elif lower_name == "cookie":
                val = self._redact_cookie_header_value(val)
            elif lower_name == "set-cookie":
                val = self._redact_set_cookie_header_value(val)
            elif lower_name in CSRF_HEADER_NAMES:
                val = self.get_or_register(
                    value=val,
                    category=SecretCategory.CSRF,
                    location=SecretLocation.HEADER,
                    key_name=h.name,
                )

            new_headers.append(h.model_copy(update={"value": val}))
        return new_headers

    def redact_cookies(self, cookies: list[Cookie]) -> list[Cookie]:
        """Redact cookie objects list."""
        new_cookies: list[Cookie] = []
        for c in cookies:
            placeholder = self.get_or_register(
                value=c.value,
                category=SecretCategory.COOKIE,
                location=SecretLocation.COOKIE,
                key_name=c.name,
            )
            new_cookies.append(c.model_copy(update={"value": placeholder}))
        return new_cookies

    def redact_query_params(
        self,
        query_params: list[QueryParam],
        url: str,
    ) -> tuple[list[QueryParam], str]:
        """Redact sensitive query string parameters and update request URL."""
        new_params: list[QueryParam] = []
        for q in query_params:
            val = q.value
            if q.name.lower() in API_KEY_PARAM_NAMES:
                val = self.get_or_register(
                    value=val,
                    category=SecretCategory.API_KEY,
                    location=SecretLocation.QUERY,
                    key_name=q.name,
                )
            new_params.append(q.model_copy(update={"value": val}))

        # Reconstruct URL query string if present
        try:
            parsed = urlparse(url)
            if parsed.query:
                qsl = parse_qsl(parsed.query, keep_blank_values=True)
                new_qsl: list[tuple[str, str]] = []
                for k, v in qsl:
                    if k.lower() in API_KEY_PARAM_NAMES:
                        v = self.get_or_register(
                            value=v,
                            category=SecretCategory.API_KEY,
                            location=SecretLocation.QUERY,
                            key_name=k,
                        )
                    new_qsl.append((k, v))
                new_query = urlencode(new_qsl)
                new_url = urlunparse(parsed._replace(query=new_query))
            else:
                new_url = url
        except Exception:
            new_url = url

        return new_params, new_url

    def _redact_json_tree(self, obj: Any) -> Any:
        """Recursively redact sensitive keys and JWT values in parsed JSON."""
        if isinstance(obj, dict):
            new_dict: dict[str, Any] = {}
            for k, v in obj.items():
                if isinstance(v, str):
                    if SENSITIVE_FIELD_REGEX.match(k):
                        cat = (
                            SecretCategory.TOKEN
                            if "token" in k.lower()
                            else SecretCategory.SECRET
                        )
                        new_dict[k] = self.get_or_register(
                            value=v,
                            category=cat,
                            location=SecretLocation.BODY,
                            key_name=k,
                        )
                    else:
                        # Scan string value for embedded JWTs
                        new_dict[k] = self._redact_embedded_jwts(v)
                else:
                    new_dict[k] = self._redact_json_tree(v)
            return new_dict
        if isinstance(obj, list):
            return [self._redact_json_tree(item) for item in obj]
        if isinstance(obj, str):
            return self._redact_embedded_jwts(obj)
        return obj

    def _redact_embedded_jwts(self, text: str) -> str:
        """Find and replace any JWT-shaped strings in text."""
        def _replace_jwt(match: re.Match[str]) -> str:
            raw_jwt = match.group(0)
            return self.get_or_register(
                value=raw_jwt,
                category=SecretCategory.TOKEN,
                location=SecretLocation.JWT,
                key_name="jwt_body",
            )

        return JWT_PATTERN.sub(_replace_jwt, text)

    def redact_body_text(self, text: str | None) -> str | None:
        """Sanitize JSON or text payloads, replacing sensitive fields and JWTs."""
        if not text:
            return text

        trimmed = text.strip()
        # Attempt JSON parsing
        if (trimmed.startswith("{") and trimmed.endswith("}")) or (
            trimmed.startswith("[") and trimmed.endswith("]")
        ):
            try:
                parsed = json.loads(trimmed)
                redacted_json = self._redact_json_tree(parsed)
                return json.dumps(redacted_json)
            except Exception:
                pass

        # Fallback: scan for embedded JWTs
        return self._redact_embedded_jwts(text)

    def _final_sweep_entry(self, entry: Entry) -> Entry:
        """Ensure no known original secret survives in any field of the entry."""
        if not self._secret_to_placeholder:
            return entry

        # Sort secrets by length descending to prevent sub-string collision
        sorted_secrets = sorted(
            [s for s in self._secret_to_placeholder.keys() if len(s) >= 4],
            key=len,
            reverse=True,
        )

        def _clean_str(text: str | None) -> str | None:
            if not text:
                return text
            cleaned = text
            for sec in sorted_secrets:
                if sec in cleaned:
                    cleaned = cleaned.replace(sec, self._secret_to_placeholder[sec])
            return cleaned

        # Clean Request
        req = entry.request
        new_url = _clean_str(req.url) or req.url
        new_headers = [
            h.model_copy(update={"value": _clean_str(h.value) or ""})
            for h in req.headers
        ]
        new_cookies = [
            c.model_copy(update={"value": _clean_str(c.value) or ""})
            for c in req.cookies
        ]
        new_query = [
            q.model_copy(update={"value": _clean_str(q.value) or ""})
            for q in req.query_string
        ]
        new_post_data = None
        if req.post_data:
            new_text = _clean_str(req.post_data.text)
            new_params = [
                p.model_copy(update={"value": _clean_str(p.value)})
                for p in req.post_data.params
            ]
            new_post_data = req.post_data.model_copy(
                update={"text": new_text, "params": new_params}
            )

        new_req = req.model_copy(
            update={
                "url": new_url,
                "headers": new_headers,
                "cookies": new_cookies,
                "query_string": new_query,
                "post_data": new_post_data,
            }
        )

        # Clean Response
        res = entry.response
        new_res_headers = [
            h.model_copy(update={"value": _clean_str(h.value) or ""})
            for h in res.headers
        ]
        new_res_cookies = [
            c.model_copy(update={"value": _clean_str(c.value) or ""})
            for c in res.cookies
        ]
        new_res_content = res.content
        if res.content.text:
            new_res_text = _clean_str(res.content.text)
            new_res_content = res.content.model_copy(update={"text": new_res_text})

        new_res = res.model_copy(
            update={
                "headers": new_res_headers,
                "cookies": new_res_cookies,
                "content": new_res_content,
            }
        )

        return entry.model_copy(update={"request": new_req, "response": new_res})

    def redact_entry(self, entry: Entry) -> Entry:
        """Sanitize a single Entry object."""
        req = entry.request
        res = entry.response

        # 1. Redact Request components
        new_headers = self.redact_headers(req.headers)
        new_cookies = self.redact_cookies(req.cookies)
        new_query, new_url = self.redact_query_params(req.query_string, req.url)

        new_post_data: PostData | None = None
        if req.post_data:
            redacted_text = self.redact_body_text(req.post_data.text)
            new_params: list[PostDataParam] = []
            for p in req.post_data.params:
                pval = p.value
                if pval and SENSITIVE_FIELD_REGEX.match(p.name):
                    pval = self.get_or_register(
                        value=pval,
                        category=SecretCategory.SECRET,
                        location=SecretLocation.BODY,
                        key_name=p.name,
                    )
                new_params.append(p.model_copy(update={"value": pval}))
            new_post_data = req.post_data.model_copy(
                update={"text": redacted_text, "params": new_params}
            )

        sanitized_req = req.model_copy(
            update={
                "url": new_url,
                "headers": new_headers,
                "cookies": new_cookies,
                "query_string": new_query,
                "post_data": new_post_data,
            }
        )

        # 2. Redact Response components
        new_res_headers = self.redact_headers(res.headers)
        new_res_cookies = self.redact_cookies(res.cookies)
        new_res_text = self.redact_body_text(res.content.text)
        new_res_content = res.content.model_copy(update={"text": new_res_text})

        sanitized_res = res.model_copy(
            update={
                "headers": new_res_headers,
                "cookies": new_res_cookies,
                "content": new_res_content,
            }
        )

        return entry.model_copy(
            update={"request": sanitized_req, "response": sanitized_res}
        )

    def process(self, entries: list[Entry]) -> RedactionResult:
        """Sanitize all entries and execute secondary comprehensive scrub.

        Args:
            entries: List of API entries.

        Returns:
            RedactionResult containing sanitized entries and secret mapping.
        """
        # Pass 1: Targeted redaction
        first_pass = [self.redact_entry(e) for e in entries]

        # Pass 2: Comprehensive final scrub against all discovered secrets
        final_entries = [self._final_sweep_entry(e) for e in first_pass]

        return RedactionResult(
            entries=final_entries,
            placeholder_map=dict(self._placeholder_to_secret),
            secrets=list(self._metadata),
        )


def redact(entries: list[Entry]) -> RedactionResult:
    """Convenience function to redact secrets from a list of entries.

    Args:
        entries: Candidate API entries.

    Returns:
        Sanitized RedactionResult ready for downstream AST and LLM processing.
    """
    engine = RedactionEngine()
    return engine.process(entries)
