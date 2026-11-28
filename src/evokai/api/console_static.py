"""The admin console's static files at ``{base}/admin`` (a single-page app)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response

from evokai.core.config import Settings

HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; media-src 'self' blob:; worker-src 'self' blob:; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "microphone=(self), camera=()",
}


def console_dir(settings: Settings) -> Path:
    """Where the built console is.

    Returns:
        ``EVOKAI_ADMIN_DIST_DIR``, else ``admin/dist`` next to the source tree.
    """
    if settings.admin_dist_dir:
        return Path(settings.admin_dist_dir)
    return Path(__file__).resolve().parents[3] / "admin" / "dist"


def mount_console(app: FastAPI, settings: Settings) -> None:
    """Serve the console at ``{base}/admin/``: files by path, anything else its index."""
    base = settings.base_path.rstrip("/")
    root = console_dir(settings).resolve()

    def serve(path: str = "") -> Response:
        index = root / "index.html"
        if not index.is_file():
            return PlainTextResponse("Console not built.", status_code=404)
        target = (root / path).resolve()
        if path and target.is_file() and target.is_relative_to(root):
            cache = (
                "public, max-age=31536000, immutable" if "/assets/" in f"/{path}" else "no-cache"
            )
            return FileResponse(target, headers={**HEADERS, "Cache-Control": cache})
        return FileResponse(index, headers={**HEADERS, "Cache-Control": "no-cache"})

    def slash() -> Response:
        # The console's files are relative to its folder: /admin must become /admin/.
        return RedirectResponse(f"{base}/admin/", status_code=307)

    app.add_api_route(f"{base}/admin", slash, methods=["GET"], include_in_schema=False)
    # The deployment's address alone (and the API's base) opens the console: a person who types
    # the host name lands on the sign-in page instead of a 404.
    for entry in {"/", f"{base}", f"{base}/"} - {""}:
        app.add_api_route(entry, slash, methods=["GET"], include_in_schema=False)
    app.add_api_route(
        f"{base}/admin/{{path:path}}", serve, methods=["GET", "HEAD"], include_in_schema=False
    )
