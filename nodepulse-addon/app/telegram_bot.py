"""
NodePulse Addon — Telegram Bot Bridge.

Provides a bidirectional bridge between Telegram and the Meshtastic network.
Uses long-polling via aiohttp to receive commands securely from an authorized
Telegram chat, and provides a callback to forward inbound mesh text messages.
"""
import asyncio
import logging
from typing import Optional, Callable, Dict, Any

import aiohttp

logger = logging.getLogger(__name__)


class TelegramBot:
    def __init__(
        self,
        config,
        send_message_callback: Callable,
        get_status_callback: Callable,
        get_nodes_callback: Callable,
    ):
        self._config = config
        self.enabled = config.telegram_enabled
        self.token = config.telegram_bot_token.strip()
        self.chat_id = str(config.telegram_chat_id).strip()
        self.allow_commands = config.telegram_allow_commands
        
        # Support both old single chat_id and new list of authorized chat IDs.
        # Normalize to strings: Telegram chat ids arrive as strings (group ids
        # are negative), so ints from config would never match the comparison.
        self._authorized_list = [str(c) for c in config.telegram_authorized_chat_ids if c]
        if self.chat_id and self.chat_id not in self._authorized_list:
            self._authorized_list.append(self.chat_id)
        self.authorized_chat_ids = set(self._authorized_list)
        # Primary target for mesh->Telegram forwarding when no inbound message
        # has been seen yet (e.g. at startup).
        self._default_forward_chat = self._authorized_list[0] if self._authorized_list else ""
        
        self.forward_channels = config.telegram_forward_channels
        self.forward_dms = config.telegram_forward_dms
        
        # Callbacks into the meshtastic connection
        self.send_message_callback = send_message_callback
        self.get_status_callback = get_status_callback
        self.get_nodes_callback = get_nodes_callback
        
        self._task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._offset = 0
        self._current_chat_id: Optional[str] = None
        # Stored at start() time so forward_mesh_message() (called from the
        # meshtastic receive thread) can schedule coroutines safely via
        # call_soon_threadsafe instead of the non-thread-safe create_task().
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def start(self) -> None:
        if not self.enabled:
            logger.info("Telegram integration is disabled in config")
            return
            
        if not self.token:
            logger.warning("Telegram is enabled but token is missing")
            return
            
        if not self.authorized_chat_ids:
            logger.warning(
                "Telegram is enabled but no chat_id / telegram_authorized_chat_ids configured"
            )
            return
            
        logger.info(
            "Starting Telegram Bot integration (authorized_chats=%s)",
            sorted(self.authorized_chat_ids),
        )
        # Capture the running event loop so forward_mesh_message() (a sync
        # method called from the meshtastic receive thread) can schedule
        # coroutines safely via call_soon_threadsafe.
        self._loop = asyncio.get_running_loop()
        # Use a long timeout for the ClientSession to accommodate long polling
        timeout = aiohttp.ClientTimeout(total=40)
        self._session = aiohttp.ClientSession(timeout=timeout)
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._session:
            await self._session.close()

    async def _api_call(self, method: str, data: dict = None) -> dict:
        if not self._session:
            return {}
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        async with self._session.post(url, json=data or {}) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _run_loop(self) -> None:
        reconnect_interval = 5
        while True:
            try:
                # getUpdates with long polling timeout of 30s
                data = {"offset": self._offset, "timeout": 30, "allowed_updates": ["message"]}
                result = await self._api_call("getUpdates", data)
                
                if result.get("ok"):
                    reconnect_interval = 5
                    for update in result.get("result", []):
                        self._offset = update["update_id"] + 1
                        message = update.get("message")
                        if message:
                            await self._handle_message(message)
                else:
                    logger.warning("Telegram getUpdates returned not ok: %s", result)
                    await asyncio.sleep(reconnect_interval)
                    
            except asyncio.TimeoutError:
                # Expected with long polling if no messages arrive
                pass 
            except asyncio.CancelledError:
                logger.info("Telegram polling cancelled")
                break
            except Exception as exc:
                logger.error("Telegram API error: %s", exc)
                await asyncio.sleep(reconnect_interval)
                reconnect_interval = min(reconnect_interval * 2, 60)
                
    async def _handle_message(self, message: dict) -> None:
        chat_id = str(message.get("chat", {}).get("id", ""))
        chat_type = message.get("chat", {}).get("type", "private")
        
        # Security: only process messages from authorized chats
        if chat_id not in self.authorized_chat_ids:
            logger.warning(
                "Rejected Telegram message from unauthorized chat %s (type: %s). Authorized: %s",
                chat_id, chat_type, sorted(self.authorized_chat_ids)
            )
            return
            
        text = message.get("text", "").strip()
        if not text:
            return
            
        # Store the current chat_id for responses
        self._current_chat_id = chat_id
            
        if text.startswith("/"):
            if not self.allow_commands:
                await self._send_text("Commands are disabled in config.")
                return
            await self._handle_command(text)
            return
            
        # Check if this is a native Telegram reply to a forwarded mesh message
        reply_to = message.get("reply_to_message")
        if reply_to and "text" in reply_to:
            orig_text = reply_to["text"]
            if orig_text.startswith("📩"):
                import re
                ch_match = re.search(r"\[Ch (\d+)\]", orig_text)
                id_match = re.search(r"\((![a-fA-F0-9]+)\)", orig_text)
                
                if "[DM]" in orig_text:
                    if id_match:
                        dest_id = id_match.group(1)
                        logger.info("Routing Telegram reply as DM to %s", dest_id)
                        success = await self.send_message_callback(text, destination=dest_id)
                        if success:
                            await self._send_text(f"✅ Reply sent as DM to {dest_id}.")
                        else:
                            await self._send_text("❌ Failed to send DM.")
                    else:
                        await self._send_text("❌ Cannot determine node ID for DM reply.")
                    return
                elif ch_match:
                    ch_idx = int(ch_match.group(1))
                    logger.info("Routing Telegram reply to Channel %d", ch_idx)
                    success = await self.send_message_callback(text, channel=ch_idx)
                    if success:
                        await self._send_text(f"✅ Reply sent to Channel {ch_idx}.")
                    else:
                        await self._send_text(f"❌ Failed to send to Channel {ch_idx}.")
                    return

        # Plain messages in any authorized chat (group or private) are broadcast to the mesh.
        sender_name = message.get("from", {}).get("first_name", "Telegram")
        formatted_text = f"[{sender_name}] {text}"
        
        logger.info("Broadcasting Telegram message from chat %s to the mesh", chat_id)
        success = await self.send_message_callback(formatted_text, channel=0)
        if not success:
            await self._send_text("❌ Failed to broadcast message to mesh.")
        else:
            await self._send_text("✅ Message sent to mesh.")

    async def _handle_command(self, text: str) -> None:
        parts = text.split(" ", 1)
        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        
        try:
            if command == "/status":
                status = await self.get_status_callback()
                info = status.get("my_info", {})
                batt = info.get("battery_level", "Unknown")
                mac = info.get("macaddr", "Unknown")
                nodes_cnt = len(await self.get_nodes_callback())
                await self._send_text(
                    f"📡 *NodePulse Status*\n"
                    f"Nodes: {nodes_cnt}\n"
                    f"Battery: {batt}%\n"
                    f"MAC: {mac}"
                )
                
            elif command == "/nodes":
                nodes = await self.get_nodes_callback()
                lines = ["*Visible Mesh Nodes:*"]
                # Sort by last heard, most recent first, take top 20.
                # The callback returns NodePulse-serialised dicts (flat keys),
                # not the raw meshtastic protobuf structure (user.shortName).
                sorted_nodes = sorted(nodes, key=lambda n: n.get("last_heard") or 0, reverse=True)[:20]
                for n in sorted_nodes:
                    name = n.get("short_name") or n.get("long_name") or n.get("id", "?")
                    snr = n.get("snr", "N/A")
                    lines.append(f"• {name} (SNR: {snr})")
                if len(nodes) > 20:
                    lines.append(f"...and {len(nodes) - 20} more.")
                await self._send_text("\n".join(lines)[:4000])
                
            elif command == "/send":
                if not args:
                    await self._send_text("Usage: `/send <message>`")
                    return
                # Send to channel 0 by default
                logger.info("Telegram /send command: sending '%s' to channel 0", args[:50])
                success = await self.send_message_callback(args, channel=0)
                if success:
                    logger.info("Telegram /send command: message sent successfully")
                    await self._send_text("✅ Message sent to mesh.")
                else:
                    logger.warning("Telegram /send command: message send failed")
                    await self._send_text("❌ Failed to send message.")
                    
            elif command == "/dm":
                dm_parts = args.split(" ", 1)
                if len(dm_parts) < 2:
                    await self._send_text("Usage: `/dm !node_id <message>`")
                    return
                dest = dm_parts[0]
                msg = dm_parts[1]
                success = await self.send_message_callback(msg, destination=dest)
                if success:
                    await self._send_text(f"✅ DM sent to {dest}.")
                else:
                    await self._send_text("❌ Failed to send DM.")
                    
            elif command == "/help":
                await self._send_text(
                    "Commands:\n"
                    "`/status` - Radio status\n"
                    "`/nodes` - List top nodes\n"
                    "`/send <msg>` - Broadcast to primary channel\n"
                    "`/dm !node <msg>` - Direct message"
                )
                
            else:
                await self._send_text("Unknown command. Type /help.")
        except Exception as exc:
            logger.error("Error executing Telegram command %s: %s", command, exc)
            await self._send_text("❌ Error executing command.")

    async def _send_text(self, text: str) -> None:
        try:
            # Use the current chat_id (from the incoming message) or fall back
            # to the configured chat_id / first authorized chat.
            target_chat_id = self._current_chat_id or self._default_forward_chat
            await self._api_call("sendMessage", {
                "chat_id": target_chat_id,
                "text": text,
                "parse_mode": "Markdown"
            })
        except Exception as exc:
            logger.error("Failed to send Telegram response to chat %s: %s", target_chat_id, exc)

    def forward_mesh_message(self, entry: Dict[str, Any]) -> None:
        """
        Called synchronously by the mesh receive thread when a new message arrives.
        We schedule it onto the async event loop to send to Telegram.

        NOTE: this method runs on the meshtastic library's background receive
        thread, NOT on the asyncio event loop thread.  We must NOT call
        asyncio.create_task() here — that is only safe from a coroutine or a
        callback already running on the loop.  Instead we use
        call_soon_threadsafe() which is the correct cross-thread bridge.
        """
        if not self.enabled or not self._session or not self._task or not self._loop:
            return

        # Ignore our own outgoing messages
        if entry.get("outgoing"):
            return

        is_dm = entry.get("is_dm", False)
        channel = entry.get("channel", 0)

        if is_dm and not self.forward_dms:
            return

        if not is_dm and channel not in self.forward_channels:
            return

        from_id = entry.get("from_id", "")
        name = entry.get("from_name", "Unknown")
        text = entry.get("text", "")
        snr = entry.get("rx_snr")

        snr_str = f" [SNR: {snr}]" if snr is not None else ""
        ch_str = "[DM]" if is_dm else f"[Ch {channel}]"
        id_str = f" ({from_id})" if from_id else ""

        # Escape markdown formatting characters in the user's name
        safe_name = name.replace("*", "").replace("_", "").replace("`", "")

        msg = f"📩 {ch_str} *{safe_name}*{id_str}{snr_str}:\n{text}"

        # Schedule the send coroutine onto the event loop from this non-async
        # thread.  call_soon_threadsafe is the thread-safe way to submit work
        # to a running asyncio loop; create_task would silently fail or attach
        # to the wrong loop when called from outside the loop thread.
        if not self._task.done():
            self._loop.call_soon_threadsafe(
                lambda m=msg: asyncio.ensure_future(self._send_text(m), loop=self._loop)
            )
