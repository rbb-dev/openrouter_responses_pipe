"""Error templates and rendering for OpenRouter Responses Pipe.

This module defines user-customizable error message templates with Handlebars-style
conditionals and placeholder substitution. All error types (400 API errors, network
timeouts, connection failures, 5xx service errors, and internal exceptions) are
rendered through these templates.

Layer: core (no dependencies on domain or adapters)
"""

from __future__ import annotations

import re
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Template Regex Patterns
# ─────────────────────────────────────────────────────────────────────────────

_TEMPLATE_VAR_PATTERN = re.compile(r"{(\w+)}")
_TEMPLATE_IF_OPEN_RE = re.compile(r"\{\{\s*#if\s+(\w+)\s*\}\}")
_TEMPLATE_IF_CLOSE_RE = re.compile(r"\{\{\s*/if\s*\}\}")


# ─────────────────────────────────────────────────────────────────────────────
# Default Error Templates
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_OPENROUTER_ERROR_TEMPLATE = (
    "{{#if heading}}\n"
    "### 🚫 {heading} could not process your request.\n\n"
    "{{/if}}\n"
    "{{#if sanitized_detail}}\n"
    "### Error: `{sanitized_detail}`\n\n"
    "{{/if}}\n"
    "{{#if model_identifier}}\n"
    "- **Model**: `{model_identifier}`\n"
    "{{/if}}\n"
    "{{#if provider}}\n"
    "- **Provider**: `{provider}`\n"
    "{{/if}}\n"
    "{{#if requested_model}}\n"
    "- **Requested model**: `{requested_model}`\n"
    "{{/if}}\n"
    "{{#if api_model_id}}\n"
    "- **API model id**: `{api_model_id}`\n"
    "{{/if}}\n"
    "{{#if normalized_model_id}}\n"
    "- **Normalized model id**: `{normalized_model_id}`\n"
    "{{/if}}\n"
    "{{#if openrouter_code}}\n"
    "- **OpenRouter code**: `{openrouter_code}`\n"
    "{{/if}}\n"
    "{{#if upstream_type}}\n"
    "- **Provider error**: `{upstream_type}`\n"
    "{{/if}}\n"
    "{{#if reason}}\n"
    "- **Reason**: `{reason}`\n"
    "{{/if}}\n"
    "{{#if request_id}}\n"
    "- **Request ID**: `{request_id}`\n"
    "{{/if}}\n"
    "{{#if include_model_limits}}\n"
    "\n**Model limits:**\n"
    "Context window: {context_limit_tokens} tokens\n"
    "Max output tokens: {max_output_tokens} tokens\n"
    "Adjust your prompt or requested output to stay within these limits.\n"
    "{{/if}}\n"
    "{{#if moderation_reasons}}\n"
    "\n**Moderation reasons:**\n"
    "{moderation_reasons}\n"
    "Please review the flagged content or contact your administrator if you believe this is a mistake.\n"
    "{{/if}}\n"
    "{{#if flagged_excerpt}}\n"
    "\n**Flagged text excerpt:**\n"
    "```\n{flagged_excerpt}\n```\n"
    "Provide this excerpt when following up with your administrator.\n"
    "{{/if}}\n"
    "{{#if raw_body}}\n"
    "\n**Raw provider response:**\n"
    "```\n{raw_body}\n```\n"
    "{{/if}}\n\n"
    "Please adjust the request and try again, or ask your admin to enable the middle-out option.\n"
    "{{#if request_id_reference}}\n"
    "{request_id_reference}\n"
    "{{/if}}\n"
)

DEFAULT_NETWORK_TIMEOUT_TEMPLATE = (
    "### ⏱️ Request Timeout\n\n"
    "The request to OpenRouter took too long to complete.\n\n"
    "**Error ID:** `{error_id}`\n"
    "{{#if timeout_seconds}}\n"
    "**Timeout:** {timeout_seconds}s\n"
    "{{/if}}\n"
    "{{#if timestamp}}\n"
    "**Time:** {timestamp}\n"
    "{{/if}}\n\n"
    "**Possible causes:**\n"
    "- OpenRouter's servers are slow or overloaded\n"
    "- Network congestion\n"
    "- Large request taking longer than expected\n\n"
    "**What to do:**\n"
    "- Wait a few moments and try again\n"
    "- Try a smaller request if possible\n"
    "- Check [OpenRouter Status](https://status.openrouter.ai/)\n"
    "{{#if support_email}}\n"
    "- Contact support: {support_email}\n"
    "{{/if}}\n"
)

DEFAULT_CONNECTION_ERROR_TEMPLATE = (
    "### 🔌 Connection Failed\n\n"
    "Unable to reach OpenRouter's servers.\n\n"
    "**Error ID:** `{error_id}`\n"
    "{{#if error_type}}\n"
    "**Error type:** `{error_type}`\n"
    "{{/if}}\n"
    "{{#if timestamp}}\n"
    "**Time:** {timestamp}\n"
    "{{/if}}\n\n"
    "**Possible causes:**\n"
    "- Network connectivity issues\n"
    "- Firewall blocking HTTPS traffic\n"
    "- DNS resolution failure\n"
    "- OpenRouter service outage\n\n"
    "**What to do:**\n"
    "1. Check your internet connection\n"
    "2. Verify firewall allows HTTPS (port 443)\n"
    "3. Check [OpenRouter Status](https://status.openrouter.ai/)\n"
    "4. Contact your network administrator if the issue persists\n"
    "{{#if support_email}}\n"
    "\n**Support:** {support_email}\n"
    "{{/if}}\n"
)

