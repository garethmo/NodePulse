"""
NodePulse Addon — Terrain Link Analysis.

Provides elevation lookups (from a free DEM web API, cached in memory) and
point-to-point radio link analysis: terrain profile, line-of-sight (LOS),
first-Fresnel-zone clearance, and a simple free-space link budget.

Math model (all documented so the numbers are auditable):

  * Earth curvature bulge at a point along the path (effective earth radius
    model, k = 4/3):  bulge(m) = d1*d2 / (2*k*R)  with d1,d2 in metres and
    R = 6 371 000 m. This is the standard radio-planning approximation that
    accounts for standard atmospheric refraction.

  * First Fresnel zone radius at the same point:
        r1(m) = sqrt( (λ * d1 * d2) / (d1 + d2) )
    with λ = c/f.

  * Free-space path loss: FSPL(dB) = 20*log10(d) + 20*log10(f) + 20*log10(4π/c)
    (d in metres, f in Hz).

  * LOS verdict: the path is LOS-clear when the radio beam stays above the
    terrain (elevation + earth bulge) at every sample. The Fresnel clearance
    ratio at each point is (beam_height - terrain_block) / r1; a path is
    "Fresnel-clear" when that ratio >= 1 at every sample (60% is often used
    for planning, we report both).

  * Clutter height: adds a fixed elevation offset to all ground points to
    model trees and buildings (meters). Useful for suburban/urban predictions.

Elevation source: OpenTopoData (https://www.opentopodata.org) SRTM30m by
default — free, no API key, supports batch lookups and returns JSON. The URL
is configurable (terrain_dem_url) so installs behind a restrictive network can
point at a compatible endpoint or a self-hosted proxy.
"""
import asyncio
import logging
import math
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

_EARTH_RADIUS_M = 6_371_000.0
_SPEED_OF_LIGHT_M_S = 299_792_458.0
_DEFAULT_K_FACTOR = 4 / 3

# OpenTopoData demo endpoint — free, no key, SRTM30m resolution.
_DEFAULT_DEM_URL = "https://api.opentopodata.org/v1/srtm30m"
# Per-request timeout — terrain analysis is best-effort; don't hang the UI.
_DEFAULT_TIMEOUT_S = 10.0

# Cap on the number of sample points fetched per path; finer sampling is
# pointless (SRTM30 is ~30 m cells) and just wastes DEM quota.
_MAX_SAMPLES = 128
# In-memory elevation cache size (points).
_MAX_CACHE = 2048


# ---------------------------------------------------------------------------
# Pure geometry / radio math (unit-testable without network)
# ---------------------------------------------------------------------------

def great_circle_distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in metres (haversine)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlng / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


