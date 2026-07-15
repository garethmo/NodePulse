"""
NodePulse Addon — Application Entry Point.

This module wires together the configuration, Meshtastic connection, REST API
routes, and static Web UI file serving into a single aiohttp application.

Startup sequence:
  1. Load config from /data/options.json.
  2. Create the MeshtasticConnection and attempt the initial connect.
  3. Launch the connection health monitor as a background Task.
  4. Start the aiohttp web server.

Shutdown sequence (on SIGTERM from HA Supervisor):
  1. aiohttp calls the on_shutdown signal handlers.
  2. We cancel the monitor Task and cleanly close the Meshtastic interface.
"""
import asyncio
import logging
import os
import signal
from pathlib import Path

from aiohttp import web
import aiohttp_cors

from .config import load_config
from .connection import MeshtasticConnection
from .routes import (
    handle_channels,
    handle_nodes,
    handle_request_position,
    handle_send,
    handle_status,
    handle_traceroute,
)

# Configure structured logging early so all subsequent imports can log.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Path to the Web UI static files bundled inside the Docker image.
_STATIC_DIR = Path(__file__).parent.parent / "web_ui"


async def _on_startup(app: web.Application) -> None:
    """
    Called by aiohttp once before the server starts accepting requests.

    We connect to Meshtastic here (rather than at module import time) because:
    - The event loop is running, so asyncio.to_thread() works.
    - Any startup failure is surfaced via aiohttp's lifecycle rather than
      crashing the process before it even binds to a port.
    """
    conn: MeshtasticConnection = app["connection"]

    logger.info({}, "NodePulse addon starting up")
    try:
        await conn.connect()
    except Exception as exc:
        # Log and continue — the monitor task will keep retrying.
        logger.error({"error": str(exc)}, "Initial Meshtastic connection failed — will retry")

    # Launch the background health monitor. We store the Task reference on
    # the app so we can cancel it cleanly on shutdown.
    app["monitor_task"] = asyncio.create_task(conn.monitor_connection())


async def _on_shutdown(app: web.Application) -> None:
    """
    Called by aiohttp during graceful shutdown (SIGTERM / SIGINT).

    Cancel the background monitor first so it doesn't attempt a reconnect
    while we are in the middle of closing the interface.
    """
    logger.info({}, "NodePulse addon shutting down")

    monitor_task: asyncio.Task = app.get("monitor_task")
    if monitor_task and not monitor_task.done():
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass  # expected on cancel

    conn: MeshtasticConnection = app["connection"]
    await conn.disconnect()
    logger.info({}, "NodePulse addon shutdown complete")


def build_app(config) -> web.Application:
    """
    Construct and configure the aiohttp Application.

    Separating construction from running makes this testable without
    actually binding to a port.
    """
    app = web.Application()

    # Attach shared state to the app so all route handlers can access it
    # without global variables. aiohttp's Application dict is the idiomatic
    # way to share dependencies in aiohttp.
    app["connection"] = MeshtasticConnection(
        host=config.meshtastic_host,
        port=config.meshtastic_port,
    )
    app["ignored_nodes"] = set(config.ignored_nodes)
    app["config"] = config

    # Register lifecycle hooks
    app.on_startup.append(_on_startup)
    app.on_shutdown.append(_on_shutdown)

    # --- REST API Routes ---
    app.router.add_get("/api/status", handle_status)
    app.router.add_get("/api/nodes", handle_nodes)
    app.router.add_get("/api/channels", handle_channels)
    app.router.add_post("/api/send", handle_send)
    app.router.add_post("/api/traceRoute", handle_traceroute)
    app.router.add_post("/api/requestPosition", handle_request_position)

    # --- Static Web UI ---
    # Serve the bundled HTML/CSS/JS dashboard under /ui/.
    # HA Ingress will proxy the root to this path, so we also redirect / → /ui/.
    if _STATIC_DIR.is_dir():
        app.router.add_static("/ui/", path=str(_STATIC_DIR), name="ui")
        app.router.add_get("/", _redirect_to_ui)
    else:
        logger.warning({"path": str(_STATIC_DIR)}, "Web UI directory not found — UI disabled")

    # --- CORS ---
    # Allow the HA frontend (running on a different port/origin during dev)
    # to call the API. In production, Ingress handles this, but permissive
    # CORS simplifies local development.
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=False,
            expose_headers="*",
            allow_headers="*",
            allow_methods=["GET", "POST", "OPTIONS"],
        )
    })
    for route in list(app.router.routes()):
        # Only apply CORS to the API routes (not static file serving).
        if route.resource and route.resource.canonical.startswith("/api"):
            cors.add(route)

    return app


async def _redirect_to_ui(request: web.Request) -> web.Response:
    """Redirect bare root requests to the Web UI index page."""
    raise web.HTTPFound("/ui/index.html")


def main() -> None:
    """CLI entry point — loads config and runs the server."""
    config = load_config()
    app = build_app(config)

    # HA Supervisor expects the addon to listen on port 8099 (matching ingress_port
    # in config.json). We bind to 0.0.0.0 so HA Ingress can reach us inside Docker.
    web.run_app(app, host="0.0.0.0", port=8099)


if __name__ == "__main__":
    main()
