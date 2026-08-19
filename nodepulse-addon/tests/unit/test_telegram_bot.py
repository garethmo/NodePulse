"""
Unit tests for app/telegram_bot.py
"""
import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.telegram_bot import TelegramBot


def make_mock_config(**kwargs):
    """Create a mock config with all required TelegramBot attributes."""
    defaults = {
        "telegram_enabled": True,
        "telegram_bot_token": "test_token",
        "telegram_chat_id": "12345",
        "telegram_allow_commands": True,
        "telegram_authorized_chat_ids": ["12345"],
        "telegram_forward_channels": [0],
        "telegram_forward_dms": False,
    }
    defaults.update(kwargs)
    mock_config = Mock()
    for k, v in defaults.items():
        setattr(mock_config, k, v)
    return mock_config


class TestTelegramBotInit:
    def test_init_disabled(self):
        mock_config = make_mock_config(telegram_enabled=False, telegram_bot_token="", telegram_chat_id="")
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        assert bot.enabled is False
        assert bot.token == ""
        assert bot.chat_id == ""

    def test_init_with_config(self):
        mock_config = make_mock_config(
            telegram_bot_token="test_token",
            telegram_chat_id="12345",
            telegram_allow_commands=True,
            telegram_authorized_chat_ids=["12345", "67890"],
            telegram_forward_channels=[0, 1],
            telegram_forward_dms=True,
        )
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        assert bot.enabled is True
        assert bot.token == "test_token"
        assert bot.chat_id == "12345"
        assert bot.allow_commands is True
        assert "12345" in bot.authorized_chat_ids
        assert "67890" in bot.authorized_chat_ids
        assert bot.forward_channels == [0, 1]
        assert bot.forward_dms is True

    def test_init_normalizes_chat_ids(self):
        mock_config = make_mock_config(
            telegram_chat_id=12345,
            telegram_authorized_chat_ids=[67890, "11111"],
        )
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        assert "12345" in bot.authorized_chat_ids
        assert "67890" in bot.authorized_chat_ids
        assert "11111" in bot.authorized_chat_ids


class TestTelegramBotStartStop:
    @pytest.mark.asyncio
    async def test_start_disabled(self):
        mock_config = make_mock_config(telegram_enabled=False)
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        await bot.start()
        assert bot._task is None

    @pytest.mark.asyncio
    async def test_start_no_token(self):
        mock_config = make_mock_config(telegram_bot_token="")
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        await bot.start()
        assert bot._task is None

    @pytest.mark.asyncio
    async def test_start_no_authorized_chats(self):
        mock_config = make_mock_config(telegram_chat_id="", telegram_authorized_chat_ids=[])
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        await bot.start()
        assert bot._task is None

    @pytest.mark.asyncio
    async def test_start_creates_session_and_task(self):
        mock_config = make_mock_config()
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        with patch("app.telegram_bot.aiohttp.ClientSession") as mock_session_class:
            mock_session = AsyncMock()
            mock_session_class.return_value = mock_session
            await bot.start()
            assert bot._session is not None
            assert bot._task is not None
            mock_session_class.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_cancels_task_and_closes_session(self):
        mock_config = make_mock_config()
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        async def dummy_task():
            await asyncio.sleep(100)
        bot._task = asyncio.create_task(dummy_task())
        bot._session = AsyncMock()
        await bot.stop()
        assert bot._task.cancelled()
        bot._session.close.assert_called_once()


class TestTelegramBotApiCall:
    @pytest.mark.asyncio
    async def test_api_call_no_session(self):
        bot = TelegramBot(make_mock_config(), Mock(), Mock(), Mock(), Mock())
        bot._session = None
        result = await bot._api_call("test_method")
        assert result == {}

    @pytest.mark.asyncio
    async def test_api_call_success(self):
        bot = TelegramBot(make_mock_config(), Mock(), Mock(), Mock(), Mock())
        mock_session = Mock()
        mock_response = AsyncMock()
        mock_response.raise_for_status = Mock()
        mock_response.json = AsyncMock(return_value={"ok": True, "result": {"test": "data"}})
        
        # Create a mock context manager for the post request
        mock_post_cm = Mock()
        mock_post_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_post_cm.__aexit__ = AsyncMock(return_value=None)
        mock_session.post = Mock(return_value=mock_post_cm)
        
        bot._session = mock_session
        bot.token = "test_token"
        result = await bot._api_call("sendMessage", {"chat_id": "123", "text": "test"})
        assert result == {"ok": True, "result": {"test": "data"}}
        mock_session.post.assert_called_once()


