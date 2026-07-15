"""
NodePulse Addon — REST API Route Handlers.

Each handler is a standalone coroutine that receives an aiohttp Request and
returns a Response. Handlers are kept thin: they validate input, delegate
to the MeshtasticConnection, and format the response. No business logic lives
here — that belongs in connection.py.

All responses use JSON. Error responses always include a human-readable
"error" key so clients can display a meaningful message.
"""
import json
import logging
from typing import Any, Dict

from aiohttp import web

from .connection import MeshtasticConnection

logger = logging.getLogger(__name__)


def _json_response(data: Any, status: int = 200) -> web.Response:
    """Helper that serialises to JSON with consistent content-type."""
    return web.Response(
        text=json.dumps(data, default=str),
        content_type="application/json",
        status=status,
    )


def _error_response(message: str, status: int = 500) -> web.Response:
    return _json_response({"error": message}, status=status)


# ---------------------------------------------------------------------------
# Route: GET /api/status
# ---------------------------------------------------------------------------

async def handle_status(request: web.Request) -> web.Response:
    """
    Return the current connection state and basic node identity.

    This is the primary health-check endpoint polled by the HA custom
    integration's DataUpdateCoordinator to determine binary sensor state.
    """
    conn: MeshtasticConnection = request.app["connection"]
    try:
        status = await conn.get_status()
        return _json_response(status)
    except Exception as exc:
        logger.error({"error": str(exc)}, "Error fetching status")
        return _error_response("Failed to retrieve status")


# ---------------------------------------------------------------------------
# Route: GET /api/nodes
# ---------------------------------------------------------------------------

async def handle_nodes(request: web.Request) -> web.Response:
    """
    Return the full node list, optionally filtered by the ignored_nodes config.

    Nodes in the ignored_nodes list are excluded from the response entirely
    rather than being marked inactive, keeping the API surface clean for the
    HA integration and the Web UI.
    """
    conn: MeshtasticConnection = request.app["connection"]
    ignored: set = request.app["ignored_nodes"]

    try:
        nodes = await conn.get_nodes()
        # Filter out nodes the user has asked to ignore by their hex ID.
        visible_nodes = [n for n in nodes if n.get("id") not in ignored]
        return _json_response(visible_nodes)
    except Exception as exc:
        logger.error({"error": str(exc)}, "Error fetching nodes")
        return _error_response("Failed to retrieve nodes")


# ---------------------------------------------------------------------------
# Route: GET /api/channels
# ---------------------------------------------------------------------------

async def handle_channels(request: web.Request) -> web.Response:
    """Return the channel list configured on the connected Meshtastic node."""
    conn: MeshtasticConnection = request.app["connection"]
    try:
        channels = await conn.get_channels()
        return _json_response(channels)
    except Exception as exc:
        logger.error({"error": str(exc)}, "Error fetching channels")
        return _error_response("Failed to retrieve channels")


# ---------------------------------------------------------------------------
# Route: POST /api/send
# ---------------------------------------------------------------------------

async def handle_send(request: web.Request) -> web.Response:
    """
    Send a text message over the mesh.

    Expected JSON body:
        {
            "text": "Hello mesh!",
            "destination": "!abcd1234",  // optional — omit for broadcast
            "channel": 0                 // optional — defaults to 0
        }

    The meshtastic library handles PKI encryption automatically for
    direct messages when a channel key is in place. We intentionally do
    NOT re-implement encryption here; the library owns that responsibility.
    """
    conn: MeshtasticConnection = request.app["connection"]

    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        return _error_response("Request body must be valid JSON", status=400)

    text = body.get("text", "").strip()
    if not text:
        return _error_response("'text' field is required and must not be empty", status=400)

    destination = body.get("destination")  # None → broadcast
    channel = int(body.get("channel", 0))

    try:
        success = await conn.send_message(text, destination=destination, channel=channel)
        if success:
            return _json_response({"sent": True})
        return _error_response("Message was not accepted by the Meshtastic interface", status=502)
    except Exception as exc:
        logger.error(
            {"destination": destination, "error": str(exc)}, "Unhandled error in send handler"
        )
        return _error_response("Failed to send message")


# ---------------------------------------------------------------------------
# Route: POST /api/traceRoute
# ---------------------------------------------------------------------------

async def handle_traceroute(request: web.Request) -> web.Response:
    """
    Initiate a traceroute towards a destination node.

    Expected JSON body:
        { "destination": "!abcd1234" }

    Traceroute results arrive asynchronously via the Meshtastic event system
    and are NOT returned in this HTTP response. The response only confirms
    that the traceroute packet was dispatched. The Web UI polls /api/nodes
    to see hop counts updated after a traceroute completes.
    """
    conn: MeshtasticConnection = request.app["connection"]

    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        return _error_response("Request body must be valid JSON", status=400)

    destination = body.get("destination", "").strip()
    if not destination:
        return _error_response("'destination' field is required", status=400)

    try:
        success = await conn.request_traceroute(destination)
        return _json_response({"dispatched": success})
    except Exception as exc:
        logger.error(
            {"destination": destination, "error": str(exc)}, "Traceroute dispatch failed"
        )
        return _error_response("Failed to dispatch traceroute")


# ---------------------------------------------------------------------------
# Route: POST /api/requestPosition
# ---------------------------------------------------------------------------

async def handle_request_position(request: web.Request) -> web.Response:
    """
    Ask a specific node to send its current GPS position.

    Expected JSON body:
        { "destination": "!abcd1234" }
    """
    conn: MeshtasticConnection = request.app["connection"]

    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        return _error_response("Request body must be valid JSON", status=400)

    destination = body.get("destination", "").strip()
    if not destination:
        return _error_response("'destination' field is required", status=400)

    try:
        success = await conn.request_position(destination)
        return _json_response({"dispatched": success})
    except Exception as exc:
        logger.error(
            {"destination": destination, "error": str(exc)}, "Position request dispatch failed"
        )
        return _error_response("Failed to dispatch position request")
