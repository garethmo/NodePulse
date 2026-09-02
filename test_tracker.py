try:
    from homeassistant.components.device_tracker.config_entry import TrackerEntity
    import inspect

    src = inspect.getsource(TrackerEntity.state)
    print(src)
except Exception as e:
    print("Error:", e)