class TestTelegramBotHandleMessage:
    @pytest.mark.asyncio
    async def test_handle_message_unauthorized_chat(self):
        mock_config = make_mock_config()
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        bot._send_text = AsyncMock()
        await bot._handle_message({"chat": {"id": 99999, "type": "private"}, "text": "hello"})
        bot._send_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_message_empty_text(self):
        mock_config = make_mock_config()
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        bot._send_text = AsyncMock()
        await bot._handle_message({"chat": {"id": 12345, "type": "private"}, "text": "  "})
        bot._send_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_message_command(self):
        mock_config = make_mock_config()
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        bot._handle_command = AsyncMock()
        await bot._handle_message({"chat": {"id": 12345, "type": "private"}, "text": "/status"})
        bot._handle_command.assert_called_once_with("/status")

    @pytest.mark.asyncio
    async def test_handle_message_command_disabled(self):
        mock_config = make_mock_config(telegram_allow_commands=False)
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        bot._send_text = AsyncMock()
        await bot._handle_message({"chat": {"id": 12345, "type": "private"}, "text": "/status"})
        bot._send_text.assert_called_once_with("Commands are disabled in config.")

    @pytest.mark.asyncio
    async def test_handle_message_reply_to_forwarded_dm(self):
        mock_config = make_mock_config(telegram_forward_dms=True)
        bot = TelegramBot(mock_config, AsyncMock(), Mock(), Mock(), Mock())
        bot._send_text = AsyncMock()
        bot.send_message_callback = AsyncMock(return_value=True)
        bot._forwarded = {123: {"is_dm": True, "node": "!abcdef"}}
        await bot._handle_message({
            "chat": {"id": 12345, "type": "private"},
            "text": "reply text",
            "reply_to_message": {"message_id": 123}
        })
        bot.send_message_callback.assert_called_once_with("reply text", destination="!abcdef")
        bot._send_text.assert_called_with("✅ Reply sent as DM to !abcdef.")

    @pytest.mark.asyncio
    async def test_handle_message_reply_to_forwarded_channel(self):
        mock_config = make_mock_config(telegram_forward_channels=[1])
        bot = TelegramBot(mock_config, AsyncMock(), Mock(), Mock(), Mock())
        bot._send_text = AsyncMock()
        bot.send_message_callback = AsyncMock(return_value=True)
        bot._forwarded = {456: {"is_dm": False, "channel": 1}}
        await bot._handle_message({
            "chat": {"id": 12345, "type": "private"},
            "text": "reply text",
            "reply_to_message": {"message_id": 456}
        })
        bot.send_message_callback.assert_called_once_with("reply text", channel=1)
        bot._send_text.assert_called_with("✅ Reply sent to Channel 1.")

    @pytest.mark.asyncio
    async def test_handle_message_plain_text_broadcast(self):
        mock_config = make_mock_config()
        bot = TelegramBot(mock_config, AsyncMock(), Mock(), Mock(), Mock())
        bot._send_text = AsyncMock()
        bot.send_message_callback = AsyncMock(return_value=True)
        await bot._handle_message({
            "chat": {"id": 12345, "type": "private"},
            "text": "hello mesh",
            "from": {"first_name": "Alice"}
        })
        bot.send_message_callback.assert_called_once_with("[Alice] hello mesh", channel=0)
        bot._send_text.assert_called_with("✅ Message sent to mesh.")


