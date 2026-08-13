import re

with open("nodepulse-addon/app/connection.py", "r") as f:
    content = f.read()

# Add schedule_message method
schedule_method = """
    def schedule_message(self, execute_time: float, destination: str, text: str, channel: int) -> None:
        with self._scheduled_messages_lock:
            self._scheduled_messages.append((execute_time, destination, text, channel, 0))
        self._schedule_save(self._scheduled_messages, "/data/scheduled_messages.json")
"""
content = re.sub(r'(\s*def _process_scheduled_messages)', schedule_method + r'\1', content)

# Add load/save for scheduled_messages in _load_messages
load_scheduled = """
        try:
            if os.path.exists("/data/scheduled_messages.json"):
                with self._persist_lock:
                    with open("/data/scheduled_messages.json", "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                if isinstance(data, list):
                    with self._scheduled_messages_lock:
                        self._scheduled_messages = [tuple(x) for x in data]
        except Exception as exc:
            logger.debug("Could not load persisted scheduled messages (ignored): %s", exc)
"""
content = re.sub(r'(def _load_messages\(self\) -> None:.*?try:.*?except Exception as exc:.*?logger\.debug\("Could not load persisted messages.*?)(def _save_messages)', r'\1' + load_scheduled + r'\n    \2', content, flags=re.DOTALL)

with open("nodepulse-addon/app/connection.py", "w") as f:
    f.write(content)
print("connection.py patched")
