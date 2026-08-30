import asyncio
from unittest.mock import MagicMock
# Just simulating dest conversion
destination = "!4ab142c0"
try:
    dest_num = int(destination.replace("!", ""), 16)
except Exception:
    dest_num = destination
print(f"dest_num: {dest_num}")