class TestTelegramBotHandleCommand:
    @pytest.mark.asyncio
    async def test_handle_command_status(self):
        mock_config = make_mock_config()
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        bot._send_text = AsyncMock()
        bot.get_status_callback = AsyncMock(return_value={"my_info": {"battery_level": 80, "macaddr": "AA:BB:CC"}})
        bot.get_nodes_callback = AsyncMock(return_value=[{"id": "!123"}, {"id": "!456"}])
        await bot._handle_command("/status")
        bot._send_text.assert_called_once()
        args = bot._send_text.call_args[0][0]
        assert "NodePulse Status" in args
        assert "Nodes: 2" in args
        

    @pytest.mark.asyncio
    async def test_handle_command_nodes(self):
        mock_config = make_mock_config()
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        bot._send_text = AsyncMock()
        bot.get_nodes_callback = AsyncMock(return_value=[
            {"short_name": "Node1", "snr": 5.0, "last_heard": 1000},
            {"short_name": "Node2", "snr": 3.0, "last_heard": 2000},
        ])
        await bot._handle_command("/nodes")
        bot._send_text.assert_called_once()
        args = bot._send_text.call_args[0][0]
        assert "Visible Mesh Nodes" in args
        assert "Node1" in args
        assert "Node2" in args

    @pytest.mark.asyncio
    async def test_handle_command_channels(self):
        mock_config = make_mock_config(telegram_forward_channels=[0])
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        bot._send_text = AsyncMock()
        bot.get_channels_callback = AsyncMock(return_value=[
            {"index": 0, "name": "Primary", "role": "PRIMARY"},
            {"index": 1, "name": "Secondary", "role": "SECONDARY"},
        ])
        await bot._handle_command("/channels")
        bot._send_text.assert_called_once()
        args = bot._send_text.call_args[0][0]
        assert "Configured Channels" in args
        assert "Primary" in args
        assert "Secondary" in args

    @pytest.mark.asyncio
    async def test_handle_command_send_with_channel(self):
        mock_config = make_mock_config(telegram_forward_channels=[0, 1])
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        bot._send_text = AsyncMock()
        bot.send_message_callback = AsyncMock(return_value=True)
        await bot._handle_command("/send #1 hello world")
        bot.send_message_callback.assert_called_once_with("hello world", channel=1)
        bot._send_text.assert_called_with("✅ Message sent to Channel 1.")

    @pytest.mark.asyncio
    async def test_handle_command_send_invalid_channel(self):
        mock_config = make_mock_config(telegram_forward_channels=[0, 1])
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        bot._send_text = AsyncMock()
        await bot._handle_command("/send #20 hello")
        bot._send_text.assert_called_with("❌ Invalid channel 20. Use 0-15.")

    @pytest.mark.asyncio
    async def test_handle_command_dm(self):
        mock_config = make_mock_config(telegram_forward_dms=True)
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        bot._send_text = AsyncMock()
        bot.send_message_callback = AsyncMock(return_value=True)
        await bot._handle_command("/dm !abcdef hello")
        bot.send_message_callback.assert_called_once_with("hello", destination="!abcdef")
        bot._send_text.assert_called_with("✅ DM sent to !abcdef.")

    @pytest.mark.asyncio
    async def test_handle_command_help(self):
        mock_config = make_mock_config()
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        bot._send_text = AsyncMock()
        await bot._handle_command("/help")
        bot._send_text.assert_called_once()
        args = bot._send_text.call_args[0][0]
        assert "Commands:" in args
        assert "/status" in args
        assert "/nodes" in args
        assert "/channels" in args
        assert "/send" in args
        assert "/dm" in args

    @pytest.mark.asyncio
    async def test_handle_command_unknown(self):
        mock_config = make_mock_config()
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        bot._send_text = AsyncMock()
        await bot._handle_command("/unknown")
        bot._send_text.assert_called_with("Unknown command. Type /help.")


class TestTelegramBotSendText:
    @pytest.mark.asyncio
    async def test_send_text_uses_current_chat(self):
        mock_config = make_mock_config(telegram_authorized_chat_ids=["12345", "67890"])
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        bot._current_chat_id = "67890"
        bot._api_call = AsyncMock(return_value={"ok": True, "result": {"message_id": 123}})
        result = await bot._send_text("test message")
        assert result == 123
        bot._api_call.assert_called_once_with("sendMessage", {
            "chat_id": "67890",
            "text": "test message",
            "parse_mode": "Markdown"
        })

    @pytest.mark.asyncio
    async def test_send_text_falls_back_to_default(self):
        mock_config = make_mock_config()
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        bot._current_chat_id = None
        bot._api_call = AsyncMock(return_value={"ok": True, "result": {"message_id": 456}})
        result = await bot._send_text("test message")
        assert result == 456
        bot._api_call.assert_called_once_with("sendMessage", {
            "chat_id": "12345",
            "text": "test message",
            "parse_mode": "Markdown"
        })

    @pytest.mark.asyncio
    async def test_send_text_returns_none_on_error(self):
        mock_config = make_mock_config()
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        bot._api_call = AsyncMock(side_effect=Exception("API error"))
        result = await bot._send_text("test message")
        assert result is None