def intermediate_point(lat1: float, lng1: float, lat2: float, lng2: float, frac: float):
    """Point at fraction `frac` (0..1) of the great-circle path.

    Returns (lat, lng) in degrees. Uses spherical linear interpolation which
    is exact for great circles and keeps the path geometrically correct for
    long links (unlike naive lat/lng lerp).
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    lam1, lam2 = math.radians(lng1), math.radians(lng2)
    d = great_circle_distance_m(lat1, lng1, lat2, lng2) / _EARTH_RADIUS_M

    if d == 0:
        return lat1, lng1

    a = math.sin((1 - frac) * d) / math.sin(d)
    b = math.sin(frac * d) / math.sin(d)
    x = a * math.cos(phi1) * math.cos(lam1) + b * math.cos(phi2) * math.cos(lam2)
    y = a * math.cos(phi1) * math.sin(lam1) + b * math.cos(phi2) * math.sin(lam2)
    z = a * math.sin(phi1) + b * math.sin(phi2)
    lat = math.degrees(math.atan2(z, math.sqrt(x * x + y * y)))
    lng = math.degrees(math.atan2(y, x))
    return lat, lng

def destination_point(lat: float, lng: float, distance_m: float, bearing_deg: float) -> tuple[float, float]:
    """Calculate the destination point given distance and bearing from start point."""
    R = _EARTH_RADIUS_M
    d = distance_m / R
    theta = math.radians(bearing_deg)
    phi1 = math.radians(lat)
    lam1 = math.radians(lng)

    phi2 = math.asin(math.sin(phi1)*math.cos(d) + math.cos(phi1)*math.sin(d)*math.cos(theta))
    lam2 = lam1 + math.atan2(math.sin(theta)*math.sin(d)*math.cos(phi1), math.cos(d)-math.sin(phi1)*math.sin(phi2))

    return math.degrees(phi2), math.degrees(lam2)


def wavelength_m(freq_mhz: float) -> float:
    """Wavelength in metres for a frequency in MHz."""
    return _SPEED_OF_LIGHT_M_S / (freq_mhz * 1e6)


def earth_bulge_m(d1_m: float, d2_m: float, k_factor: float = _DEFAULT_K_FACTOR) -> float:
    """Earth-curvature bulge at a point d1 from the Tx, d2 from the Rx."""
    return (d1_m * d2_m) / (2 * k_factor * _EARTH_RADIUS_M)


def fresnel_radius_m(d1_m: float, d2_m: float, freq_mhz: float, n: int = 1) -> float:
    """Radius of the n-th Fresnel zone at a point d1 from Tx, d2 from Rx."""
    lam = wavelength_m(freq_mhz)
    if d1_m + d2_m == 0:
        return 0.0
    return math.sqrt((n * lam * d1_m * d2_m) / (d1_m + d2_m))


def free_space_path_loss_db(distance_m: float, freq_mhz: float) -> float:
    """Free-space path loss in dB between two isotropic antennas."""
    if distance_m <= 0:
        return 0.0
    return 20 * math.log10(distance_m) + 20 * math.log10(freq_mhz * 1e6) + 20 * math.log10(
        4 * math.pi / _SPEED_OF_LIGHT_M_S
    )


def compute_link_budget(
    distance_m: float,
    freq_mhz: float,
    tx_power_dbm: float,
    tx_gain_dbi: float = 0.0,
    rx_gain_dbi: float = 0.0,
    cable_loss_db: float = 0.0,
) -> dict[str, float]:
    """Compute a simple free-space link budget.

    Returns EIRP, free-space path loss, received power and fade margin given
    the receiver sensitivity separately (margin = Rx - sensitivity).
    """
    eirp_dbm = tx_power_dbm + tx_gain_dbi - cable_loss_db
    fspl_db = free_space_path_loss_db(distance_m, freq_mhz)
    rx_power_dbm = eirp_dbm + rx_gain_dbi - fspl_db
    return {
        "distance_m": distance_m,
        "eirp_dbm": eirp_dbm,
        "fspl_db": fspl_db,
        "rx_power_dbm": rx_power_dbm,
    }


def analyze_link(
    from_point: dict[str, Any],
    to_point: dict[str, Any],
    freq_mhz: float,
    elevations: list[float],
    tx_power_dbm: float = 0.0,
    tx_gain_dbi: float = 0.0,
    rx_gain_dbi: float = 0.0,
    rx_sensitivity_dbm: float = -137.0,
    cable_loss_db: float = 0.0,
    tx_antenna_height_m: float = 2.0,
    rx_antenna_height_m: float = 2.0,
    k_factor: float = _DEFAULT_K_FACTOR,
    clutter_height_m: float = 0.0,
) -> dict[str, Any]:
    """Analyse a point-to-point link from sampled terrain elevations.

    `elevations` must be the DEM ground elevation (m) at each sample along the
    path, inclusive of both endpoints. Antenna heights are added on top of the
    ground at the two ends. `clutter_height_m` is an additional elevation
    offset (meters) added to all ground points to model trees and buildings.

    Returns a dict with the per-point geometry (distance from Tx, ground
    elevation, beam height, earth bulge, Fresnel radius, clearance ratio) plus
    verdicts and the link budget.
    """
    lat1, lng1 = from_point["lat"], from_point["lng"]
    lat2, lng2 = to_point["lat"], to_point["lng"]
    total_m = great_circle_distance_m(lat1, lng1, lat2, lng2)

    n = len(elevations)
    if n < 2:
        raise ValueError("analyze_link requires at least 2 elevation samples")

    ground_tx = elevations[0] + clutter_height_m
    ground_rx = elevations[-1] + clutter_height_m
    beam_tx_m = ground_tx + tx_antenna_height_m
    beam_rx_m = ground_rx + rx_antenna_height_m

    points: list[dict[str, Any]] = []
    fresnel_clear = True
    los_clear = True
    min_clearance_ratio = float("inf")
    worst_point_index = 0
    max_bulge_m = 0.0

    for i in range(n):
        frac = i / (n - 1)
        d1_m = total_m * frac
        d2_m = total_m - d1_m
        ground = elevations[i]
        bulge = earth_bulge_m(d1_m, d2_m, k_factor)
        max_bulge_m = max(max_bulge_m, bulge)

        # Linear interpolation of the radio beam height between the two
        # antenna tips (straight line through free space).
        beam = beam_tx_m + (beam_rx_m - beam_tx_m) * frac
        # Effective terrain block = ground + earth bulge.
        block = ground + bulge

        r1 = fresnel_radius_m(d1_m, d2_m, freq_mhz)
        clearance = beam - block
        ratio = (clearance / r1) if r1 > 0 else (1.0 if clearance >= 0 else -1.0)
        if clearance < 0:
            los_clear = False
        if ratio < 1.0:
            fresnel_clear = False
        if ratio < min_clearance_ratio:
            min_clearance_ratio = ratio
            worst_point_index = i

        points.append({
            "fraction": round(frac, 5),
            "distance_m": round(d1_m, 1),
            "elevation_m": round(ground, 1),
            "clutter_height_m": round(clutter_height_m, 1),
            "beam_height_m": round(beam, 1),
            "earth_bulge_m": round(bulge, 1),
            "fresnel_radius_m": round(r1, 1),
            "clearance_m": round(clearance, 1),
            "clearance_ratio": round(ratio, 3),
        })

    budget = compute_link_budget(
        distance_m=total_m,
        freq_mhz=freq_mhz,
        tx_power_dbm=tx_power_dbm,
        tx_gain_dbi=tx_gain_dbi,
        rx_gain_dbi=rx_gain_dbi,
        cable_loss_db=cable_loss_db,
    )
    fade_margin_db = budget["rx_power_dbm"] - rx_sensitivity_dbm

    worst = points[worst_point_index]
    return {
        "distance_m": round(total_m, 1),
        "distance_km": round(total_m / 1000, 3),
        "frequency_mhz": freq_mhz,
        "los_clear": los_clear,
        "fresnel_clear": fresnel_clear,
        "min_clearance_ratio": round(min_clearance_ratio, 3),
        "worst_point": worst,
        "max_earth_bulge_m": round(max_bulge_m, 1),
        "link_budget": {
            "eirp_dbm": round(budget["eirp_dbm"], 1),
            "fspl_db": round(budget["fspl_db"], 1),
            "rx_power_dbm": round(budget["rx_power_dbm"], 1),
            "fade_margin_db": round(fade_margin_db, 1),
            "rx_sensitivity_dbm": rx_sensitivity_dbm,
        },
        "profile": points,
    }


async def analyze_coverage(
    terrain_svc,
    lat: float, lng: float,
    radius_m: float,
    freq_mhz: float,
    tx_power_dbm: float,
    tx_gain_dbi: float,
    rx_gain_dbi: float,
    rx_sensitivity_dbm: float,
    tx_antenna_height_m: float,
    rx_antenna_height_m: float,
    env_loss_db: float = 0.0,
    k_factor: float = _DEFAULT_K_FACTOR,
    radial_count: int = 72,
    samples_per_radial: int = 30,
    clutter_height_m: float = 0.0,
) -> dict[str, Any]:
    """Analyse radio coverage radially from a point to build a viewshed polygon.

    `clutter_height_m` is an additional elevation offset (meters) added to all
    ground points to model trees and buildings, extending the coverage range
    when set to 0 (open terrain) or reducing it when positive.
    """
    radials_points = []
    all_points = []
    
    for i in range(radial_count):
        bearing = i * (360.0 / radial_count)
        end_lat, end_lng = destination_point(lat, lng, radius_m, bearing)
        pts = [intermediate_point(lat, lng, end_lat, end_lng, j / (samples_per_radial - 1)) for j in range(samples_per_radial)]
        radials_points.append(pts)
        all_points.extend(pts)
        
    all_elevations = await terrain_svc.get_elevations(all_points)
    
    poly_strong = []
    poly_medium = []
    poly_weak = []
    idx = 0
    
    for pts in radials_points:
        elevs = all_elevations[idx : idx + samples_per_radial]
        idx += samples_per_radial
        
        ground_tx = (elevs[0] or 0.0) + clutter_height_m
        beam_tx_m = ground_tx + tx_antenna_height_m
        
        last_strong = pts[0]
        last_medium = pts[0]
        last_weak = pts[0]
        
        for j in range(1, samples_per_radial):
            if elevs[j] is None:
                continue
                
            frac = j / (samples_per_radial - 1)
            dist = radius_m * frac
            
            ground_rx = (elevs[j] or 0.0) + clutter_height_m
            beam_rx_m = ground_rx + rx_antenna_height_m
            
            los_clear = True
            for k in range(1, j):
                if elevs[k] is None:
                    continue
                d1 = radius_m * (k / (samples_per_radial - 1))
                d2 = dist - d1
                bulge = earth_bulge_m(d1, d2, k_factor)
                beam = beam_tx_m + (beam_rx_m - beam_tx_m) * (k / j)
                block = elevs[k] + bulge + clutter_height_m
                if beam < block:
                    los_clear = False
                    break
                    
            if not los_clear:
                break
                
            budget = compute_link_budget(dist, freq_mhz, tx_power_dbm, tx_gain_dbi, rx_gain_dbi, 0.0)
            rx_pwr = budget["rx_power_dbm"] - env_loss_db
            
            if rx_pwr < rx_sensitivity_dbm:
                break
                
            last_weak = pts[j]
            if rx_pwr >= -120:
                last_medium = pts[j]
            if rx_pwr >= -100:
                last_strong = pts[j]
            
        poly_strong.append({"lat": last_strong[0], "lng": last_strong[1]})
        poly_medium.append({"lat": last_medium[0], "lng": last_medium[1]})
        poly_weak.append({"lat": last_weak[0], "lng": last_weak[1]})
        
    if poly_weak:
        poly_strong.append(poly_strong[0])
        poly_medium.append(poly_medium[0])
        poly_weak.append(poly_weak[0])
        
    return {
        "center": {"lat": lat, "lng": lng},
        "radius_m": radius_m,
        "polygons": {
            "strong": poly_strong,
            "medium": poly_medium,
            "weak": poly_weak
        }
    }


# ---------------------------------------------------------------------------
# TerrainService — DEM elevation fetcher with an in-memory cache
# ---------------------------------------------------------------------------

class TerrainService:
    """Fetches ground elevations from a DEM web API with an LRU-style cache.

    Owns its own aiohttp session (created lazily) so routes don't share a
    session with the Meshtastic connection. Cache misses are served from the
    API; repeat lookups of the same point are free.
    """

    def __init__(
        self,
        dem_url: str = _DEFAULT_DEM_URL,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        cache_size: int = _MAX_CACHE,
    ):
        self._dem_url = dem_url
        self._timeout_s = timeout_s
        self._session: aiohttp.ClientSession | None = None
        self._cache: dict[tuple[float, float], float | None] = {}
        self._cache_size = cache_size

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self._timeout_s)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        """Close the HTTP session (idempotent; call on app shutdown)."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    def _cache_get(self, lat: float, lng: float):
        """Read the cache, refreshing recency so it behaves LRU-ish."""
        key = (round(lat, 6), round(lng, 6))
        val = self._cache.pop(key, _MISS)
        if val is _MISS:
            return None, False
        self._cache[key] = val
        return val, True

    def _cache_put(self, lat: float, lng: float, elevation: float | None) -> None:
        key = (round(lat, 6), round(lng, 6))
        if len(self._cache) >= self._cache_size:
            # Drop the oldest key (dicts preserve insertion order in 3.7+).
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = elevation

    async def get_elevation(self, lat: float, lng: float) -> float | None:
        """Return ground elevation (m) at a point, or None if unavailable."""
        cached, hit = self._cache_get(lat, lng)
        if hit:
            return cached
        elevation = await self._fetch_batch([(lat, lng)])
        value = elevation[0] if elevation else None
        self._cache_put(lat, lng, value)
        return value

    async def get_elevations(self, points: list[tuple[float, float]]) -> list[float | None]:
        """Return elevations for a list of (lat, lng) points.

        Cached points are served instantly; the remainder are fetched in one
        batch API call. Returns None entries for points that failed.
        """
        results: list[float | None] = []
        uncached: list[tuple[int, tuple[float, float]]] = []
        for idx, (lat, lng) in enumerate(points):
            val, hit = self._cache_get(lat, lng)
            results.append(val if hit else None)
            if not hit:
                uncached.append((idx, (lat, lng)))

        if uncached:
            coords = [p for _, p in uncached]
            fetched = await self._fetch_batch(coords)
            for offset, (idx, (lat, lng)) in enumerate(uncached):
                val = fetched[offset] if offset < len(fetched) else None
                results[idx] = val
                self._cache_put(lat, lng, val)
        return results

    async def _fetch_batch(self, points: list[tuple[float, float]]) -> list[float | None]:
        """Query the DEM API for one or more points. Best-effort: returns None
        per point on any failure so terrain analysis never hard-crashes.
        Chunks requests to max 100 points to comply with OpenTopoData limits."""
        if not points:
            return []
            
        chunk_size = 100
        results: list[float | None] = []
        try:
            session = await self._get_session()
            for i in range(0, len(points), chunk_size):
                chunk = points[i:i+chunk_size]
                locations = "|".join(f"{lat},{lng}" for lat, lng in chunk)
                url = f"{self._dem_url}?locations={locations}"
                async with session.get(url) as resp:
                    if resp.status != 200:
                        logger.warning("Terrain DEM lookup failed (HTTP %s) for %d points", resp.status, len(chunk))
                        results.extend([None] * len(chunk))
                        continue
                    data = await resp.json()
                    chunk_results = []
                    for entry in data.get("results", []) or []:
                        elev = entry.get("elevation")
                        chunk_results.append(round(elev, 1) if isinstance(elev, (int, float)) else None)
                    while len(chunk_results) < len(chunk):
                        chunk_results.append(None)
                    results.extend(chunk_results)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("Terrain DEM lookup error: %s", exc)
            while len(results) < len(points):
                results.append(None)
                
        return results

    async def sample_path(
        self,
        lat1: float,
        lng1: float,
        lat2: float,
        lng2: float,
        samples: int = 32,
    ) -> list[float | None]:
        """Sample ground elevation along the great-circle path between two
        points. Number of samples is clamped to [_MAX_SAMPLES]."""
        samples = max(2, min(int(samples), _MAX_SAMPLES))
        points = [intermediate_point(lat1, lng1, lat2, lng2, i / (samples - 1)) for i in range(samples)]
        return await self.get_elevations(points)


