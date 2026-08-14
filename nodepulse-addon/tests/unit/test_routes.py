"""
Unit tests for app/routes.py
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
import json
import os
import sys

# Add app to path so we can import it
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "app")))

import app.routes as routes

_validate_destination = routes._validate_destination
_json_response = routes._json_response
_error_response = routes._error_response
_apply_access_key = routes._apply_access_key
handle_status = routes.handle_status
handle_nodes = routes.handle_nodes
handle_clear_stale_nodes = routes.handle_clear_stale_nodes
handle_delete_node = routes.handle_delete_node
handle_search_messages = routes.handle_search_messages


def test_validate_destination_valid():
    """Test validation of valid destination node ID."""
    body = {"destination": "!12345678"}
    result = _validate_destination(body)
    assert result == "!12345678"


def test_validate_destination_invalid_format():
    """Test validation of invalid destination format."""
    body = {"destination": "invalid"}
    result = _validate_destination(body)
    assert result is None


def test_validate_destination_missing():
    """Test validation when destination is missing."""
    body = {}
    result = _validate_destination(body)
    assert result is None


def test_validate_destination_empty():
    """Test validation when destination is empty string."""
    body = {"destination": ""}
    result = _validate_destination(body)
    assert result is None


def test_validate_destination_whitespace():
    """Test validation when destination is whitespace."""
    body = {"destination": "  "}
    result = _validate_destination(body)
    assert result is None


def test_json_response():
    """Test JSON response helper."""
    data = {"key": "value"}
    response = _json_response(data)
    assert response.status == 200
    assert response.content_type == "application/json"
    assert json.loads(response.text) == data


def test_json_response_with_status():
    """Test JSON response helper with custom status."""
    data = {"error": "not found"}
    response = _json_response(data, status=404)
    assert response.status == 404
    assert json.loads(response.text) == data


def test_error_response():
    """Test error response helper."""
    response = _error_response("test error")
    assert response.status == 500
    assert json.loads(response.text) == {"error": "test error"}


def test_error_response_with_status():
    """Test error response helper with custom status."""
    response = _error_response("not found", status=404)
    assert response.status == 404
    assert json.loads(response.text) == {"error": "not found"}


def test_apply_access_key_with_key():
    """Test applying access key from request header."""
    mock_connection = MagicMock()
    mock_connection.set_access_key = MagicMock()
    
    request = MagicMock()
    request.headers = {'X-NodePulse-Access-Key': 'test_key'}
    request.app = {'connection': mock_connection}
    
    _apply_access_key(request)
    
    mock_connection.set_access_key.assert_called_once_with('test_key')


def test_apply_access_key_without_key():
    """Test applying access key when not present in header."""
    mock_connection = MagicMock()
    mock_connection.set_access_key = MagicMock()
    
    request = MagicMock()
    request.headers = {}
    request.app = {'connection': mock_connection}
    
    _apply_access_key(request)
    
    mock_connection.set_access_key.assert_not_called()
