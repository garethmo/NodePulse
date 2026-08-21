"""
Unit tests for app/terrain.py — elevation fetching, LOS/Fresnel analysis,
link budget, and the /api/terrain/* route handlers.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import routes
from app.terrain import (
    TerrainService,
    analyze_link,
    earth_bulge_m,
    free_space_path_loss_db,
    fresnel_radius_m,
    great_circle_distance_m,
    intermediate_point,
    wavelength_m,
)

# ---------------------------------------------------------------------------
# Geometry / radio math
# ---------------------------------------------------------------------------

class TestGeometry:
    def test_great_circle_distance_same_point_is_zero(self):
        assert great_circle_distance_m(10.0, 20.0, 10.0, 20.0) == 0.0

    def test_great_circle_distance_known_value(self):
        # ~111.19 km per degree of latitude at the equator.
        d = great_circle_distance_m(0.0, 0.0, 1.0, 0.0)
        assert 111_000 < d < 112_000

    def test_great_circle_distance_antipodal(self):
        d = great_circle_distance_m(0.0, 0.0, 0.0, 180.0)
        assert d > 20_000_000

    def test_intermediate_point_midpoint(self):
        lat, lng = intermediate_point(0.0, 0.0, 0.0, 1.0, 0.5)
        assert abs(lat - 0.0) < 1e-9
        assert abs(lng - 0.5) < 1e-6

    def test_intermediate_point_endpoints(self):
        a = intermediate_point(-29.85, 31.02, -29.86, 31.05, 0.0)
        b = intermediate_point(-29.85, 31.02, -29.86, 31.05, 1.0)
        assert a == (-29.85, 31.02)
        assert abs(b[0] - -29.86) < 1e-9
        assert abs(b[1] - 31.05) < 1e-9

    def test_intermediate_point_zero_distance_returns_start(self):
        assert intermediate_point(1.0, 2.0, 1.0, 2.0, 0.5) == (1.0, 2.0)


class TestRadioMath:
    def test_wavelength_915mhz(self):
        # c / 915 MHz ≈ 0.3277 m
        lam = wavelength_m(915.0)
        assert 0.32 < lam < 0.34

    def test_fresnel_radius_midpoint(self):
        # Two points 1 km apart at 915 MHz, midpoint: d1 = d2 = 500 m.
        r = fresnel_radius_m(500.0, 500.0, 915.0)
        assert 9.0 < r < 9.1

    def test_fresnel_radius_zero_distance(self):
        assert fresnel_radius_m(0.0, 0.0, 915.0) == 0.0

    def test_fresnel_radius_second_zone_is_larger(self):
        r1 = fresnel_radius_m(500.0, 500.0, 915.0, n=1)
        r2 = fresnel_radius_m(500.0, 500.0, 915.0, n=2)
        assert r2 > r1

    def test_earth_bulge_midpoint(self):
        # 10 km path: bulge at midpoint ≈ d1*d2/(2*k*R) = 5000*5000/(2*1.333*6.371e6)
        b = earth_bulge_m(5000.0, 5000.0)
        assert 1.4 < b < 1.6

    def test_earth_bulge_zero(self):
        assert earth_bulge_m(0.0, 5000.0) == 0.0

    def test_free_space_path_loss(self):
        # Known value: 1 km at 915 MHz ≈ 91.6 dB
        loss = free_space_path_loss_db(1000.0, 915.0)
        assert 91.0 < loss < 92.0

    def test_free_space_path_loss_zero_distance(self):
        assert free_space_path_loss_db(0.0, 915.0) == 0.0

    def test_link_budget_increases_with_gain(self):
        base = analyze_link(
            from_point={"lat": 0.0, "lng": 0.0},
            to_point={"lat": 0.0, "lng": 0.01},
            freq_mhz=915.0,
            elevations=[0.0, 0.0],
            tx_power_dbm=10.0,
        )
        boosted = analyze_link(
            from_point={"lat": 0.0, "lng": 0.0},
            to_point={"lat": 0.0, "lng": 0.01},
            freq_mhz=915.0,
            elevations=[0.0, 0.0],
            tx_power_dbm=10.0,
            tx_gain_dbi=5.0,
            rx_gain_dbi=3.0,
        )
        assert boosted["link_budget"]["rx_power_dbm"] > base["link_budget"]["rx_power_dbm"]


# ---------------------------------------------------------------------------
# analyze_link — LOS / Fresnel verdicts
# ---------------------------------------------------------------------------

class TestAnalyzeLink:
    def test_flat_terrain_is_los_clear_and_fresnel_clear(self):
        result = analyze_link(
            from_point={"lat": 0.0, "lng": 0.0},
            to_point={"lat": 0.0, "lng": 0.02},
            freq_mhz=915.0,
            elevations=[0.0] * 10,
            tx_antenna_height_m=25.0,
            rx_antenna_height_m=25.0,
        )
        assert result["los_clear"] is True
        assert result["fresnel_clear"] is True
        assert result["distance_km"] > 2.0
        assert len(result["profile"]) == 10

    def test_mountain_in_middle_blocks_los(self):
        # A 300 m peak in the middle of a short path with low antennas.
        result = analyze_link(
            from_point={"lat": 0.0, "lng": 0.0},
            to_point={"lat": 0.0, "lng": 0.01},
            freq_mhz=915.0,
            elevations=[0.0, 50.0, 300.0, 50.0, 0.0],
            tx_antenna_height_m=2.0,
            rx_antenna_height_m=2.0,
        )
        assert result["los_clear"] is False
        assert result["fresnel_clear"] is False
        assert result["min_clearance_ratio"] < 0.0

    def test_high_antennas_clear_the_peak(self):
        result = analyze_link(
            from_point={"lat": 0.0, "lng": 0.0},
            to_point={"lat": 0.0, "lng": 0.01},
            freq_mhz=915.0,
            elevations=[0.0, 50.0, 300.0, 50.0, 0.0],
            tx_antenna_height_m=400.0,
            rx_antenna_height_m=400.0,
        )
        assert result["los_clear"] is True

    def test_profile_matches_elevation_length(self):
        result = analyze_link(
            from_point={"lat": 0.0, "lng": 0.0},
            to_point={"lat": 0.0, "lng": 0.01},
            freq_mhz=915.0,
            elevations=[1.0, 2.0, 3.0, 4.0],
        )
        assert len(result["profile"]) == 4
        assert result["profile"][0]["fraction"] == 0.0
        assert result["profile"][-1]["fraction"] == 1.0

    def test_too_few_samples_raises(self):
        with pytest.raises(ValueError):
            analyze_link(
                from_point={"lat": 0.0, "lng": 0.0},
                to_point={"lat": 0.0, "lng": 0.01},
                freq_mhz=915.0,
                elevations=[0.0],
            )

    def test_fade_margin_math(self):
        result = analyze_link(
            from_point={"lat": 0.0, "lng": 0.0},
            to_point={"lat": 0.0, "lng": 0.01},
            freq_mhz=915.0,
            elevations=[0.0, 0.0],
            tx_power_dbm=20.0,
            rx_sensitivity_dbm=-137.0,
        )
        budget = result["link_budget"]
        expected_margin = budget["rx_power_dbm"] - budget["rx_sensitivity_dbm"]
        assert budget["fade_margin_db"] == pytest.approx(expected_margin)


# ---------------------------------------------------------------------------
# TerrainService — cache + fetch behaviour (network mocked)
# ---------------------------------------------------------------------------

class TestTerrainService:
    @pytest.mark.asyncio
    async def test_cache_hit_avoids_network(self):
        svc = TerrainService()
        svc._cache_put(1.0, 2.0, 100.0)
        with patch.object(svc, "_fetch_batch") as mock_fetch:
            elev = await svc.get_elevation(1.0, 2.0)
        mock_fetch.assert_not_called()
        assert elev == 100.0

    @pytest.mark.asyncio
    async def test_fetch_caches_result(self):
        svc = TerrainService()
        with patch.object(svc, "_fetch_batch", new=AsyncMock(return_value=[123.4])):
            elev = await svc.get_elevation(-29.85, 31.02)
        assert elev == 123.4
        # Second call hits the cache — no network.
        with patch.object(svc, "_fetch_batch") as mock_fetch:
            elev2 = await svc.get_elevation(-29.85, 31.02)
        mock_fetch.assert_not_called()
        assert elev2 == 123.4

    @pytest.mark.asyncio
    async def test_batch_fetch_and_cache(self):
        svc = TerrainService()
        with patch.object(svc, "_fetch_batch", new=AsyncMock(return_value=[1.0, None, 3.0])):
            elevs = await svc.get_elevations([(0.0, 0.0), (0.0, 0.01), (0.0, 0.02)])
        assert elevs == [1.0, None, 3.0]
        # Cached values served without network.
        with patch.object(svc, "_fetch_batch") as mock_fetch:
            elevs2 = await svc.get_elevations([(0.0, 0.0), (0.0, 0.01), (0.0, 0.02)])
        mock_fetch.assert_not_called()
        assert elevs2 == [1.0, None, 3.0]

    @pytest.mark.asyncio
    async def test_sample_path_clamps_samples(self):
        svc = TerrainService()
        with patch.object(svc, "_fetch_batch", new=AsyncMock(return_value=[0.0] * 5)):
            elevs = await svc.sample_path(0.0, 0.0, 0.0, 0.05, samples=5)
        assert len(elevs) == 5

    @pytest.mark.asyncio
    async def test_sample_path_caps_at_max(self):
        svc = TerrainService()
        with patch.object(svc, "_fetch_batch", new=AsyncMock(return_value=[0.0] * 200)):
            elevs = await svc.sample_path(0.0, 0.0, 0.0, 0.05, samples=500)
        assert len(elevs) == 128

    @pytest.mark.asyncio
    async def test_cache_eviction(self):
        svc = TerrainService(cache_size=3)
        with patch.object(svc, "_fetch_batch", new=AsyncMock(return_value=[1.0])):
            for i in range(4):
                await svc.get_elevation(i, 0.0)
        assert len(svc._cache) == 3

    @pytest.mark.asyncio
    async def test_network_error_returns_none(self):
        svc = TerrainService()
        session = MagicMock()
        session.closed = False
        session.get.return_value.__aenter__ = AsyncMock(return_value=MagicMock(status=500))
        svc._session = session
        elev = await svc.get_elevation(1.0, 2.0)
        assert elev is None

    @pytest.mark.asyncio
    async def test_batch_response_padding(self):
        svc = TerrainService()
        session = MagicMock()
        session.closed = False
        resp = MagicMock(status=200)
        resp.json = AsyncMock(return_value={"results": [{"elevation": 5.0}]})
        session.get.return_value.__aenter__ = AsyncMock(return_value=resp)
        svc._session = session
        elevs = await svc.get_elevations([(1.0, 1.0), (2.0, 2.0), (3.0, 3.0)])
        assert elevs == [5.0, None, None]

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self):
        svc = TerrainService()
        await svc.close()
        await svc.close()  # no session yet — must not raise


# ---------------------------------------------------------------------------
# Route handlers — /api/terrain/*
# ---------------------------------------------------------------------------

def make_request(app_dict=None, method="GET", path="/", body=None, query=None):
    request = MagicMock()
    request.method = method
    request.path = path
    request.query = query or {}
    request.app = app_dict or {}
    request._body = b""
    request.content = MagicMock()
    request.content.read = AsyncMock(return_value=json.dumps(body or {}).encode())
    request.json = AsyncMock(return_value=body or {})
    return request


def _terrain_app(**conn_overrides):
    app = {"terrain": MagicMock()}
    return app


class TestTerrainElevationRoute:
    @pytest.mark.asyncio
    async def test_returns_elevation(self):
        app = _terrain_app()
        app["terrain"].get_elevation = AsyncMock(return_value=150.0)
        request = make_request(app, query={"lat": "-29.85", "lng": "31.02"})
        resp = await routes.handle_terrain_elevation(request)
        data = json.loads(resp.body)
        assert resp.status == 200
        assert data["elevation_m"] == 150.0

    @pytest.mark.asyncio
    async def test_missing_params_is_400(self):
        request = make_request(_terrain_app())
        resp = await routes.handle_terrain_elevation(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_invalid_lat_is_400(self):
        request = make_request(_terrain_app(), query={"lat": "abc", "lng": "31.02"})
        resp = await routes.handle_terrain_elevation(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_out_of_range_lat_is_400(self):
        request = make_request(_terrain_app(), query={"lat": "95", "lng": "31.02"})
        resp = await routes.handle_terrain_elevation(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_service_error_is_502(self):
        app = _terrain_app()
        app["terrain"].get_elevation = AsyncMock(side_effect=RuntimeError("boom"))
        request = make_request(app, query={"lat": "1", "lng": "2"})
        resp = await routes.handle_terrain_elevation(request)
        assert resp.status == 502

    @pytest.mark.asyncio
    async def test_missing_service_is_503(self):
        request = make_request({}, query={"lat": "1", "lng": "2"})
        resp = await routes.handle_terrain_elevation(request)
        assert resp.status == 503


class TestTerrainLinkRoute:
    def _base_body(self):
        return {
            "from": {"lat": -29.85, "lng": 31.02},
            "to": {"lat": -29.86, "lng": 31.05},
            "frequency_mhz": 915,
        }

    @pytest.mark.asyncio
    async def test_valid_link(self):
        app = _terrain_app()
        app["terrain"].sample_path = AsyncMock(return_value=[10.0] * 20)
        request = make_request(app, method="POST", path="/api/terrain/link", body=self._base_body())
        resp = await routes.handle_terrain_link(request)
        data = json.loads(resp.body)
        assert resp.status == 200
        assert "profile" in data
        assert len(data["profile"]) == 20
        assert "los_clear" in data
        assert "link_budget" in data

    @pytest.mark.asyncio
    async def test_missing_from_is_400(self):
        body = self._base_body()
        del body["from"]
        request = make_request(_terrain_app(), method="POST", body=body)
        resp = await routes.handle_terrain_link(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_same_points_is_400(self):
        body = self._base_body()
        body["to"] = {"lat": -29.85, "lng": 31.02}
        request = make_request(_terrain_app(), method="POST", body=body)
        resp = await routes.handle_terrain_link(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_missing_frequency_is_400(self):
        body = self._base_body()
        del body["frequency_mhz"]
        request = make_request(_terrain_app(), method="POST", body=body)
        resp = await routes.handle_terrain_link(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_bad_frequency_is_400(self):
        body = self._base_body()
        body["frequency_mhz"] = "abc"
        request = make_request(_terrain_app(), method="POST", body=body)
        resp = await routes.handle_terrain_link(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_bad_optional_float_is_400(self):
        body = self._base_body()
        body["tx_power_dbm"] = "abc"
        request = make_request(_terrain_app(), method="POST", body=body)
        resp = await routes.handle_terrain_link(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_invalid_json_is_400(self):
        request = make_request(_terrain_app(), method="POST")
        request.json = AsyncMock(side_effect=RuntimeError("bad json"))
        resp = await routes.handle_terrain_link(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_all_none_elevations_is_502(self):
        app = _terrain_app()
        app["terrain"].sample_path = AsyncMock(return_value=[None] * 20)
        request = make_request(app, method="POST", body=self._base_body())
        resp = await routes.handle_terrain_link(request)
        assert resp.status == 502

    @pytest.mark.asyncio
    async def test_missing_service_is_503(self):
        request = make_request({}, method="POST", body=self._base_body())
        resp = await routes.handle_terrain_link(request)
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_custom_params_flow_through(self):
        app = _terrain_app()
        app["terrain"].sample_path = AsyncMock(return_value=[5.0] * 16)
        body = self._base_body()
        body["tx_power_dbm"] = 10
        body["tx_gain_dbi"] = 2.1
        body["rx_gain_dbi"] = 2.1
        body["rx_sensitivity_dbm"] = -140
        body["samples"] = 16
        request = make_request(app, method="POST", body=body)
        resp = await routes.handle_terrain_link(request)
        data = json.loads(resp.body)
        assert resp.status == 200
        assert len(data["profile"]) == 16
        assert data["link_budget"]["rx_sensitivity_dbm"] == -140


class TestInterpolateNones:
    def test_no_nones_passthrough(self):
        assert routes._interpolate_nones([1.0, 2.0, 3.0]) == [1.0, 2.0, 3.0]

    def test_empty(self):
        assert routes._interpolate_nones([]) == []

    def test_all_nones(self):
        assert routes._interpolate_nones([None, None]) == [None, None]

    def test_leading_nones_filled_with_first_known(self):
        assert routes._interpolate_nones([None, None, 5.0, 7.0]) == [5.0, 5.0, 5.0, 7.0]

    def test_trailing_nones_stay_none(self):
        assert routes._interpolate_nones([1.0, 3.0, None]) == [1.0, 3.0, None]

    def test_interior_gap_interpolated(self):
        assert routes._interpolate_nones([1.0, None, 3.0]) == [1.0, 2.0, 3.0]

    def test_wide_gap_interpolated(self):
        result = routes._interpolate_nones([10.0, None, None, 30.0])
        assert result[0] == 10.0
        assert result[1] == pytest.approx(16.6667, abs=1e-3)
        assert result[2] == pytest.approx(23.3333, abs=1e-3)
        assert result[3] == 30.0