class TestTelegramBotForwardMeshMessage:
    def test_forward_mesh_message_disabled(self):
        mock_config = make_mock_config(telegram_enabled=False)
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        bot._task = Mock()
        bot._task.done = Mock(return_value=False)
        bot._session = Mock()
        bot._loop = Mock()
        bot.forward_mesh_message({"text": "test"})
        bot._loop.call_soon_threadsafe.assert_not_called()

    def test_forward_mesh_message_own_outgoing(self):
        mock_config = make_mock_config()
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        bot._task = Mock()
        bot._task.done = Mock(return_value=False)
        bot._session = Mock()
        bot._loop = Mock()
        bot.forward_mesh_message({"text": "test", "outgoing": True})
        bot._loop.call_soon_threadsafe.assert_not_called()

    def test_forward_mesh_message_dm_not_forwarded(self):
        mock_config = make_mock_config(telegram_forward_dms=False)
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        bot._task = Mock()
        bot._task.done = Mock(return_value=False)
        bot._session = Mock()
        bot._loop = Mock()
        bot.forward_mesh_message({"text": "test", "is_dm": True})
        bot._loop.call_soon_threadsafe.assert_not_called()

    def test_forward_mesh_message_channel_not_forwarded(self):
        mock_config = make_mock_config(telegram_forward_channels=[0])
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        bot._task = Mock()
        bot._task.done = Mock(return_value=False)
        bot._session = Mock()
        bot._loop = Mock()
        bot.forward_mesh_message({"text": "test", "channel": 5})
        bot._loop.call_soon_threadsafe.assert_not_called()

    def test_forward_mesh_message_schedules_coroutine(self):
        mock_config = make_mock_config(telegram_forward_channels=[0, 1], telegram_forward_dms=True)
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        bot._task = Mock()
        bot._task.done = Mock(return_value=False)
        bot._session = Mock()
        bot._loop = Mock()
        bot.forward_mesh_message({
            "text": "test",
            "is_dm": False,
            "channel": 1,
            "from_id": "!abcdef",
            "from_name": "TestNode",
            "rx_snr": 5.0
        })
        bot._loop.call_soon_threadsafe.assert_called_once()


class TestTelegramBotSendForward:
    @pytest.mark.asyncio
    async def test_send_forward_records_message_id(self):
        mock_config = make_mock_config()
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        bot._send_text = AsyncMock(return_value=123)
        await bot._send_forward("test message", {"is_dm": False, "channel": 0, "node": "!abcdef"})
        assert 123 in bot._forwarded
        assert bot._forwarded[123]["is_dm"] is False
        assert bot._forwarded[123]["channel"] == 0
        assert bot._forwarded[123]["node"] == "!abcdef"

    @pytest.mark.asyncio
    async def test_send_forward_caps_map_size(self):
        mock_config = make_mock_config()
        bot = TelegramBot(mock_config, Mock(), Mock(), Mock(), Mock())
        bot._send_text = AsyncMock(return_value=500)
        import time
        old_time = time.time() - 25 * 3600
        for i in range(500):
            bot._forwarded[i] = {"ts": old_time, "is_dm": True, "channel": 0}
        await bot._send_forward("new message", {"is_dm": False, "channel": 0})
        assert len(bot._forwarded) <= 500
        # New entry should be there
        assert any(v.get("is_dm") is False for v in bot._forwarded.values())


class TestTelegramBotReplyParsing:
    @pytest.mark.asyncio
    async def test_handle_message_reply_fallback_dm(self):
        mock_config = make_mock_config(telegram_forward_dms=True)
        bot = TelegramBot(mock_config, AsyncMock(), Mock(), Mock(), Mock())
        bot._send_text = AsyncMock()
        bot.send_message_callback = AsyncMock(return_value=True)
        bot._forwarded = {}
        await bot._handle_message({
            "chat": {"id": 12345, "type": "private"},
            "text": "reply to dm",
            "reply_to_message": {
                "message_id": 999,
                "text": "📩 [DM] *TestNode* (!abcdef12):\noriginal message"
            }
        })
        bot.send_message_callback.assert_called_once_with("reply to dm", destination="!abcdef12")

    @pytest.mark.asyncio
    async def test_handle_message_reply_fallback_channel(self):
        mock_config = make_mock_config(telegram_forward_channels=[1])
        bot = TelegramBot(mock_config, AsyncMock(), Mock(), Mock(), Mock())
        bot._send_text = AsyncMock()
        bot.send_message_callback = AsyncMock(return_value=True)
        bot._forwarded = {}
        await bot._handle_message({
            "chat": {"id": 12345, "type": "private"},
            "text": "reply to channel",
            "reply_to_message": {
                "message_id": 999,
                "text": "📩 [Ch 1] *TestNode*: original"
            }
        })
        bot.send_message_callback.assert_called_once_with("reply to channel", channel=1)