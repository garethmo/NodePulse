import pytest

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
