"""Pydantic data models representing HTTP Archive (HAR 1.2) components."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Header(BaseModel):
    """HTTP header key-value pair."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str = Field(..., description="Header name.")
    value: str = Field(..., description="Header value.")
    comment: str | None = Field(default=None, description="Optional comment.")


class Cookie(BaseModel):
    """HTTP cookie definition."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str = Field(..., description="Cookie name.")
    value: str = Field(..., description="Cookie value.")
    path: str | None = Field(default=None, description="Cookie path attribute.")
    domain: str | None = Field(default=None, description="Cookie domain attribute.")
    expires: str | None = Field(default=None, description="Cookie expiration timestamp.")
    http_only: bool | None = Field(
        default=None,
        alias="httpOnly",
        description="Whether cookie is HTTPOnly.",
    )
    secure: bool | None = Field(
        default=None,
        description="Whether cookie requires secure connection.",
    )
    comment: str | None = Field(default=None, description="Optional comment.")


class QueryParam(BaseModel):
    """URL query string parameter."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str = Field(..., description="Parameter name.")
    value: str = Field(..., description="Parameter value.")
    comment: str | None = Field(default=None, description="Optional comment.")


class PostDataParam(BaseModel):
    """Single parameter inside multipart/form-data or form-urlencoded postData."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str = Field(..., description="Field name.")
    value: str | None = Field(default=None, description="Field text value.")
    file_name: str | None = Field(
        default=None,
        alias="fileName",
        description="Uploaded file name.",
    )
    content_type: str | None = Field(
        default=None,
        alias="contentType",
        description="Field content type.",
    )
    comment: str | None = Field(default=None, description="Optional comment.")


class PostData(BaseModel):
    """HTTP POST request body description."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    mime_type: str = Field(
        default="",
        alias="mimeType",
        description="MIME type of posted data.",
    )
    text: str | None = Field(
        default=None,
        description="Raw or parsed post payload string.",
    )
    params: list[PostDataParam] = Field(
        default_factory=list,
        description="List of posted form parameters.",
    )
    comment: str | None = Field(default=None, description="Optional comment.")


class Request(BaseModel):
    """Recorded HTTP request."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    method: str = Field(..., description="HTTP method (GET, POST, etc.).")
    url: str = Field(..., description="Absolute request URL.")
    http_version: str = Field(
        default="HTTP/1.1",
        alias="httpVersion",
        description="HTTP version string.",
    )
    cookies: list[Cookie] = Field(
        default_factory=list,
        description="Cookies sent with request.",
    )
    headers: list[Header] = Field(
        default_factory=list,
        description="HTTP request headers.",
    )
    query_string: list[QueryParam] = Field(
        default_factory=list,
        alias="queryString",
        description="Parsed query string parameters.",
    )
    post_data: PostData | None = Field(
        default=None,
        alias="postData",
        description="Request body content.",
    )
    headers_size: int = Field(
        default=-1,
        alias="headersSize",
        description="Total size of request headers.",
    )
    body_size: int = Field(
        default=-1,
        alias="bodySize",
        description="Total size of request body.",
    )
    comment: str | None = Field(default=None, description="Optional comment.")

    def get_header(self, name: str) -> str | None:
        """Case-insensitively retrieve header value."""
        target = name.lower()
        for h in self.headers:
            if h.name.lower() == target:
                return h.value
        return None

    def get_cookie(self, name: str) -> str | None:
        """Lookup cookie value by name."""
        for c in self.cookies:
            if c.name == name:
                return c.value
        return None

    def get_query_param(self, name: str) -> str | None:
        """Lookup query parameter value by name."""
        for q in self.query_string:
            if q.name == name:
                return q.value
        return None


class ResponseContent(BaseModel):
    """Response body content."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    size: int = Field(default=0, description="Length of returned content in bytes.")
    mime_type: str = Field(
        default="",
        alias="mimeType",
        description="MIME type of response body.",
    )
    text: str | None = Field(default=None, description="Response body text or base64.")
    encoding: str | None = Field(
        default=None,
        description="Encoding format, e.g. 'base64'.",
    )
    comment: str | None = Field(default=None, description="Optional comment.")


