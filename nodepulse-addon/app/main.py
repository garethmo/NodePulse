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
from pathlib import Path

from aiohttp import web
import aiohttp_cors

from .config import load_config, resolve_target
from .connection import MeshtasticConnection
from .routes import (
    handle_channels,
    handle_clear_stale_nodes,
    handle_messages,
    handle_nodes,
    handle_position_history,
    handle_request_position,
    handle_send,
    handle_set_tags,
    handle_status,
    handle_tags,
    handle_traceroute,
    handle_track_node,
    handle_tracked_nodes,
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

    IMPORTANT: This handler must NOT block on the Meshtastic connection. In
    aiohttp, on_startup handlers run *before* the server binds its listening
    socket, so `await conn.connect()` would delay the web server (and HA
    ingress / health checks) until the TCP connect succeeds or times out. A
    slow or unreachable Meshtastic host would then make the addon look dead.

    Connection (and reconnection) is handled entirely by the background
    `monitor_connection` task, which connects in a worker thread without
    blocking the event loop or the server's startup.
    """
    conn: MeshtasticConnection = app["connection"]

    logger.debug("NodePulse addon starting up")

    # Launch the background health monitor. We store the Task reference on
    # the app so we can cancel it cleanly on shutdown.
    app["monitor_task"] = asyncio.create_task(conn.monitor_connection())

    # Launch the periodic channel-refresh task so the UI's channel list stays
    # in sync with the radio without waiting for a config push.
    app["channel_refresh_task"] = asyncio.create_task(conn.run_channel_refresh_loop())


async def _on_shutdown(app: web.Application) -> None:
    """
    Called by aiohttp during graceful shutdown (SIGTERM / SIGINT).

    Cancel the background monitor first so it doesn't attempt a reconnect
    while we are in the middle of closing the interface.
    """
    logger.debug("NodePulse addon shutting down")

    monitor_task: asyncio.Task = app.get("monitor_task")
    if monitor_task and not monitor_task.done():
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass  # expected on cancel

    channel_refresh_task: asyncio.Task = app.get("channel_refresh_task")
    if channel_refresh_task and not channel_refresh_task.done():
        channel_refresh_task.cancel()
        try:
            await channel_refresh_task
        except asyncio.CancelledError:
            pass  # expected on cancel

    conn: MeshtasticConnection = app["connection"]
    await conn.disconnect()
    logger.debug("NodePulse addon shutdown complete")


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
    target_host, target_port, mode = resolve_target(config)
    logger.debug(
        "NodePulse connection mode=%s, target=%s:%s",
        mode, target_host, target_port,
    )
    app["connection"] = MeshtasticConnection(
        host=target_host,
        port=target_port,
        mode=mode,
        access_key=config.access_key,
    )
    app["ignored_nodes"] = set(config.ignored_nodes)
    app["config"] = config

    # Register lifecycle hooks
    app.on_startup.append(_on_startup)
    app.on_shutdown.append(_on_shutdown)

    # --- REST API Routes ---
    app.router.add_get("/api/status", handle_status)
    app.router.add_get("/api/nodes", handle_nodes)
    app.router.add_post("/api/nodes/clear-stale", handle_clear_stale_nodes)
    app.router.add_get("/api/channels", handle_channels)
    app.router.add_get("/api/messages", handle_messages)
    app.router.add_post("/api/send", handle_send)
    app.router.add_post("/api/traceRoute", handle_traceroute)
    app.router.add_post("/api/requestPosition", handle_request_position)
    app.router.add_get("/api/tags", handle_tags)
    app.router.add_put("/api/tags", handle_set_tags)
    app.router.add_get("/api/position-history", handle_position_history)
    app.router.add_get("/api/position-history/{node_id}", handle_position_history)
    app.router.add_get("/api/tracked-nodes", handle_tracked_nodes)
    app.router.add_post("/api/track-node", handle_track_node)

    # --- Static Web UI ---
    # Serve the dashboard from the root path. Under HA Ingress the addon is
    # reached at https://<ha>/api/hassio_ingress/<token>/, so redirecting to an
    # absolute /ui/index.html would escape the ingress prefix and 404. Serving
    # at "/" with the HTML's relative asset paths keeps everything inside the
    # ingress path.
    if _STATIC_DIR.is_dir():
        app.router.add_get("/", _serve_index)
        app.router.add_static("/", path=str(_STATIC_DIR), name="ui")
    else:
        logger.warning("Web UI directory not found — UI disabled (path=%s)", _STATIC_DIR)

    # --- CORS ---
    # Allow the HA frontend (running on a different port/origin during dev)
    # to call the API. In production, Ingress handles this, but permissive
    # CORS simplifies local development.
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=False,
            expose_headers="*",
            allow_headers="*",
            allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        )
    })
    for route in list(app.router.routes()):
        # Only apply CORS to the API routes (not static file serving).
        if route.resource and route.resource.canonical.startswith("/api"):
            cors.add(route)

    return app


async def _serve_index(request: web.Request) -> web.Response:
    """Serve the Web UI index page at the root path."""
    return web.FileResponse(str(_STATIC_DIR / "index.html"))


def main() -> None:
    """CLI entry point — loads config and runs the server."""
    config = load_config()
    
    # Update log level based on config
    numeric_level = getattr(logging, config.log_level, logging.INFO)
    logging.getLogger().setLevel(numeric_level)
    
    app = build_app(config)

    # HA Supervisor expects the addon to listen on port 8099 (matching ingress_port
    # in config.json). We bind to 0.0.0.0 so HA Ingress can reach us inside Docker.
    web.run_app(app, host="0.0.0.0", port=8099)


if __name__ == "__main__":
    main()
