"""
Unit tests for app/main.py
"""
import asyncio
import pytest
from unittest.mock import Mock, patch, AsyncMock

from app import main


class TestOnStartup:
    @pytest.mark.asyncio
    async def test_on_startup_creates_tasks(self):
        mock_app = {
            "connection": Mock(),
            "mqtt_bridge": Mock(),
            "telegram_bot": Mock(),
        }
        mock_app["connection"].monitor_connection = AsyncMock()
        mock_app["connection"].run_channel_refresh_loop = AsyncMock()
        mock_app["connection"].expire_pending_acks = AsyncMock()
        mock_app["connection"]._process_scheduled_messages = Mock(return_value=[])
        mock_app["connection"].send_message = AsyncMock()
        
        mock_app["mqtt_bridge"].start = AsyncMock()
        mock_app["telegram_bot"].start = AsyncMock()

        with patch("app.main.asyncio.create_task") as mock_create_task:
            await main._on_startup(mock_app)
            assert mock_create_task.call_count == 4

    @pytest.mark.asyncio
    async def test_on_startup_mqtt_bridge_start(self):
        mock_app = {
            "connection": Mock(),
            "mqtt_bridge": Mock(),
            "telegram_bot": Mock(),
        }
        mock_app["connection"].monitor_connection = AsyncMock()
        mock_app["connection"].run_channel_refresh_loop = AsyncMock()
        mock_app["connection"].expire_pending_acks = AsyncMock()
        mock_app["connection"]._process_scheduled_messages = Mock(return_value=[])
        mock_app["connection"].send_message = AsyncMock()
        
        mock_app["mqtt_bridge"].start = AsyncMock()
        mock_app["telegram_bot"].start = AsyncMock()

        await main._on_startup(mock_app)
        mock_app["mqtt_bridge"].start.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_startup_telegram_bot_start(self):
        mock_app = {
            "connection": Mock(),
            "mqtt_bridge": Mock(),
            "telegram_bot": Mock(),
        }
        mock_app["connection"].monitor_connection = AsyncMock()
        mock_app["connection"].run_channel_refresh_loop = AsyncMock()
        mock_app["connection"].expire_pending_acks = AsyncMock()
        mock_app["connection"]._process_scheduled_messages = Mock(return_value=[])
        mock_app["connection"].send_message = AsyncMock()
        
        mock_app["mqtt_bridge"].start = AsyncMock()
        mock_app["telegram_bot"].start = AsyncMock()

        await main._on_startup(mock_app)
        mock_app["telegram_bot"].start.assert_called_once()


class TestOnShutdown:
    @pytest.mark.asyncio
    async def test_on_shutdown_cancels_tasks(self):
        # Create real async tasks that can be awaited
        async def dummy_task():
            await asyncio.sleep(100)  # Long sleep so we can cancel it
        
        mock_monitor_task = asyncio.create_task(dummy_task())
        mock_channel_task = asyncio.create_task(dummy_task())
        mock_ack_task = asyncio.create_task(dummy_task())
        
        mock_mqtt_bridge = Mock()
        mock_mqtt_bridge.stop = AsyncMock()
        mock_telegram_bot = Mock()
        mock_telegram_bot.stop = AsyncMock()
        mock_connection = Mock()
        mock_connection.disconnect = AsyncMock()
        
        mock_app = {
            "monitor_task": mock_monitor_task,
            "channel_refresh_task": mock_channel_task,
            "ack_expiry_task": mock_ack_task,
            "mqtt_bridge": mock_mqtt_bridge,
            "telegram_bot": mock_telegram_bot,
            "connection": mock_connection,
        }
        
        await main._on_shutdown(mock_app)
        
        mock_mqtt_bridge.stop.assert_called_once()
        mock_telegram_bot.stop.assert_called_once()
        mock_connection.disconnect.assert_called_once()
        
        # Tasks should be cancelled
        assert mock_monitor_task.cancelled()
        assert mock_channel_task.cancelled()
        assert mock_ack_task.cancelled()

    @pytest.mark.asyncio
    async def test_on_shutdown_handles_done_tasks(self):
        mock_monitor_task = Mock()
        mock_monitor_task.done = Mock(return_value=True)
        mock_monitor_task.cancel = Mock()
        
        mock_app = {
            "monitor_task": mock_monitor_task,
            "channel_refresh_task": None,
            "ack_expiry_task": None,
            "mqtt_bridge": None,
            "telegram_bot": None,
            "connection": Mock(),
        }
        
        mock_app["connection"].disconnect = AsyncMock()
        
        await main._on_shutdown(mock_app)
        mock_monitor_task.cancel.assert_not_called()


