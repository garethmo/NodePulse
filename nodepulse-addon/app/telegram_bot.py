"""
NodePulse Addon — Telegram Bot Bridge.

Provides a bidirectional bridge between Telegram and the Meshtastic network.
Uses long-polling via aiohttp to receive commands securely from an authorized
Telegram chat, and provides a callback to forward inbound mesh text messages.
"""
import asyncio
import contextlib
import datetime
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import aiohttp

from .terrain import analyze_coverage, analyze_link

logger = logging.getLogger(__name__)


class TelegramBot:
    def __init__(
        self,
        config,
        send_message_callback: Callable,
        get_status_callback: Callable,
        get_nodes_callback: Callable,
        get_channels_callback: Callable | None = None,
        conn=None,
        terrain=None,
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
        self.get_channels_callback = get_channels_callback

        # Optional direct handles to the meshtastic connection and terrain
        # service, used by the richer command set (traceroute, terrain analysis,
        # remote administration). Kept optional so the bot stays testable with
        # only the lightweight callbacks wired up.
        self._conn = conn
        self._terrain = terrain
        
        self._task: asyncio.Task | None = None
        self._session: aiohttp.ClientSession | None = None
        self._offset = 0
        self._current_chat_id: str | None = None
        # Stored at start() time so forward_mesh_message() (called from the
        # meshtastic receive thread) can schedule coroutines safely via
        # call_soon_threadsafe instead of the non-thread-safe create_task().
        self._loop: asyncio.AbstractEventLoop | None = None
        # Map Telegram message_id -> forwarding metadata (channel / DM node)
        # for every mesh message we relay to Telegram. Replies are routed by
        # message_id so we never depend on parsing the displayed text (which
        # Telegram's Markdown rendering can alter). Guarded by _forward_lock.
        self._forwarded: dict[int, dict[str, Any]] = {}
        self._forward_lock = threading.Lock()

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
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
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
            except Exception as exc:  # noqa: BLE001
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
        if reply_to:
            reply_id = reply_to.get("message_id")
            forwarded = None
            if reply_id is not None:
                with self._forward_lock:
                    forwarded = self._forwarded.get(reply_id)
            if forwarded is not None:
                # Preferred path: route by message_id — immune to Markdown
                # rendering altering the replied-to text.
                if forwarded.get("is_dm"):
                    dest_id = forwarded.get("node")
                    if dest_id:
                        logger.info("Routing Telegram reply as DM to %s", dest_id)
                        success = await self.send_message_callback(text, destination=dest_id)
                        if success:
                            await self._send_text(f"✅ Reply sent as DM to {dest_id}.")
                        else:
                            await self._send_text("❌ Failed to send DM.")
                    else:
                        await self._send_text("❌ Cannot determine node ID for DM reply.")
                else:
                    ch_idx = forwarded.get("channel", 0)
                    logger.info("Routing Telegram reply to Channel %d", ch_idx)
                    success = await self.send_message_callback(text, channel=ch_idx)
                    if success:
                        await self._send_text(f"✅ Reply sent to Channel {ch_idx}.")
                    else:
                        await self._send_text(f"❌ Failed to send to Channel {ch_idx}.")
                return

            # Fallback: parse the replied-to text (covers messages forwarded
            # before the message_id tracking existed).
            orig_text = reply_to.get("text", "")
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
                connected = bool(status.get("connected"))
                info = status.get("my_info", {}) or {}
                batt = info.get("battery_level", "Unknown")
                nodes_cnt = len(await self.get_nodes_callback())

                if connected:
                    online = "✅ Online"
                    self_name = info.get("long_name") or info.get("short_name") or info.get("node_id", "Self")
                else:
                    online = "❌ Offline"
                    self_name = "Radio not connected"

                last_heard = info.get("last_heard")
                if last_heard:
                    last_heard_str = self._format_relative_time(last_heard)
                else:
                    last_heard_str = "Unknown"

                uptime = info.get("uptime")
                uptime_str = self._format_uptime(uptime) if uptime else "Unknown"

                await self._send_text(
                    f"📡 *NodePulse Status*\n"
                    f"Node: {self._escape_md(self_name)}\n"
                    f"Status: {online}\n"
                    f"Last heard: {last_heard_str}\n"
                    f"Uptime: {uptime_str}\n"
                    f"Battery: {batt}%\n"
                    f"Nodes: {nodes_cnt}"
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
                    badge = " 🔒" if n.get("public_key") else ""
                    line = f"• {self._escape_md(name)}{badge} (SNR: {snr})"
                    status = n.get("status")
                    if status:
                        line += f" — {self._escape_md(status)}"
                    lines.append(line)
                if len(nodes) > 20:
                    lines.append(f"...and {len(nodes) - 20} more.")
                await self._send_text("\n".join(lines)[:4000])
                
            elif command == "/channels":
                if self.get_channels_callback is None:
                    await self._send_text("❌ Channel listing unavailable.")
                    return
                channels = await self.get_channels_callback()
                if not channels:
                    await self._send_text("No channels found on the node.")
                    return
                lines = ["*Configured Channels:*"]
                for ch in channels:
                    idx = ch.get("index", "?")
                    name = ch.get("name", "") or "Unnamed"
                    role = ch.get("role", "").lower().capitalize()
                    active = "✓" if idx in self.forward_channels else "—"
                    lines.append(f"{active} Ch {idx} - {self._escape_md(name)} ({self._escape_md(role)})")
                await self._send_text("\n".join(lines)[:4000])

            elif command == "/send":
                if not args:
                    await self._send_text("Usage: `/send [channel] <message>`")
                    return
                # Optional channel selector: "/send 1 hello" or "/send #1 hello"
                # send to channel 1; otherwise fall back to channel 0. A bare
                # numeric message like "/send 5" is still treated as text so it
                # goes to the default channel.
                first, _, rest = args.partition(" ")
                channel = 0
                msg = args.strip()
                if first.startswith("#") and first[1:].isdigit():
                    channel = int(first[1:])
                    msg = rest.strip()
                elif first.isdigit() and rest.strip():
                    channel = int(first)
                    msg = rest.strip()
                if not msg:
                    await self._send_text(f"Usage: `/send [{channel}] <message>`")
                    return
                if channel < 0 or channel > 15:
                    await self._send_text(f"❌ Invalid channel {channel}. Use 0-15.")
                    return
                logger.info("Telegram /send command: sending '%s' to channel %d", msg[:50], channel)
                success = await self.send_message_callback(msg, channel=channel)
                if success:
                    logger.info("Telegram /send command: message sent successfully")
                    await self._send_text(f"✅ Message sent to Channel {channel}.")
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

            elif command == "/device":
                status = await self.get_status_callback()
                info = (status or {}).get("my_info", {}) or {}
                name = self._escape_md(info.get("long_name") or info.get("short_name") or "Self")
                hw = self._escape_md(info.get("hw_model") or "Unknown")
                fw = self._escape_md(info.get("firmware_version") or "Unknown")
                await self._send_text(
                    f"🔧 *Device*\n"
                    f"Node: {name}\n"
                    f"HW model: {hw}\n"
                    f"Firmware: {fw}"
                )

            elif command == "/where":
                if not args:
                    await self._send_text("Usage: `/where !node`")
                    return
                nodes = await self.get_nodes_callback()
                node = self._resolve_node(nodes, args)
                if not node:
                    await self._send_text(f"❌ Node not found: {self._escape_md(args)}")
                    return
                lat = node.get("latitude")
                lng = node.get("longitude")
                name = self._node_label(node)
                signed = " 🔒" if node.get("public_key") else ""
                status = node.get("status")
                status_line = f"\nStatus: {self._escape_md(status)}" if status else ""
                if lat is None or lng is None:
                    await self._send_text(f"📍 *{name}*{signed}\nNo position known.{status_line}")
                    return
                lh = (
                    self._format_relative_time(node.get("last_heard"))
                    if node.get("last_heard")
                    else "Unknown"
                )
                map_url = f"https://www.openstreetmap.org/?mlat={lat}&mlon={lng}#map=15/{lat}/{lng}"
                await self._send_text(
                    f"📍 *{name}*{signed}\n"
                    f"Lat: {lat:.5f}\n"
                    f"Lon: {lng:.5f}\n"
                    f"Last heard: {lh}\n"
                    f"{map_url}{status_line}"
                )

            elif command == "/neighbors":
                if not args:
                    await self._send_text("Usage: `/neighbors !node`")
                    return
                nodes = await self.get_nodes_callback()
                node = self._resolve_node(nodes, args)
                if not node:
                    await self._send_text(f"❌ Node not found: {self._escape_md(args)}")
                    return
                neighbors = node.get("neighbors") or []
                name = self._node_label(node)
                if not neighbors:
                    await self._send_text(f"🔗 *{name}* has no known neighbors.")
                    return
                lines = [f"🔗 *{name} — {len(neighbors)} neighbors:*"]
                for nb in neighbors[:20]:
                    nb_id = nb.get("id", "?")
                    nb_node = self._resolve_node(nodes, nb_id)
                    nb_name = self._node_label(nb_node) if nb_node else nb_id
                    snr = nb.get("snr")
                    snr_str = f" (SNR {snr:.1f}dB)" if snr is not None else ""
                    lines.append(f"• {nb_name}{snr_str}")
                await self._send_text("\n".join(lines)[:4000])

            elif command == "/last":
                nodes = await self.get_nodes_callback()
                recent = sorted(
                    nodes, key=lambda n: n.get("last_heard") or 0, reverse=True
                )[:15]
                if not recent:
                    await self._send_text("No nodes heard yet.")
                    return
                lines = ["*Recently heard:*"]
                for n in recent:
                    name = self._node_label(n)
                    lh = (
                        self._format_relative_time(n.get("last_heard"))
                        if n.get("last_heard")
                        else "Unknown"
                    )
                    lines.append(f"• {name} ({lh})")
                await self._send_text("\n".join(lines)[:4000])

            elif command == "/link":
                if not self._terrain:
                    await self._send_text("❌ Terrain service unavailable.")
                    return
                parts = args.split()
                if len(parts) < 2:
                    await self._send_text("Usage: `/link !nodeA !nodeB [freq_mhz]`")
                    return
                freq = 915.0
                if len(parts) >= 3:
                    try:
                        freq = float(parts[2])
                    except ValueError:
                        await self._send_text("❌ Frequency must be a number.")
                        return
                nodes = await self.get_nodes_callback()
                na = self._resolve_node(nodes, parts[0])
                nb = self._resolve_node(nodes, parts[1])
                if not na or not nb:
                    await self._send_text("❌ One or both nodes not found.")
                    return
                lat1, lng1 = na.get("latitude"), na.get("longitude")
                lat2, lng2 = nb.get("latitude"), nb.get("longitude")
                if None in (lat1, lng1, lat2, lng2):
                    await self._send_text(
                        "❌ Both nodes need a known position for link analysis."
                    )
                    return
                try:
                    elevations = await self._terrain.sample_path(
                        lat1, lng1, lat2, lng2, 48
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Telegram /link elevation failed: %s", exc)
                    await self._send_text("❌ Terrain elevation lookup failed.")
                    return
                if any(e is None for e in elevations):
                    await self._send_text(
                        "❌ Terrain elevation unavailable along this path."
                    )
                    return
                result = analyze_link(
                    from_point={"lat": lat1, "lng": lng1},
                    to_point={"lat": lat2, "lng": lng2},
                    freq_mhz=freq,
                    elevations=elevations,
                )
                name_a = self._node_label(na)
                name_b = self._node_label(nb)
                los = "✅ LOS clear" if result["los_clear"] else "⛔ LOS blocked"
                fres = (
                    "✅ Fresnel clear"
                    if result["fresnel_clear"]
                    else "⚠️ Fresnel marginal"
                )
                await self._send_text(
                    f"📡 *Link {name_a} → {name_b}*\n"
                    f"Distance: {result['distance_km']} km\n"
                    f"{los}\n{fres}\n"
                    f"Min clearance: {result['min_clearance_ratio']}\n"
                    f"Fade margin: {result['link_budget']['fade_margin_db']} dB"
                )

            elif command == "/coverage":
                if not self._terrain:
                    await self._send_text("❌ Terrain service unavailable.")
                    return
                parts = args.split()
                if not parts:
                    await self._send_text(
                        "Usage: `/coverage !node [radius_m] [freq_mhz]`"
                    )
                    return
                radius = 8000.0
                freq = 915.0
                if len(parts) >= 2:
                    try:
                        radius = float(parts[1])
                    except ValueError:
                        await self._send_text("❌ Radius must be a number.")
                        return
                if len(parts) >= 3:
                    try:
                        freq = float(parts[2])
                    except ValueError:
                        await self._send_text("❌ Frequency must be a number.")
                        return
                nodes = await self.get_nodes_callback()
                node = self._resolve_node(nodes, parts[0])
                if not node:
                    await self._send_text(f"❌ Node not found: {self._escape_md(parts[0])}")
                    return
                lat, lng = node.get("latitude"), node.get("longitude")
                if lat is None or lng is None:
                    await self._send_text(
                        "❌ Node has no known position for coverage analysis."
                    )
                    return
                try:
                    result = await analyze_coverage(
                        self._terrain,
                        lat,
                        lng,
                        radius,
                        freq,
                        tx_power_dbm=0.0,
                        tx_gain_dbi=0.0,
                        rx_gain_dbi=0.0,
                        rx_sensitivity_dbm=-137.0,
                        tx_antenna_height_m=2.0,
                        rx_antenna_height_m=2.0,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Telegram /coverage failed: %s", exc)
                    await self._send_text("❌ Coverage analysis failed.")
                    return
                name = self._node_label(node)
                polys = result.get("polygons", {})
                strong = len(polys.get("strong", []))
                await self._send_text(
                    f"📡 *Coverage {name}*\n"
                    f"Radius: {radius:.0f} m\n"
                    f"Frequency: {freq:.0f} MHz\n"
                    f"Strong-coverage vertices: {strong}"
                )

            elif command == "/traceroute":
                if not self._conn:
                    await self._send_text("❌ Mesh connection unavailable.")
                    return
                if not args:
                    await self._send_text("Usage: `/traceroute !node`")
                    return
                nodes = await self.get_nodes_callback()
                node = self._resolve_node(nodes, args)
                if not node:
                    await self._send_text(f"❌ Node not found: {self._escape_md(args)}")
                    return
                dest = node.get("id")
                name = self._node_label(node)
                if not await self._conn.request_traceroute(dest):
                    await self._send_text(
                        "❌ Could not queue traceroute (too many pending)."
                    )
                    return
                await self._send_text(f"🔍 Requesting traceroute to {name}…")
                num_to_name = {
                    int(n["id"].lstrip("!"), 16) & 0xFFFFFFFF: self._node_label(n)
                    for n in nodes
                    if n.get("id")
                }
                record = None
                for _ in range(15):
                    await asyncio.sleep(2)
                    refreshed = await self.get_nodes_callback()
                    current = self._resolve_node(refreshed, dest)
                    rec = current.get("traceroute") if current else None
                    if rec and (rec.get("route") or rec.get("route_back")):
                        record = rec
                        break
                if record:
                    await self._send_text(self._format_traceroute(record, num_to_name))
                else:
                    await self._send_text(
                        f"⏱️ No traceroute result for {name} yet (timed out)."
                    )

            elif command == "/ping":
                if not args:
                    await self._send_text("Usage: `/ping !node`")
                    return
                nodes = await self.get_nodes_callback()
                node = self._resolve_node(nodes, args)
                if not node:
                    await self._send_text(f"❌ Node not found: {self._escape_md(args)}")
                    return
                dest = node.get("id")
                name = self._node_label(node)
                ok = await self.send_message_callback("ping", destination=dest)
                snr = node.get("snr")
                rssi = node.get("rssi")
                signal = ""
                if snr is not None or rssi is not None:
                    signal = (
                        f"\nLast signal — SNR: {snr}, RSSI: {rssi}"
                        if snr is not None and rssi is not None
                        else f"\nLast signal — {snr if snr is not None else rssi}"
                    )
                await self._send_text(
                    f"{'✅ Ping sent to' if ok else '❌ Failed to ping'} {name}.{signal}"
                )

            elif command == "/reboot":
                if not self._conn:
                    await self._send_text("❌ Mesh connection unavailable.")
                    return
                if not args:
                    status = await self.get_status_callback()
                    info = (status or {}).get("my_info", {}) or {}
                    dest = info.get("node_id")
                    target = "this gateway"
                else:
                    nodes = await self.get_nodes_callback()
                    node = self._resolve_node(nodes, args)
                    if not node:
                        await self._send_text(f"❌ Node not found: {self._escape_md(args)}")
                        return
                    dest = node.get("id")
                    target = self._node_label(node)
                if not dest:
                    await self._send_text("❌ Could not determine target node.")
                    return
                try:
                    await self._conn.remote_admin_action(
                        dest, "reboot", {"seconds": 10}
                    )
                    await self._send_text(f"🔄 Reboot sent to {target}.")
                except Exception as exc:  # noqa: BLE001
                    await self._send_text(f"❌ Reboot failed: {exc}")

            elif command == "/setpos":
                if not self._conn:
                    await self._send_text("❌ Mesh connection unavailable.")
                    return
                parts = args.split()
                if len(parts) < 3:
                    await self._send_text("Usage: `/setpos !node <lat> <lon> [alt_m]`")
                    return
                try:
                    lat = float(parts[1])
                    lng = float(parts[2])
                except ValueError:
                    await self._send_text("❌ Latitude/longitude must be numbers.")
                    return
                alt = int(float(parts[3])) if len(parts) >= 4 else 0
                nodes = await self.get_nodes_callback()
                node = self._resolve_node(nodes, parts[0])
                if not node:
                    await self._send_text(f"❌ Node not found: {self._escape_md(parts[0])}")
                    return
                dest = node.get("id")
                name = self._node_label(node)
                try:
                    await self._conn.remote_admin_action(
                        dest,
                        "set_fixed_position",
                        {"lat": lat, "lng": lng, "alt": alt},
                    )
                    # 2.8 firmware blocks precise position broadcasts on public
                    # (unencrypted) channels. Fixed position set via admin still
                    # applies locally, but warn the user if the primary channel
                    # is public so they aren't surprised that others can't see it.
                    warn = ""
                    channels = None
                    if self.get_channels_callback is not None:
                        try:
                            channels = await self.get_channels_callback()
                        except Exception:  # noqa: BLE001
                            channels = None
                    if channels and any(
                        c.get("index") == 0 and c.get("public") for c in channels
                    ):
                        warn = (
                            "\n⚠️ Primary channel is public — 2.8 firmware blocks "
                            "precise position broadcasts there. The fixed position is "
                            "set on the node but won't be shared on the public channel."
                        )
                    await self._send_text(
                        f"📌 Fixed position set for {name}: {lat:.5f}, {lng:.5f} (alt {alt}m).{warn}"
                    )
                except Exception as exc:  # noqa: BLE001
                    await self._send_text(f"❌ Set position failed: {exc}")

            elif command == "/find":
                if not self._conn:
                    await self._send_text("❌ Mesh connection unavailable.")
                    return
                if not args:
                    await self._send_text("Usage: `/find !node`")
                    return
                nodes = await self.get_nodes_callback()
                node = self._resolve_node(nodes, args)
                if not node:
                    await self._send_text(f"❌ Node not found: {self._escape_md(args)}")
                    return
                dest = node.get("id")
                name = self._node_label(node)
                lat = node.get("latitude")
                lng = node.get("longitude")
                pos = (
                    f"Last known: {lat:.5f}, {lng:.5f}"
                    if lat is not None and lng is not None
                    else "No position known"
                )
                await self._conn.request_position(dest)
                await self._send_text(
                    f"📡 Requested position from {name}.\n{pos}"
                )

            elif command == "/diag":
                if not self._conn:
                    await self._send_text("❌ Mesh connection unavailable.")
                    return
                if not args:
                    await self._send_text("Usage: `/diag !node`")
                    return
                nodes = await self.get_nodes_callback()
                node = self._resolve_node(nodes, args)
                if not node:
                    await self._send_text(f"❌ Node not found: {self._escape_md(args)}")
                    return
                dest = node.get("id")
                name = self._node_label(node)
                try:
                    sig = await self._conn.get_node_signal(dest)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Telegram /diag failed: %s", exc)
                    await self._send_text("❌ Could not read node diagnostics.")
                    return
                if not sig:
                    await self._send_text(f"❌ No diagnostics for {name}.")
                    return

                def fval(v, unit="", na="n/a"):
                    return f"{v}{unit}" if v is not None else na

                lh = (
                    self._format_relative_time(node.get("last_heard"))
                    if node.get("last_heard")
                    else "Unknown"
                )
                await self._send_text(
                    f"🩺 *Diag {name}*\n"
                    f"Hops away: {fval(node.get('hops_away'))}\n"
                    f"SNR avg: {fval(sig.get('snr_avg'), 'dB')}\n"
                    f"Signal: {sig.get('signal_quality') or 'n/a'}\n"
                    f"Battery: {fval(node.get('battery_level'), '%')}\n"
                    f"Voltage: {fval(node.get('voltage'), 'V')}\n"
                    f"Uptime: {self._format_uptime(node.get('uptime'))}\n"
                    f"Chan util: {fval(node.get('channel_utilization'), '%')}\n"
                    f"Air util TX: {fval(node.get('air_util_tx'), '%')}\n"
                    f"Noise floor: {fval(sig.get('noise_floor'), 'dBm')}\n"
                    f"Pos fixes: {fval(node.get('position_fix_count'))}\n"
                    f"Last heard: {lh}"
                )

            elif command == "/gpx":
                if not self._conn:
                    await self._send_text("❌ Mesh connection unavailable.")
                    return
                if not args:
                    await self._send_text("Usage: `/gpx !node`")
                    return
                nodes = await self.get_nodes_callback()
                node = self._resolve_node(nodes, args)
                if not node:
                    await self._send_text(f"❌ Node not found: {self._escape_md(args)}")
                    return
                dest = node.get("id")
                name = self._node_label(node)
                try:
                    history = await self._conn.get_position_history(dest)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Telegram /gpx failed: %s", exc)
                    await self._send_text("❌ Could not read position history.")
                    return
                trail = history.get(dest, [])
                if not trail:
                    await self._send_text(f"📍 *{name}* has no position history to export.")
                    return
                gpx = self._build_gpx(name, dest, trail)
                sent = await self._send_document(gpx, f"{dest.lstrip('!')}_track.gpx")
                if not sent:
                    await self._send_text("❌ Failed to send GPX file.")

            elif command == "/hops":
                nodes = await self.get_nodes_callback()
                buckets: dict[str, int] = {}
                for n in nodes:
                    h = n.get("hops_away")
                    key = str(h) if (h is not None and h <= 5) else ("6+" if h is not None else "unknown")
                    buckets[key] = buckets.get(key, 0) + 1
                lines = ["*Nodes per hop:*"]
                for k in ("0", "1", "2", "3", "4", "5", "6+", "unknown"):
                    if k in buckets:
                        lines.append(f"Hop {k}: {buckets[k]}")
                lines.append(f"\nTotal nodes: {len(nodes)}")
                await self._send_text("\n".join(lines)[:4000])

            elif command == "/waypoint":
                if not self._conn:
                    await self._send_text("❌ Mesh connection unavailable.")
                    return
                parts = args.split()
                if len(parts) < 2:
                    await self._send_text("Usage: `/waypoint <lat> <lon> [name] [expire_hours]`")
                    return
                try:
                    lat = float(parts[0])
                    lng = float(parts[1])
                except ValueError:
                    await self._send_text("❌ Latitude/longitude must be numbers.")
                    return
                name = parts[2] if len(parts) >= 3 else "Waypoint"
                expire = None
                if len(parts) >= 4:
                    try:
                        expire = int(time.time() + float(parts[3]) * 3600)
                    except ValueError:
                        await self._send_text("❌ expire_hours must be a number.")
                        return
                wp = {
                    "lat": lat,
                    "lng": lng,
                    "name": name,
                    "description": "",
                    "icon": "📍",
                    "expire": expire,
                }
                try:
                    res = await self._conn.send_waypoint(wp)
                except Exception as exc:  # noqa: BLE001
                    await self._send_text(f"❌ Waypoint failed: {exc}")
                    return
                await self._send_text(
                    f"📌 Waypoint '{self._escape_md(name)}' created ({res.get('detail', '')})."
                )

            elif command == "/beacon":
                if not self._conn:
                    await self._send_text("❌ Mesh connection unavailable.")
                    return
                try:
                    bc = await self._conn.get_beacon_config()
                except Exception as exc:  # noqa: BLE001
                    await self._send_text(f"❌ Could not read beacon config: {exc}")
                    return
                if not bc.get("available"):
                    await self._send_text(
                        f"📡 *Mesh Beacon*\nNot available: {self._escape_md(bc.get('reason', ''))}"
                    )
                    return
                en = "enabled" if bc.get("enabled") else "disabled"
                lines = [f"📡 *Mesh Beacon* ({en})"]
                if bc.get("listen") is not None:
                    lines.append(f"Listen: {'yes' if bc['listen'] else 'no'}")
                if bc.get("share_beacon_location") is not None:
                    lines.append(f"Share location: {'yes' if bc['share_beacon_location'] else 'no'}")
                if bc.get("interval_seconds") is not None:
                    lines.append(f"Interval: {bc['interval_seconds']}s")
                if bc.get("channel_name"):
                    lines.append(f"Channel: {self._escape_md(bc['channel_name'])}")
                if bc.get("region"):
                    lines.append(f"Region: {self._escape_md(bc['region'])}")
                await self._send_text("\n".join(lines)[:4000])

            elif command == "/help":
                await self._send_text(
                    "Commands:\n"
                    "`/status` - Radio status\n"
                    "`/device` - Hardware model & firmware\n"
                    "`/nodes` - List top nodes\n"
                    "`/last` - Recently heard nodes\n"
                    "`/where !node` - Node position & map link\n"
                    "`/neighbors !node` - Node's direct neighbors\n"
                    "`/link !a !b [freq]` - Terrain link analysis\n"
                    "`/coverage !node [radius] [freq]` - Coverage analysis\n"
                    "`/traceroute !node` - Trace route to a node\n"
                    "`/ping !node` - Send a ping & show last signal\n"
                    "`/find !node` - Request a node's position\n"
                    "`/reboot [!node]` - Reboot gateway or a remote node\n"
                    "`/setpos !node <lat> <lon> [alt]` - Set a node's fixed position\n"
                    "`/diag !node` - Node diagnostics (SNR, battery, noise floor)\n"
                    "`/gpx !node` - Export a node's position history as GPX\n"
                    "`/hops` - Nodes per hop-count distribution\n"
                    "`/waypoint <lat> <lon> [name] [expire_h]` - Drop a waypoint\n"
                    "`/beacon` - Mesh Beacon module status\n"
                    "`/channels` - List configured channels\n"
                    "`/send <msg>` - Broadcast to primary channel\n"
                    "`/send <ch> <msg>` or `/send #<ch> <msg>` - Broadcast to a specific channel\n"
                    "`/dm !node <msg>` - Direct message"
                )
                
            else:
                await self._send_text("Unknown command. Type /help.")
        except Exception as exc:  # noqa: BLE001
            logger.error("Error executing Telegram command %s: %s", command, exc)
            await self._send_text("❌ Error executing command.")

    @staticmethod
    def _format_relative_time(timestamp: float) -> str:
        """Human-readable 'Xm ago' style string for a unix timestamp."""
        try:
            delta = time.time() - float(timestamp)
        except (TypeError, ValueError):
            return "Unknown"
        if delta < 0:
            return "just now"
        seconds = int(delta)
        if seconds < 60:
            return f"{seconds}s ago"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        return f"{days}d ago"

    @staticmethod
    def _format_uptime(uptime_seconds) -> str:
        """Human-readable duration (Xd Yh Zm) for a device uptime in seconds."""
        try:
            total = int(uptime_seconds)
        except (TypeError, ValueError):
            return "Unknown"
        if total < 0:
            return "Unknown"
        days, rem = divmod(total, 86400)
        hours, rem = divmod(rem, 3600)
        minutes = rem // 60
        parts = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        if not parts:
            parts.append(f"{total}s")
        return " ".join(parts)

    @staticmethod
    def _resolve_node(nodes: list[dict[str, Any]], query: str) -> dict | None:
        """Find a node by !hex id, node number, or (partial) name."""
        q = (query or "").strip().lstrip("!")
        if not q:
            return None
        ql = q.lower()
        fallback = None
        for n in nodes:
            nid = (n.get("id") or "").lstrip("!")
            if nid.lower() == ql:
                return n
            num = n.get("num")
            if num is not None and str(int(num) & 0xFFFFFFFF) == ql:
                return n
            sn = (n.get("short_name") or "").lower()
            ln = (n.get("long_name") or "").lower()
            if sn == ql or ln == ql:
                return n
            if fallback is None and (sn.startswith(ql) or ln.startswith(ql)):
                fallback = n
        return fallback

    @staticmethod
    def _escape_md(text: Any) -> str:
        """Escape Telegram legacy-Markdown special characters in dynamic text.

        Node/user names (e.g. ``R1_mini``) and IDs frequently contain
        underscores or asterisks. A single unmatched Markdown delimiter makes
        Telegram reject the whole message as unparseable, which silently
        produces no output. Escaping prevents that.
        """
        s = "" if text is None else str(text)
        for ch in ("\\", "*", "_", "`", "[", "]", "(", ")"):
            s = s.replace(ch, "\\" + ch)
        return s

    @staticmethod
    def _node_label(node: dict[str, Any]) -> str:
        return TelegramBot._escape_md(
            node.get("long_name") or node.get("short_name") or node.get("id") or "?"
        )

    @staticmethod
    def _format_traceroute(record: dict[str, Any], num_to_name: dict[int, str]) -> str:
        """Render a captured traceroute record as a readable path string."""

        def _name(num) -> str:
            # Route entries may be raw uint32 ints OR "!hex" node-id strings.
            s = str(num).lstrip("!")
            try:
                key = int(s)
            except ValueError:
                try:
                    key = int(s, 16)
                except ValueError:
                    return str(num)
            key &= 0xFFFFFFFF
            # 0xffffffff is Meshtastic's NODE_NONE placeholder: a hop whose node
            # ID was not recorded (typically the local gateway or an unadvertised
            # relay). Label it instead of dumping the raw number.
            if key == 0xFFFFFFFF:
                return "unknown"
            return num_to_name.get(key, str(num))

        def _path(route, snrs) -> str:
            if not route:
                return "—"
            parts = []
            for i, hop in enumerate(route):
                label = _name(hop)
                if i < len(snrs) and snrs[i] is not None:
                    label += f" ({snrs[i]:.1f}dB)"
                parts.append(label)
            return " → ".join(parts)

        lines = ["🔍 *Traceroute*"]
        if record.get("route"):
            lines.append(f"To:   {_path(record['route'], record.get('snr_towards', []))}")
        if record.get("route_back"):
            lines.append(f"Back: {_path(record['route_back'], record.get('snr_back', []))}")
        if len(lines) == 1:
            lines.append("No route discovered yet.")
        return "\n".join(lines)

    async def _send_text(self, text: str) -> int | None:
        """
        Send a Telegram text message and return its message_id (or None).

        The message_id is used to route native Telegram replies back to the
        correct mesh channel / DM node.
        """
        try:
            # Use the current chat_id (from the incoming message) or fall back
            # to the configured chat_id / first authorized chat.
            target_chat_id = self._current_chat_id or self._default_forward_chat
            result = await self._api_call("sendMessage", {
                "chat_id": target_chat_id,
                "text": text,
                "parse_mode": "Markdown"
            })
            if result.get("ok"):
                return result.get("result", {}).get("message_id")
            # Telegram rejected the Markdown (most likely unparseable entities).
            # Retry as plain text so the user still receives a response instead
            # of a silent failure.
            logger.warning(
                "Telegram Markdown send rejected (%s); retrying as plain text",
                result,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to send Telegram response to chat %s: %s", target_chat_id, exc)
            return None
        try:
            result = await self._api_call("sendMessage", {
                "chat_id": target_chat_id,
                "text": text
            })
            if result.get("ok"):
                return result.get("result", {}).get("message_id")
            logger.error("Telegram sendMessage rejected message: %s", result)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to send Telegram response to chat %s: %s", target_chat_id, exc)
        return None

    async def _send_document(self, content: str, filename: str) -> bool:
        """Send a text file (e.g. a GPX track) as a Telegram document.

        Returns True if Telegram accepted the upload, False otherwise. Uses
        multipart form-data so the file is attached rather than pasted as text.
        """
        try:
            from aiohttp import FormData
            target_chat_id = self._current_chat_id or self._default_forward_chat
            if not target_chat_id:
                return False
            data = FormData()
            data.add_field("chat_id", target_chat_id)
            data.add_field(
                "document",
                content.encode("utf-8"),
                filename=filename,
                content_type="application/gpx+xml",
            )
            url = f"https://api.telegram.org/bot{self.token}/sendDocument"
            async with self._session.post(url, data=data) as resp:
                result = await resp.json()
            if result.get("ok"):
                return True
            logger.error("Telegram sendDocument rejected: %s", result)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to send Telegram document: %s", exc)
        return False

    @staticmethod
    def _build_gpx(name: str, node_id: str, trail: list[dict]) -> str:
        """Render a node's position history as a GPX 1.1 track file.

        ``trail`` is a list of dicts with ``lat``/``lng`` (required), and
        optional ``alt``/``timestamp``. Names are XML-escaped defensively.
        """
        esc = (name or node_id).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        pts = []
        for p in trail:
            lat = p.get("lat")
            lng = p.get("lng")
            if lat is None or lng is None:
                continue
            ele = p.get("alt")
            ele_str = f"      <ele>{ele}</ele>\n" if ele is not None else ""
            ts = p.get("timestamp")
            time_str = ""
            if ts:
                try:
                    time_str = (
                        "      <time>"
                        + datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")
                        + "</time>\n"
                    )
                except (TypeError, ValueError):
                    time_str = ""
            pts.append(
                f'    <trkpt lat="{lat}" lon="{lng}">\n{ele_str}{time_str}    </trkpt>'
            )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<gpx version="1.1" creator="NodePulse" '
            'xmlns="http://www.topografix.com/GPX/1/1">\n'
            f"  <trk>\n    <name>{esc}</name>\n    <trkseg>\n"
            + "\n".join(pts)
            + "\n    </trkseg>\n  </trk>\n</gpx>"
        )

    def forward_mesh_message(self, entry: dict[str, Any]) -> None:
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
        text = entry.get("text", "")
        snr = entry.get("rx_snr")

        snr_str = f" [SNR: {snr}]" if snr is not None else ""
        ch_str = "[DM]" if is_dm else f"[Ch {channel}]"
        id_str = f" ({from_id})" if from_id else ""

        # Resolve a human-readable sender label, prioritising the short name
        # (e.g. "Bob") and appending the long name when it adds clarity, so the
        # user can tell at a glance who a message came from.
        short = entry.get("from_short") or ""
        long = entry.get("from_long") or ""
        if short and long and short != long:
            label = f"{short} ({long})"
        elif short:
            label = short
        elif long:
            label = long
        else:
            label = entry.get("from_name", "Unknown")

        # Escape markdown formatting characters in the user's name
        safe_name = label.replace("*", "").replace("_", "").replace("`", "")

        msg = f"📩 {ch_str} *{safe_name}*{id_str}{snr_str}:\n{text}"

        # Schedule the send coroutine onto the event loop from this non-async
        # thread.  call_soon_threadsafe is the thread-safe way to submit work
        # to a running asyncio loop; create_task would silently fail or attach
        # to the wrong loop when called from outside the loop thread.
        if not self._task.done():
            metadata = {
                "is_dm": is_dm,
                "channel": channel,
                "node": from_id,
            }
            self._loop.call_soon_threadsafe(
                lambda m=msg, meta=metadata: asyncio.ensure_future(
                    self._send_forward(m, meta), loop=self._loop
                )
            )

    async def _send_forward(self, msg: str, metadata: dict[str, Any]) -> None:
        """Send a forwarded mesh message and record its message_id so native
        Telegram replies can be routed back to the originating channel/node."""
        message_id = await self._send_text(msg)
        if not message_id:
            return
        with self._forward_lock:
            self._forwarded[message_id] = dict(metadata, ts=time.time())
            # Cap the map so it can't grow unbounded over a long uptime.
            if len(self._forwarded) > 500:
                cutoff = time.time() - 24 * 3600
                self._forwarded = {
                    mid: meta for mid, meta in self._forwarded.items()
                    if meta.get("ts", 0) >= cutoff
                }