DEFAULT_SERVICE_ERROR_TEMPLATE = (
    "### 🔴 OpenRouter Service Error\n\n"
    "OpenRouter's servers are experiencing issues.\n\n"
    "**Error ID:** `{error_id}`\n"
    "{{#if status_code}}\n"
    "**Status:** {status_code} {reason}\n"
    "{{/if}}\n"
    "{{#if timestamp}}\n"
    "**Time:** {timestamp}\n"
    "{{/if}}\n\n"
    "This is **not** a problem with your request. The issue is on OpenRouter's side.\n\n"
    "**What to do:**\n"
    "- Wait a few minutes and try again\n"
    "- Check [OpenRouter Status](https://status.openrouter.ai/) for updates\n"
    "- If the problem persists for more than 15 minutes, contact OpenRouter support\n"
    "{{#if support_email}}\n"
    "\n**Support:** {support_email}\n"
    "{{/if}}\n"
)

DEFAULT_INTERNAL_ERROR_TEMPLATE = (
    "### ⚠️ Unexpected Error\n\n"
    "Something unexpected went wrong while processing your request.\n\n"
    "**Error ID:** `{error_id}` — Share this with support\n"
    "{{#if error_type}}\n"
    "**Error type:** `{error_type}`\n"
    "{{/if}}\n"
    "{{#if timestamp}}\n"
    "**Time:** {timestamp}\n"
    "{{/if}}\n\n"
    "The error has been logged and will be investigated.\n\n"
    "**What to do:**\n"
    "- Try your request again\n"
    "- If the problem persists, contact support with the Error ID above\n"
    "{{#if support_email}}\n"
    "- Email: {support_email}\n"
    "{{/if}}\n"
    "{{#if support_url}}\n"
    "- Support: {support_url}\n"
    "{{/if}}\n"
)


# ─────────────────────────────────────────────────────────────────────────────
# Template Rendering
# ─────────────────────────────────────────────────────────────────────────────

def render_error_template(template: str, values: dict[str, Any]) -> str:
    """Render a user-supplied template, honoring {{#if}} conditionals.

    Supports Handlebars-style {{#if variable}} ... {{/if}} blocks and
    placeholder substitution with {variable}.

    Args:
        template: Template string with conditionals and placeholders
        values: Dictionary of placeholder values

    Returns:
        str: Rendered template with conditionals evaluated and placeholders replaced

    Example:
        >>> template = "{{#if name}}Hello {name}!{{/if}}"
        >>> render_error_template(template, {"name": "World"})
        'Hello World!'
        >>> render_error_template(template, {})
        ''
    """
    if not template:
        template = DEFAULT_OPENROUTER_ERROR_TEMPLATE

    rendered_lines: list[str] = []
    condition_stack: list[bool] = []

    def _conditions_active() -> bool:
        """Return True when the current {{#if}} stack has no falsy guards."""
        return all(condition_stack) if condition_stack else True

    for raw_line in template.splitlines():
        stripped = raw_line.strip()

        # Handle {{#if variable}}
        open_match = _TEMPLATE_IF_OPEN_RE.fullmatch(stripped)
        if open_match:
            key = open_match.group(1)
            condition_stack.append(bool(values.get(key)))
            continue

        # Handle {{/if}}
        if _TEMPLATE_IF_CLOSE_RE.fullmatch(stripped):
            if condition_stack:
                condition_stack.pop()
            continue

        # Skip lines inside false conditionals
        if not _conditions_active():
            continue

        # Replace placeholders with values
        line = raw_line
        for name, value in values.items():
            if f"{{{name}}}" in line:
                line = line.replace(f"{{{name}}}", str(value))
        rendered_lines.append(line)

    return "\n".join(rendered_lines).strip()


def build_error_values(
    *,
    error_id: str,
    heading: str = "",
    detail: str = "",
    model_identifier: str = "",
    provider: str = "",
    requested_model: str = "",
    api_model_id: str = "",
    normalized_model_id: str = "",
    openrouter_code: str = "",
    upstream_type: str = "",
    reason: str = "",
    request_id: str = "",
    support_email: str = "",
    support_url: str = "",
    **extra_values: Any
) -> dict[str, Any]:
    """Build a dictionary of error template values with defaults.

    Args:
        error_id: Unique error identifier for support correlation
        heading: Error heading/title
        detail: Detailed error description
        model_identifier: Model display name
        provider: Provider name (e.g., "OpenAI", "Anthropic")
        requested_model: Original model ID from user request
        api_model_id: Normalized API model ID
        normalized_model_id: Pipe-normalized model ID
        openrouter_code: OpenRouter error code
        upstream_type: Upstream provider error type
        reason: Human-readable error reason
        request_id: OpenRouter request ID for tracking
        support_email: Support contact email
        support_url: Support URL
        **extra_values: Additional custom values

    Returns:
        dict: Template values with sanitized detail and computed fields
    """
    # Sanitize detail for Markdown code blocks
    sanitized_detail = detail.replace("`", "\\`")

    values = {
        "error_id": error_id,
        "heading": heading,
        "detail": detail,
        "sanitized_detail": sanitized_detail,
        "model_identifier": model_identifier,
        "provider": provider,
        "requested_model": requested_model,
        "api_model_id": api_model_id,
        "normalized_model_id": normalized_model_id,
        "openrouter_code": openrouter_code,
        "upstream_type": upstream_type,
        "reason": reason,
        "request_id": request_id,
        "support_email": support_email,
        "support_url": support_url,
    }

    # Merge extra values
    values.update(extra_values)

    return values