class Response(BaseModel):
    """Recorded HTTP response."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    status: int = Field(..., description="HTTP status code.")
    status_text: str = Field(
        default="",
        alias="statusText",
        description="HTTP status description.",
    )
    http_version: str = Field(
        default="HTTP/1.1",
        alias="httpVersion",
        description="HTTP protocol version.",
    )
    cookies: list[Cookie] = Field(
        default_factory=list,
        description="Cookies received from server.",
    )
    headers: list[Header] = Field(
        default_factory=list,
        description="HTTP response headers.",
    )
    content: ResponseContent = Field(
        default_factory=ResponseContent,
        description="Response payload details.",
    )
    redirect_url: str = Field(
        default="",
        alias="redirectURL",
        description="Redirection target URL from Location header.",
    )
    headers_size: int = Field(
        default=-1,
        alias="headersSize",
        description="Total size of response headers.",
    )
    body_size: int = Field(
        default=-1,
        alias="bodySize",
        description="Total size of response body.",
    )
    comment: str | None = Field(default=None, description="Optional comment.")

    def get_header(self, name: str) -> str | None:
        """Case-insensitively retrieve header value."""
        target = name.lower()
        for h in self.headers:
            if h.name.lower() == target:
                return h.value
        return None


class Entry(BaseModel):
    """Single HTTP transaction entry in a HAR recording."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    started_date_time: str = Field(
        default="",
        alias="startedDateTime",
        description="ISO 8601 start timestamp.",
    )
    time: float = Field(default=0.0, description="Total elapsed time in milliseconds.")
    request: Request = Field(..., description="Recorded HTTP request.")
    response: Response = Field(..., description="Recorded HTTP response.")
    cache: dict[str, Any] = Field(
        default_factory=dict,
        description="Cache status information.",
    )
    timings: dict[str, Any] = Field(
        default_factory=dict,
        description="Detailed timing breakdown.",
    )
    server_ip_address: str | None = Field(
        default=None,
        alias="serverIPAddress",
        description="Server IP.",
    )
    connection: str | None = Field(
        default=None,
        description="Unique TCP connection identifier.",
    )
    pageref: str | None = Field(default=None, description="Page reference ID.")
    resource_type: str | None = Field(
        default=None,
        alias="_resourceType",
        description="Browser DevTools resource type (e.g. xhr, fetch, image).",
    )


class HARPage(BaseModel):
    """Recorded browser page metadata."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    started_date_time: str = Field(
        default="",
        alias="startedDateTime",
        description="Page start time.",
    )
    id: str = Field(..., description="Unique page identifier.")
    title: str = Field(default="", description="Page title.")
    page_timings: dict[str, Any] = Field(
        default_factory=dict,
        alias="pageTimings",
        description="Page-level timing events.",
    )


class HARCreator(BaseModel):
    """HAR generator metadata."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str = Field(default="Unknown", description="Generator tool name.")
    version: str = Field(default="0.0.0", description="Generator tool version.")
    comment: str | None = Field(default=None, description="Optional comment.")


class HARLog(BaseModel):
    """Root log container within a HAR file."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    version: str = Field(default="1.2", description="HAR specification version.")
    creator: HARCreator = Field(
        default_factory=HARCreator,
        description="Creator tool details.",
    )
    pages: list[HARPage] = Field(
        default_factory=list,
        description="Recorded page entries.",
    )
    entries: list[Entry] = Field(
        default_factory=list,
        description="Recorded HTTP exchanges.",
    )
    comment: str | None = Field(default=None, description="Optional comment.")


class HARRoot(BaseModel):
    """Root wrapper for a HAR JSON document."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    log: HARLog = Field(..., description="HAR log root object.")
