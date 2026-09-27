"""Jinja render helper compatible with Starlette 1.7+ TemplateResponse API."""
from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse


def render(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    templates = request.app.state.templates if hasattr(request.app.state, "templates") else None
    if templates is None:
        templates = getattr(request.state, "templates", None)
    if templates is None:
        raise RuntimeError("Jinja2Templates not attached to request")
    ctx = dict(context or {})
    # request is passed as first arg; do not put in context for Starlette 1.7
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)
