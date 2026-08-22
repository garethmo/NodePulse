from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        await p.chromium.launch()
        # We need the server running. Oh wait, we can't do this without the server.