# Sentinel for cache misses (distinguishes "not cached" from "cached None").
_MISS = object()


def geojson_from_polygon(polygon: list[dict[str, Any]]) -> dict[str, Any]:
    """Convert a polygon ring (list of {lat, lng}) to a GeoJSON Feature geometry."""
    coords = [(p["lng"], p["lat"]) for p in polygon if p.get("lat") is not None and p.get("lng") is not None]
    if len(coords) < 3:
        return {"type": "Polygon", "coordinates": []}
    return {"type": "Polygon", "coordinates": [coords]}


def export_coverage_geojson(result: dict[str, Any]) -> dict[str, Any]:
    """Export a coverage analysis result as GeoJSON with three polygons (strong/medium/weak)."""
    radius = result.get("radius_m", 0)
    polygons = result.get("polygons", {})
    features = []
    for label, ring in polygons.items():
        if ring:
            geom = geojson_from_polygon(ring)
            features.append({
                "type": "Feature",
                "properties": {"label": label, "radius_m": radius},
                "geometry": geom,
            })
    return {
        "type": "FeatureCollection",
        "features": features,
    }


async def export_coverage_kml(result: dict[str, Any]) -> str:
    """Export a coverage analysis result as KML (Polygon + Placemark for each ring)."""
    from xml.sax.saxutils import escape
    polygons = result.get("polygons", {})
    lines = []
    for label, ring in polygons.items():
        if not ring:
            continue
        coords = " ".join(escape(f"{p['lng']} {p['lat']}") for p in ring if p.get("lat") is not None and p.get("lng") is not None)
        if len(ring) >= 3:
            lines.append(f"<Placemark><name>{escape(label)}</name><Polygon><outerBoundaryIs><LinearRing><tessellate>1</tessellate><coordinates>{coords}</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>")
    kml_head = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">
<Document><name>NodePulse Coverage</name>"""
    kml_tail = "</Document></kml>"
    return kml_head + "".join(lines) + kml_tail