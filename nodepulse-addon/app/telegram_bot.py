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
        
        self.forward_channels = config.telegram_forward_channels
        self.forward_dms = config.telegram_forward_dms
        
        # Callbacks into the meshtastic connection
        self.send_message_callback = send_message_callback
        self.get_status_callback = get_status_callback
        self.get_nodes_callback = get_nodes_callback
        
        self._task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._offset = 0

    async def start(self) -> None:
        if not self.enabled:
            logger.info("Telegram integration is disabled in config")
            return
            
        if not self.token or not self.chat_id:
            logger.warning("Telegram is enabled but token or chat_id is missing")
            return
            
        logger.info("Starting Telegram Bot integration (chat_id=%s)", self.chat_id)
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
        
        # Security: only process messages from the authorized chat
        if chat_id != self.chat_id:
            logger.warning("Rejected Telegram message from unauthorized chat: %s", chat_id)
            return
            
        text = message.get("text", "").strip()
        if not text:
            return
            
        if text.startswith("/"):
            if not self.allow_commands:
                await self._send_text("Commands are disabled in config.")
                return
            await self._handle_command(text)
        else:
            # We require explicit commands to avoid accidental mesh broadcasts
            await self._send_text("Please use `/send <text>` to broadcast to the mesh.")

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
                # Sort by last heard, most recent first, take top 20
                sorted_nodes = sorted(nodes, key=lambda n: n.get("last_heard", 0), reverse=True)[:20]
                for n in sorted_nodes:
                    name = n.get("user", {}).get("shortName") or n.get("user", {}).get("longName") or n.get("id")
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
            await self._api_call("sendMessage", {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "Markdown"
            })
        except Exception as exc:
            logger.error("Failed to send Telegram response: %s", exc)

    def forward_mesh_message(self, entry: Dict[str, Any]) -> None:
        """
        Called synchronously by the mesh receive thread when a new message arrives.
        We schedule it onto the async event loop to send to Telegram.
        """
        if not self.enabled or not self._session or not self._task:
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
            
        name = entry.get("from_name", "Unknown")
        text = entry.get("text", "")
        snr = entry.get("rx_snr")
        
        snr_str = f" [SNR: {snr}]" if snr is not None else ""
        ch_str = "[DM]" if is_dm else f"[Ch {channel}]"
        
        # Escape markdown formatting characters in the user's name
        safe_name = name.replace("*", "").replace("_", "").replace("`", "")
        
        msg = f"📩 {ch_str} *{safe_name}*{snr_str}:\n{text}"
        
        # Schedule the coroutine
        if not self._task.done():
            # Use asyncio.create_task to fire and forget
            asyncio.create_task(self._send_text(msg))
