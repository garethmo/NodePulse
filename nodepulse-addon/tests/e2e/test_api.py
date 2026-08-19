import pytest
from unittest.mock import AsyncMock

pytestmark = pytest.mark.asyncio

async def test_get_status(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get("/api/status")
    assert resp.status == 200
    data = await resp.json()
    assert data["connected"] is True
    assert "uptime" in data

async def test_get_nodes(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get("/api/nodes")
    assert resp.status == 200
    data = await resp.json()
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["id"] == "!12345678"

async def test_get_channels(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get("/api/channels")
    assert resp.status == 200
    data = await resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["name"] == "LongFast"

async def test_get_messages(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get("/api/messages")
    assert resp.status == 200
    data = await resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["text"] == "Hello world"

async def test_send_message(aiohttp_client, app):
    client = await aiohttp_client(app)
    payload = {
        "text": "Hello Mesh",
        "destination": None,
        "channel": 0
    }
    resp = await client.post("/api/send", json=payload)
    assert resp.status == 200
    data = await resp.json()
    assert data.get("sent") is True
    
    # Verify the mock was called
    conn = app["connection"]
    conn.send_message.assert_called_once_with(
        "Hello Mesh",
        destination=None,
        channel=0
    )

async def test_send_message_with_destination(aiohttp_client, app):
    client = await aiohttp_client(app)
    payload = {
        "text": "Direct message",
        "destination": "!12345678",
        "channel": 0
    }
    resp = await client.post("/api/send", json=payload)
    assert resp.status == 200
    data = await resp.json()
    assert data.get("sent") is True

async def test_send_message_invalid_channel(aiohttp_client, app):
    client = await aiohttp_client(app)
    payload = {
        "text": "Hello",
        "channel": 99  # Invalid channel
    }
    resp = await client.post("/api/send", json=payload)
    assert resp.status == 400

async def test_send_message_missing_text(aiohttp_client, app):
    client = await aiohttp_client(app)
    payload = {
        "channel": 0
    }
    resp = await client.post("/api/send", json=payload)
    assert resp.status == 400

async def test_get_position_history(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get("/api/position-history")
    assert resp.status == 200
    data = await resp.json()
    assert isinstance(data, dict) or isinstance(data, list)

async def test_get_position_history_for_node(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get("/api/position-history/!12345678")
    assert resp.status == 200
    data = await resp.json()
    assert isinstance(data, list)

async def test_get_waypoints(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get("/api/waypoints")
    assert resp.status == 200
    data = await resp.json()
    assert isinstance(data, list)

async def test_add_waypoint(aiohttp_client, app):
    client = await aiohttp_client(app)
    payload = {
        "name": "Test Waypoint",
        "lat": 40.7128,
        "lng": -74.0060,
        "description": "Test description"
    }
    resp = await client.post("/api/waypoints", json=payload)
    assert resp.status == 200
    data = await resp.json()
    assert "name" in data

async def test_get_tags(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get("/api/tags")
    assert resp.status == 200
    data = await resp.json()
    assert isinstance(data, dict)

async def test_set_tags(aiohttp_client, app):
    client = await aiohttp_client(app)
    payload = {
        "node_id": "!12345678",
        "tags": ["gateway", "roof"]
    }
    resp = await client.put("/api/tags", json=payload)
    assert resp.status == 200
    data = await resp.json()
    assert isinstance(data, dict)

async def test_set_tags_invalid_node_id(aiohttp_client, app):
    client = await aiohttp_client(app)
    payload = {
        "node_id": "invalid",
        "tags": ["test"]
    }
    resp = await client.put("/api/tags", json=payload)
    assert resp.status == 400

async def test_traceroute(aiohttp_client, app):
    client = await aiohttp_client(app)
    payload = {
        "destination": "!12345678"
    }
    resp = await client.post("/api/traceRoute", json=payload)
    assert resp.status == 200
    data = await resp.json()
    assert "dispatched" in data

async def test_traceroute_invalid_destination(aiohttp_client, app):
    client = await aiohttp_client(app)
    payload = {
        "destination": "invalid"
    }
    resp = await client.post("/api/traceRoute", json=payload)
    assert resp.status == 400

async def test_request_position(aiohttp_client, app):
    client = await aiohttp_client(app)
    payload = {
        "destination": "!12345678"
    }
    resp = await client.post("/api/requestPosition", json=payload)
    assert resp.status == 200
    data = await resp.json()
    assert "dispatched" in data

async def test_clear_stale_nodes(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.post("/api/nodes/clear-stale")
    assert resp.status == 200
    data = await resp.json()
    assert "removed" in data

async def test_delete_node(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.delete("/api/node/!12345678")
    assert resp.status == 200
    data = await resp.json()
    assert data.get("deleted") == "!12345678"

async def test_delete_node_not_found(aiohttp_client, app):
    client = await aiohttp_client(app)
    # First mock delete_node to return False
    app["connection"].delete_node = AsyncMock(return_value=False)
    resp = await client.delete("/api/node/!99999999")
    assert resp.status == 404
