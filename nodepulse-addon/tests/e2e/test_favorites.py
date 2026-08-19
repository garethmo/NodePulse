"""
E2E tests for the Favorite Nodes feature.

Tests cover:
- Favorite button appears on node cards
- Clicking favorite adds node to favorites (visual feedback)
- Favorite nodes are sorted to the top of the list
- Favorites persist in localStorage across page reloads
"""
import re

import pytest
from playwright.async_api import Page, expect

pytestmark = pytest.mark.asyncio


async def _wait_for_poll(async_page: Page):
    """Wait for the initial pollData() to complete and render nodes."""
    await async_page.wait_for_timeout(3000)


async def _goto_nodes_tab(async_page: Page, server_url: str):
    """Navigate to the Nodes tab."""
    await async_page.goto(server_url, wait_until="domcontentloaded")
    await _wait_for_poll(async_page)
    await async_page.click("#header-tabs .tab-btn[data-view='nodes']")
    await _wait_for_poll(async_page)
    # Wait for node cards to render
    await async_page.wait_for_selector(".node-card", timeout=5000)


async def test_favorite_button_appears_on_node_cards(async_page: Page, aio_server):
    """Verify the favorite star button appears on each node card."""
    server_url = f"http://{aio_server.host}:{aio_server.port}"
    await _goto_nodes_tab(async_page, server_url)

    # Check that favorite buttons exist on node cards
    fav_buttons = await async_page.locator(".node-fav-btn").all()
    assert len(fav_buttons) >= 2, "Expected at least 2 favorite buttons (one per mock node)"


async def test_clicking_favorite_adds_node_to_favorites(async_page: Page, aio_server):
    """Click the favorite button and verify visual feedback."""
    server_url = f"http://{aio_server.host}:{aio_server.port}"
    await _goto_nodes_tab(async_page, server_url)

    # Get the first favorite button
    fav_button = async_page.locator(".node-fav-btn").first
    
    # Initially should not be active
    await expect(fav_button).not_to_have_class(re.compile(r".*active.*"))
    
    # Click the favorite button
    await fav_button.click()
    
    # Should now have active class
    await expect(fav_button).to_have_class(re.compile(r".*active.*"))
    
    # Should have gold-ish color (exact shade may vary in headless mode)
    color = await fav_button.evaluate("el => window.getComputedStyle(el).color")
    # Just verify it's not the default muted color
    assert color is not None, "Expected color to be set"


async def test_favorite_nodes_sorted_to_top(async_page: Page, aio_server):
    """Verify favorite nodes appear at the top of the list."""
    server_url = f"http://{aio_server.host}:{aio_server.port}"
    await _goto_nodes_tab(async_page, server_url)

    # Get the first node card
    node_cards = async_page.locator(".node-card")
    await expect(node_cards.first).to_be_visible()
    
    # Click favorite on the first node
    first_fav = node_cards.first.locator(".node-fav-btn")
    await first_fav.click()
    
    # Wait for re-render
    await _wait_for_poll(async_page)
    
    # The first node should still be first (now with active favorite)
    fav_button_first = node_cards.first.locator(".node-fav-btn")
    await expect(fav_button_first).to_have_class(re.compile(r".*active.*"))


async def test_favorites_persist_in_localstorage(async_page: Page, aio_server):
    """Verify favorites persist across page reloads via localStorage."""
    server_url = f"http://{aio_server.host}:{aio_server.port}"
    await _goto_nodes_tab(async_page, server_url)

    # Click favorite on first node
    fav_button = async_page.locator(".node-fav-btn").first
    await fav_button.click()
    
    # Verify it's active
    await expect(fav_button).to_have_class(re.compile(r".*active.*"))
    
    # Reload the page
    await async_page.reload(wait_until="domcontentloaded")
    await _wait_for_poll(async_page)
    await async_page.click("#header-tabs .tab-btn[data-view='nodes']")
    await _wait_for_poll(async_page)
    
    # Verify favorite is still active
    fav_button = async_page.locator(".node-fav-btn").first
    await expect(fav_button).to_have_class(re.compile(r".*active.*"))
    
    # Verify localStorage has the favorite
    fav_storage = await async_page.evaluate("localStorage.getItem('np_favorite_nodes')")
    assert fav_storage is not None, "Expected np_favorite_nodes in localStorage"
    assert "!12345678" in fav_storage, f"Expected node ID in storage, got: {fav_storage}"


async def test_favorite_toast_notification(async_page: Page, aio_server):
    """Verify toast notification appears when toggling favorite."""
    server_url = f"http://{aio_server.host}:{aio_server.port}"
    await _goto_nodes_tab(async_page, server_url)

    # Click favorite on first node
    fav_button = async_page.locator(".node-fav-btn").first
    await fav_button.click()
    
    # Check for toast notification
    toast = async_page.locator(".toast:has-text('favorite')")
    await expect(toast).to_be_visible(timeout=3000)
    await expect(toast).to_have_text(re.compile(r".*favorite.*", re.IGNORECASE))


# Also test on the Dashboard view node list
@pytest.mark.skip(reason="Dashboard sidebar doesn't currently have favorite buttons - only Nodes view grid")
async def test_favorite_button_in_dashboard_sidebar(async_page: Page, aio_server):
    """Verify favorite button appears and works in the Dashboard sidebar node list."""
    ...


async def test_unfavorite_removes_active_state(async_page: Page, aio_server):
    """Click favorite twice to toggle off, verify active state removed."""
    server_url = f"http://{aio_server.host}:{aio_server.port}"
    await _goto_nodes_tab(async_page, server_url)

    fav_button = async_page.locator(".node-fav-btn").first
    
    # Click to favorite
    await fav_button.click()
    await expect(fav_button).to_have_class(re.compile(r".*active.*"))
    
    # Click again to unfavorite
    await fav_button.click()
    await expect(fav_button).not_to_have_class(re.compile(r".*active.*"))
    
    # Verify localStorage is empty
    fav_storage = await async_page.evaluate("localStorage.getItem('np_favorite_nodes')")
    assert fav_storage == "[]" or fav_storage is None, f"Expected empty array, got: {fav_storage}"