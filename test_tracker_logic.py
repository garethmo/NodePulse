class ZoneState:
    def __init__(self, name):
        self.name = name
        self.entity_id = "zone." + name.lower()

class DummyTracker:
    def __init__(self, lat, lon):
        self._lat = lat
        self._lon = lon
        self.location_name = None
        self.__in_zones = None
        self.__active_zone = None
    
    @property
    def latitude(self): return self._lat
    @property
    def longitude(self): return self._lon

    @property
    def state(self):
        if self.location_name is not None:
            return self.location_name
        if (self.latitude is not None and self.longitude is not None) or self.__in_zones is not None:
            zone_state = self.__active_zone
            if zone_state is None:
                state = "not_home"
            elif zone_state.entity_id == "zone.home":
                state = "home"
            else:
                state = zone_state.name
            return state
        return None

t1 = DummyTracker(1.0, 1.0)
print("t1 state:", t1.state)

t2 = DummyTracker(None, None)
print("t2 state:", t2.state)