class TestBuildApp:
    @patch("app.main.MeshtasticConnection")
    @patch("app.main.MqttBridge")
    @patch("app.main.TelegramBot")
    @patch("app.main.resolve_target")
    @patch("app.main.web.Application")
    @patch("app.main.Path.is_dir")
    @patch("app.main.aiohttp_cors.setup")
    def test_build_app_creates_routes(self, mock_cors_setup, mock_is_dir, mock_app_class, mock_resolve_target, mock_telegram_bot, mock_mqtt_bridge, mock_meshtastic_conn):
        mock_config = Mock()
        mock_config.access_key = "test_key"
        mock_config.ignored_nodes = []
        mock_config.log_level = "INFO"
        mock_config.telegram_enabled = False
        
        mock_resolve_target.return_value = ("localhost", 4403, "tcp")
        mock_meshtastic_conn.return_value = Mock()
        mock_mqtt_bridge.return_value = Mock()
        mock_telegram_bot.return_value = Mock()
        
        mock_is_dir.return_value = False
        
        mock_app = Mock()
        mock_app.router = Mock()
        mock_app.router.add_get = Mock()
        mock_app.router.add_post = Mock()
        mock_app.router.add_put = Mock()
        mock_app.router.add_delete = Mock()
        mock_app.router.add_patch = Mock()
        mock_app.router.routes = Mock(return_value=[])
        mock_app.on_startup = []
        mock_app.on_shutdown = []
        mock_app.__setitem__ = Mock()
        mock_app.__getitem__ = Mock(side_effect=lambda k: {
            "connection": mock_meshtastic_conn.return_value,
            "mqtt_bridge": mock_mqtt_bridge.return_value,
            "telegram_bot": mock_telegram_bot.return_value,
            "config": mock_config,
            "ignored_nodes": set(),
        }.get(k))
        mock_app_class.return_value = mock_app
        
        mock_cors = Mock()
        mock_cors.add = Mock()
        mock_cors_setup.return_value = mock_cors
        
        result = main.build_app(mock_config)

        assert result == mock_app
        assert mock_app.__setitem__.call_count >= 4

    @patch("app.main.MeshtasticConnection")
    @patch("app.main.MqttBridge")
    @patch("app.main.TelegramBot")
    @patch("app.main.resolve_target")
    @patch("app.main.web.Application")
    @patch("app.main.Path.is_dir")
    @patch("app.main.aiohttp_cors.setup")
    def test_build_app_registers_api_routes(self, mock_cors_setup, mock_is_dir, mock_app_class, mock_resolve_target, mock_telegram_bot, mock_mqtt_bridge, mock_meshtastic_conn):
        mock_config = Mock()
        mock_config.access_key = "test_key"
        mock_config.ignored_nodes = []
        mock_config.log_level = "INFO"
        mock_config.telegram_enabled = False
        
        mock_resolve_target.return_value = ("localhost", 4403, "tcp")
        mock_meshtastic_conn.return_value = Mock()
        mock_mqtt_bridge.return_value = Mock()
        mock_telegram_bot.return_value = Mock()
        
        mock_is_dir.return_value = False
        
        mock_app = Mock()
        mock_app.router = Mock()
        mock_app.router.add_get = Mock()
        mock_app.router.add_post = Mock()
        mock_app.router.add_put = Mock()
        mock_app.router.add_delete = Mock()
        mock_app.router.add_patch = Mock()
        mock_app.router.routes = Mock(return_value=[])
        mock_app.on_startup = []
        mock_app.on_shutdown = []
        mock_app.__setitem__ = Mock()
        mock_app.__getitem__ = Mock(side_effect=lambda k: {
            "connection": mock_meshtastic_conn.return_value,
            "mqtt_bridge": mock_mqtt_bridge.return_value,
            "telegram_bot": mock_telegram_bot.return_value,
            "config": mock_config,
            "ignored_nodes": set(),
        }.get(k))
        mock_app_class.return_value = mock_app
        
        mock_cors = Mock()
        mock_cors.add = Mock()
        mock_cors_setup.return_value = mock_cors
        
        main.build_app(mock_config)

        # Check that key routes are registered
        add_get_calls = [call[0][0] for call in mock_app.router.add_get.call_args_list]
        add_post_calls = [call[0][0] for call in mock_app.router.add_post.call_args_list]
        add_put_calls = [call[0][0] for call in mock_app.router.add_put.call_args_list]

        assert "/api/status" in add_get_calls
        assert "/api/nodes" in add_get_calls
        assert "/api/send" in add_post_calls
        assert "/api/tags" in add_put_calls
        assert "/api/favorites" in add_get_calls
        assert "/api/favorites" in add_put_calls
        assert "/api/device-config" in add_get_calls


class TestServeIndex:
    @pytest.mark.asyncio
    async def test_serve_index_returns_file_response(self):
        mock_request = Mock()
        
        with patch("app.main.web.FileResponse") as mock_file_response:
            mock_file_response.return_value = Mock()
            await main._serve_index(mock_request)
            mock_file_response.assert_called_once()


class TestMain:
    @patch("app.main.load_config")
    @patch("app.main.build_app")
    @patch("app.main.web.run_app")
    def test_main_runs_app(self, mock_run_app, mock_build_app, mock_load_config):
        mock_config = Mock()
        mock_config.log_level = "INFO"
        mock_load_config.return_value = mock_config
        mock_build_app.return_value = Mock()

        main.main()

        mock_load_config.assert_called_once()
        mock_build_app.assert_called_once_with(mock_config)
        mock_run_app.assert_called_once()

    @patch("app.main.load_config")
    @patch("app.main.build_app")
    @patch("app.main.web.run_app")
    def test_main_sets_log_level(self, mock_run_app, mock_build_app, mock_load_config):
        mock_config = Mock()
        mock_config.log_level = "DEBUG"
        mock_load_config.return_value = mock_config
        mock_build_app.return_value = Mock()

        with patch("app.main.logging.getLogger") as mock_get_logger:
            mock_root_logger = Mock()
            mock_get_logger.return_value = mock_root_logger
            
            main.main()
            
            mock_root_logger.setLevel.assert_called_once()


class TestStaticDir:
    def test_static_dir_path(self):
        assert main._STATIC_DIR.name == "web_ui"
        assert "nodepulse-addon" in str(main._STATIC_DIR)