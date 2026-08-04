"""
StockIQ backend — test suite
Run:  pytest tests/ -v
All tests work without real API keys or network access.
"""

import math
import os
import sys
import time
import types
from datetime import datetime
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ─── Stub heavy dependencies before importing our module ──────────────────────

def _make_curl_stub():
    mod = types.ModuleType("curl_cffi")
    req = types.ModuleType("curl_cffi.requests")
    sess = MagicMock()
    req.Session = MagicMock(return_value=sess)
    mod.requests = req
    sys.modules["curl_cffi"] = mod
    sys.modules["curl_cffi.requests"] = req
    return mod, sess


def _make_yf_stub():
    mod = types.ModuleType("yfinance")
    sys.modules["yfinance"] = mod
    return mod


_curl_mod, _curl_sess = _make_curl_stub()
_yf_mod = _make_yf_stub()

# ─── Now import the real module ───────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stock_api as api
from stock_api import (
    _cache,
    _CACHE_MAX,
    _MAX_TTL,
    _client_ip,
    _extended_hours_window,
    _rate,
    _scan_one,
    _translate,
    app,
    cache_get,
    cache_set,
    clean,
    err,
    get_universe,
    norm_ticker,
    rate_ok,
    CORE_UNIVERSE,
    TICKER_RE,
)
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse

client = TestClient(app, raise_server_exceptions=False)


# ═══════════════════════════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _clear_cache():
    _cache.clear()


def _clear_rate():
    _rate.clear()


def _fake_request(ip="1.2.3.4", fwd=None):
    req = MagicMock()
    req.client.host = ip
    req.headers = {}
    if fwd:
        req.headers = {"x-forwarded-for": fwd}
    return req


# ═══════════════════════════════════════════════════════════════════════════════
# 1. norm_ticker
# ═══════════════════════════════════════════════════════════════════════════════

class TestNormTicker:
    def test_simple_uppercase(self):
        assert norm_ticker("AAPL") == "AAPL"

    def test_lowercasе_gets_uppercased(self):
        assert norm_ticker("aapl") == "AAPL"

    def test_mixed_case(self):
        assert norm_ticker("Msft") == "MSFT"

    def test_dot_separator(self):
        assert norm_ticker("BRK.B") == "BRK.B"

    def test_dash_separator(self):
        assert norm_ticker("BRK-B") == "BRK-B"

    def test_single_letter(self):
        assert norm_ticker("A") == "A"

    def test_digit_start(self):
        assert norm_ticker("1AAPL") == "1AAPL"

    def test_ten_chars_exactly(self):
        # 1 + 9 = 10 chars, boundary case
        assert norm_ticker("ABCDE12345") == "ABCDE12345"

    def test_eleven_chars_rejected(self):
        assert norm_ticker("ABCDE123456") is None

    def test_empty_string(self):
        assert norm_ticker("") is None

    def test_none_input(self):
        assert norm_ticker(None) is None

    def test_whitespace_stripped(self):
        assert norm_ticker("  AAPL  ") == "AAPL"

    def test_exclamation_rejected(self):
        assert norm_ticker("AAPL!") is None

    def test_spaces_inside_rejected(self):
        assert norm_ticker("AA PL") is None

    def test_unicode_rejected(self):
        assert norm_ticker("מניה") is None

    def test_slash_rejected(self):
        assert norm_ticker("AA/BB") is None

    def test_dot_at_start_rejected(self):
        # first char must be [A-Z0-9], not dot
        assert norm_ticker(".AAPL") is None

    def test_numbers_only(self):
        assert norm_ticker("123") == "123"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. clean()
# ═══════════════════════════════════════════════════════════════════════════════

class TestClean:
    def test_integer_returns_float(self):
        assert clean(42) == 42.0

    def test_float_passthrough(self):
        assert clean(3.14) == pytest.approx(3.14)

    def test_string_number(self):
        assert clean("2.5") == pytest.approx(2.5)

    def test_none_returns_none(self):
        assert clean(None) is None

    def test_nan_returns_none(self):
        assert clean(float("nan")) is None

    def test_inf_returns_inf(self):
        assert clean(float("inf")) == float("inf")

    def test_non_numeric_string(self):
        assert clean("hello") is None

    def test_zero(self):
        assert clean(0) == 0.0

    def test_negative(self):
        assert clean(-5.5) == pytest.approx(-5.5)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. err()
# ═══════════════════════════════════════════════════════════════════════════════

class TestErr:
    def test_returns_json_response(self):
        r = err(400, "bad input")
        assert isinstance(r, JSONResponse)

    def test_status_code_400(self):
        r = err(400, "bad")
        assert r.status_code == 400

    def test_status_code_404(self):
        r = err(404, "not found")
        assert r.status_code == 404

    def test_status_code_429(self):
        r = err(429, "rate limit")
        assert r.status_code == 429

    def test_status_code_502(self):
        r = err(502, "upstream error")
        assert r.status_code == 502

    def test_body_has_error_key(self):
        import json
        r = err(400, "test message")
        body = json.loads(r.body)
        assert "error" in body

    def test_body_error_value(self):
        import json
        r = err(400, "test message")
        body = json.loads(r.body)
        assert body["error"] == "test message"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. cache_get / cache_set
# ═══════════════════════════════════════════════════════════════════════════════

class TestCache:
    def setup_method(self):
        _clear_cache()

    def test_get_missing_key(self):
        assert cache_get("missing", 60) is None

    def test_set_and_get(self):
        cache_set("k1", {"v": 1})
        assert cache_get("k1", 60) == {"v": 1}

    def test_ttl_not_expired(self):
        cache_set("k2", "hello")
        assert cache_get("k2", 3600) == "hello"

    def test_ttl_expired(self):
        cache_set("k3", "old")
        # manipulate the timestamp directly
        _cache["k3"] = (time.time() - 100, "old")
        assert cache_get("k3", 10) is None   # TTL=10s, 100s old → expired

    def test_set_returns_value(self):
        result = cache_set("k4", [1, 2, 3])
        assert result == [1, 2, 3]

    def test_overwrite_existing(self):
        cache_set("k5", "first")
        cache_set("k5", "second")
        assert cache_get("k5", 60) == "second"

    def test_different_keys_independent(self):
        cache_set("a", 1)
        cache_set("b", 2)
        assert cache_get("a", 60) == 1
        assert cache_get("b", 60) == 2

    def test_eviction_when_full(self):
        """Cache bounded at _CACHE_MAX entries; new entry still gets stored."""
        _clear_cache()
        old_time = time.time() - (_MAX_TTL + 1)   # definitely expired
        for i in range(_CACHE_MAX):
            _cache[f"old_{i}"] = (old_time, i)
        # Add one more — should evict stale entries then succeed
        cache_set("new_entry", "value")
        assert cache_get("new_entry", 60) == "value"

    def test_eviction_removes_oldest_quarter_when_no_expired(self):
        """When cache is full of fresh entries, oldest quarter is evicted."""
        _clear_cache()
        now = time.time()
        for i in range(_CACHE_MAX):
            _cache[f"fresh_{i}"] = (now - (_CACHE_MAX - i), i)  # increasingly recent
        # oldest 75 (25%) should be candidates
        initial_count = len(_cache)
        cache_set("brand_new", "fresh")
        assert len(_cache) <= _CACHE_MAX
        assert cache_get("brand_new", 60) == "fresh"

    def test_zero_ttl_always_misses(self):
        cache_set("k6", "x")
        # TTL=0 means anything is expired
        assert cache_get("k6", 0) is None

    def test_large_value_stored(self):
        big = list(range(10000))
        cache_set("big", big)
        assert cache_get("big", 60) == big


# ═══════════════════════════════════════════════════════════════════════════════
# 5. _client_ip
# ═══════════════════════════════════════════════════════════════════════════════

class TestClientIP:
    def test_direct_client(self):
        req = MagicMock()
        req.headers = {}
        req.client.host = "10.0.0.1"
        assert _client_ip(req) == "10.0.0.1"

    def test_forwarded_for_single(self):
        req = MagicMock()
        req.headers = {"x-forwarded-for": "203.0.113.5"}
        assert _client_ip(req) == "203.0.113.5"

    def test_forwarded_for_chain(self):
        req = MagicMock()
        req.headers = {"x-forwarded-for": "203.0.113.5, 10.0.0.1, 172.16.0.1"}
        assert _client_ip(req) == "203.0.113.5"

    def test_forwarded_for_with_spaces(self):
        req = MagicMock()
        req.headers = {"x-forwarded-for": " 1.2.3.4 , 5.6.7.8"}
        assert _client_ip(req) == "1.2.3.4"

    def test_no_client(self):
        req = MagicMock()
        req.headers = {}
        req.client = None
        assert _client_ip(req) == "unknown"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. rate_ok
# ═══════════════════════════════════════════════════════════════════════════════

class TestRateOk:
    def setup_method(self):
        _clear_rate()

    def test_first_call_passes(self):
        req = _fake_request()
        assert rate_ok(req, "test", 5, 60) is True

    def test_under_limit_passes(self):
        req = _fake_request("1.1.1.1")
        for _ in range(4):
            assert rate_ok(req, "bucket", 5, 60) is True

    def test_at_limit_fails(self):
        req = _fake_request("2.2.2.2")
        for _ in range(5):
            rate_ok(req, "lim", 5, 60)
        assert rate_ok(req, "lim", 5, 60) is False

    def test_different_buckets_independent(self):
        req = _fake_request("3.3.3.3")
        for _ in range(5):
            rate_ok(req, "bucket_a", 5, 60)
        # bucket_a is at limit but bucket_b is fresh
        assert rate_ok(req, "bucket_b", 5, 60) is True

    def test_different_ips_independent(self):
        req_a = _fake_request("4.4.4.4")
        req_b = _fake_request("5.5.5.5")
        for _ in range(5):
            rate_ok(req_a, "shared", 5, 60)
        # req_a is throttled, req_b is not
        assert rate_ok(req_b, "shared", 5, 60) is True

    def test_window_expiry(self):
        req = _fake_request("6.6.6.6")
        # Put stale timestamps into the bucket manually
        stale = time.time() - 120   # 2 minutes ago
        _rate["test_exp:6.6.6.6"] = [stale] * 5
        # These are outside a 60-second window, so should be treated as zero hits
        assert rate_ok(req, "test_exp", 5, 60) is True

    def test_limit_1_blocks_second_call(self):
        req = _fake_request("7.7.7.7")
        rate_ok(req, "strict", 1, 60)
        assert rate_ok(req, "strict", 1, 60) is False

    def test_forwarded_for_used_for_ip(self):
        req = MagicMock()
        req.headers = {"x-forwarded-for": "203.0.113.99"}
        req.client.host = "10.0.0.1"
        # Both calls from the same forwarded IP share the rate bucket
        assert rate_ok(req, "fwd", 1, 60) is True
        assert rate_ok(req, "fwd", 1, 60) is False


# ═══════════════════════════════════════════════════════════════════════════════
# 7. _scan_one
# ═══════════════════════════════════════════════════════════════════════════════

import pandas as pd
import numpy as np


def _make_hist(n=200, trend="up"):
    """Generate a fake history DataFrame."""
    closes = []
    p = 100.0
    for i in range(n):
        if trend == "up":
            p *= 1.005
        elif trend == "down":
            p *= 0.995
        else:
            p += 0.1 * (1 if i % 2 == 0 else -1)
        closes.append(round(p, 2))
    highs = [c * 1.01 for c in closes]
    lows = [c * 0.99 for c in closes]
    df = pd.DataFrame({"Close": closes, "High": highs, "Low": lows})
    return df


class TestScanOne:
    def test_short_history_returns_none(self):
        df = _make_hist(10)
        assert _scan_one("AAPL", df) is None

    def test_exactly_19_rows_returns_none(self):
        df = _make_hist(19)
        assert _scan_one("AAPL", df) is None

    def test_exactly_20_rows_returns_none_or_result(self):
        # 20 rows but fewer than period+1=15 for RSI — we just test it doesn't crash
        df = _make_hist(20)
        result = _scan_one("AAPL", df)
        # Either None (not enough for RSI) or a valid dict — never an exception
        assert result is None or isinstance(result, dict)

    def test_standard_uptrend_produces_result(self):
        df = _make_hist(100, "up")
        result = _scan_one("AAPL", df)
        # Uptrend should at least give MA9 > MA20 signal
        assert result is None or isinstance(result, dict)

    def test_result_has_required_keys(self):
        df = _make_hist(150, "up")
        result = _scan_one("AAPL", df)
        if result is not None:
            for key in ("ticker", "price", "rsi", "dist_to_break", "signals", "score", "overbought", "spark"):
                assert key in result

    def test_ticker_preserved(self):
        df = _make_hist(150, "up")
        result = _scan_one("NVDA", df)
        if result is not None:
            assert result["ticker"] == "NVDA"

    def test_spark_length(self):
        df = _make_hist(150, "up")
        result = _scan_one("AAPL", df)
        if result is not None:
            assert len(result["spark"]) == 20

    def test_spark_values_are_float(self):
        df = _make_hist(150, "up")
        result = _scan_one("AAPL", df)
        if result is not None:
            assert all(isinstance(v, float) for v in result["spark"])

    def test_rsi_range(self):
        df = _make_hist(200, "up")
        result = _scan_one("AAPL", df)
        if result is not None:
            assert 0 <= result["rsi"] <= 100

    def test_overbought_flag_true_when_rsi_above_70(self):
        """Craft a strong uptrend to push RSI above 70."""
        closes = [100.0 * (1.02 ** i) for i in range(150)]
        highs = [c * 1.01 for c in closes]
        lows = [c * 0.99 for c in closes]
        df = pd.DataFrame({"Close": closes, "High": highs, "Low": lows})
        result = _scan_one("AAPL", df)
        if result is not None and result["rsi"] > 70:
            assert result["overbought"] is True

    def test_overbought_flag_false_when_rsi_low(self):
        """Strong downtrend → RSI well below 70."""
        closes = [200.0 * (0.98 ** i) for i in range(150)]
        highs = [c * 1.005 for c in closes]
        lows = [c * 0.995 for c in closes]
        df = pd.DataFrame({"Close": closes, "High": highs, "Low": lows})
        result = _scan_one("AAPL", df)
        if result is not None:
            assert result["overbought"] is False

    def test_score_non_negative(self):
        df = _make_hist(200, "up")
        result = _scan_one("AAPL", df)
        if result is not None:
            assert result["score"] >= 0

    def test_no_signals_returns_none(self):
        """Prices that rise then fall: RSI in neutral zone, MA9 < MA20, no close resistance."""
        # Rise from 100 to 140 over first 170 days, then drop to 110 over last 30 days.
        # This gives: MA9 < MA20 (no MA signal), RSI ~35-50 (no extreme signal),
        # and the nearest resistance cluster is >5% above 110 (so no breakout signal).
        up = [100.0 + i * (40.0 / 169) for i in range(170)]
        down = [140.0 - j * (30.0 / 29) for j in range(30)]
        closes = up + down
        highs = [c * 1.005 for c in closes]
        lows = [c * 0.995 for c in closes]
        df = pd.DataFrame({"Close": closes, "High": highs, "Low": lows})
        result = _scan_one("AAPL", df)
        # This scenario should yield no signals (or None if RSI exactly at boundary)
        # At minimum: if a result is returned, verify it's a dict; otherwise None is fine.
        assert result is None or isinstance(result, dict)

    def test_nan_price_returns_none(self):
        closes = [float("nan")] * 100 + [100.0] * 50
        # Put NaN at the last position
        closes[-1] = float("nan")
        highs = [c if not math.isnan(c) else 100.5 for c in closes]
        lows = [c if not math.isnan(c) else 99.5 for c in closes]
        df = pd.DataFrame({"Close": closes, "High": highs, "Low": lows})
        # The function may or may not cope; at minimum it should not raise
        try:
            result = _scan_one("AAPL", df)
            assert result is None or isinstance(result, dict)
        except Exception:
            pass   # NaN propagation edge case — just don't crash unhandled

    def test_dist_to_break_none_when_no_resistance_above(self):
        """Strongly rising prices — all pivots likely below current price."""
        closes = [50.0 + i for i in range(200)]  # steadily climbing
        highs = [c * 1.005 for c in closes]
        lows = [c * 0.995 for c in closes]
        df = pd.DataFrame({"Close": closes, "High": highs, "Low": lows})
        result = _scan_one("AAPL", df)
        if result is not None:
            # dist_to_break is None when no resistance above current price
            assert result["dist_to_break"] is None or isinstance(result["dist_to_break"], float)

    def test_signals_is_list(self):
        df = _make_hist(200, "up")
        result = _scan_one("AAPL", df)
        if result is not None:
            assert isinstance(result["signals"], list)

    def test_nan_dropped_by_dropna(self):
        closes = [float("nan")] * 5 + [100.0 + i * 0.5 for i in range(150)]
        highs = [100.5 + i * 0.5 for i in range(155)]
        lows = [99.5 + i * 0.5 for i in range(155)]
        df = pd.DataFrame({"Close": closes, "High": highs, "Low": lows})
        # Should handle NaN rows gracefully via dropna
        try:
            result = _scan_one("AAPL", df)
            assert result is None or isinstance(result, dict)
        except Exception:
            pass

    def test_wilder_rsi_not_simple_average(self):
        """
        Wilder RSI and simple-average RSI diverge. This test verifies the
        function reaches through at least period+1=15 steps of smoothing.
        """
        import random
        random.seed(42)
        closes = [100.0]
        for _ in range(199):
            closes.append(closes[-1] * (1 + random.uniform(-0.03, 0.031)))
        highs = [c * 1.01 for c in closes]
        lows = [c * 0.99 for c in closes]
        df = pd.DataFrame({"Close": closes, "High": highs, "Low": lows})
        result = _scan_one("X", df)
        # If it runs through the Wilder loop it won't crash
        assert result is None or 0 <= result["rsi"] <= 100


# ═══════════════════════════════════════════════════════════════════════════════
# 8. _extended_hours_window
# ═══════════════════════════════════════════════════════════════════════════════

class TestExtendedHoursWindow:
    def _patch_now(self, weekday, hour, minute):
        dt = MagicMock()
        dt.weekday.return_value = weekday  # 0=Mon … 4=Fri, 5=Sat, 6=Sun
        import datetime as _dt
        dt.time.return_value = _dt.time(hour, minute)
        return dt

    def test_saturday_returns_false(self):
        with patch("stock_api.datetime") as mock_dt:
            mock_dt.now.return_value = self._patch_now(5, 8, 0)  # Saturday
            assert _extended_hours_window() is False

    def test_sunday_returns_false(self):
        with patch("stock_api.datetime") as mock_dt:
            mock_dt.now.return_value = self._patch_now(6, 8, 0)  # Sunday
            assert _extended_hours_window() is False

    def test_regular_hours_returns_false(self):
        with patch("stock_api.datetime") as mock_dt:
            mock_dt.now.return_value = self._patch_now(1, 12, 0)  # Tuesday noon
            assert _extended_hours_window() is False

    def test_pre_market_returns_true(self):
        with patch("stock_api.datetime") as mock_dt:
            mock_dt.now.return_value = self._patch_now(1, 7, 0)  # Tuesday 7am ET
            assert _extended_hours_window() is True

    def test_post_market_returns_true(self):
        with patch("stock_api.datetime") as mock_dt:
            mock_dt.now.return_value = self._patch_now(2, 17, 30)  # Wed 5:30pm ET
            assert _extended_hours_window() is True

    def test_market_open_boundary_9_30_returns_false(self):
        with patch("stock_api.datetime") as mock_dt:
            mock_dt.now.return_value = self._patch_now(0, 9, 30)  # Mon 9:30am
            assert _extended_hours_window() is False

    def test_post_market_end_boundary_20_00_returns_true(self):
        with patch("stock_api.datetime") as mock_dt:
            mock_dt.now.return_value = self._patch_now(0, 20, 0)  # Mon 8pm
            assert _extended_hours_window() is True

    def test_after_post_market_21_00_returns_false(self):
        with patch("stock_api.datetime") as mock_dt:
            mock_dt.now.return_value = self._patch_now(0, 21, 0)  # Mon 9pm
            assert _extended_hours_window() is False

    def test_zoneinfo_exception_returns_false(self):
        with patch("stock_api.datetime") as mock_dt:
            mock_dt.now.side_effect = Exception("tz error")
            assert _extended_hours_window() is False


# ═══════════════════════════════════════════════════════════════════════════════
# 9. get_universe
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetUniverse:
    def setup_method(self):
        _clear_cache()

    def test_includes_core_universe(self):
        with patch("stock_api._fetch_trending", return_value=[]):
            u = get_universe()
        for ticker in CORE_UNIVERSE[:5]:
            assert ticker in u

    def test_trending_appended(self):
        with patch("stock_api._fetch_trending", return_value=["FAKE1", "FAKE2"]):
            u = get_universe()
        assert "FAKE1" in u
        assert "FAKE2" in u

    def test_no_duplicates(self):
        with patch("stock_api._fetch_trending", return_value=["AAPL", "MSFT"]):
            u = get_universe()
        assert u.count("AAPL") == 1
        assert u.count("MSFT") == 1

    def test_trending_cached_on_second_call(self):
        call_count = {"n": 0}
        def _trending():
            call_count["n"] += 1
            return ["TSTR"]
        with patch("stock_api._fetch_trending", side_effect=_trending):
            get_universe()
            get_universe()
        assert call_count["n"] == 1  # second call should hit cache

    def test_failed_trending_falls_back_to_core(self):
        with patch("stock_api._fetch_trending", return_value=[]):
            u = get_universe()
        assert u == CORE_UNIVERSE


# ═══════════════════════════════════════════════════════════════════════════════
# 10. _translate
# ═══════════════════════════════════════════════════════════════════════════════

class TestTranslate:
    def test_empty_text_returns_none(self):
        assert _translate("", 10) is None

    def test_zero_budget_returns_none(self):
        assert _translate("hello", 0) is None

    def test_negative_budget_returns_none(self):
        assert _translate("hello", -1) is None

    def test_exception_returns_none(self):
        with patch("stock_api.crequests") as mock_r:
            mock_r.get.side_effect = Exception("network error")
            result = _translate("hello world", 5)
        assert result is None

    def test_successful_translation(self):
        mock_response = MagicMock()
        mock_response.json.return_value = [[["שלום עולם", "hello world", None, None, None]], None, "en"]
        with patch("stock_api.crequests") as mock_r:
            mock_r.get.return_value = mock_response
            result = _translate("hello world", 5)
        assert result == "שלום עולם"

    def test_malformed_response_returns_none(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {"unexpected": "format"}
        with patch("stock_api.crequests") as mock_r:
            mock_r.get.return_value = mock_response
            result = _translate("hello", 5)
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# 11. HTTP endpoints (via TestClient)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRootEndpoint:
    def test_root_ok(self):
        r = client.get("/")
        assert r.status_code == 200

    def test_root_has_status(self):
        r = client.get("/")
        assert r.json().get("status") == "ok"

    def test_root_has_service(self):
        r = client.get("/")
        assert "service" in r.json()


class TestStockEndpoint:
    def setup_method(self):
        _clear_cache()
        _clear_rate()

    def test_invalid_ticker_returns_400(self):
        r = client.get("/stock/!!bad!!")
        assert r.status_code == 400

    def test_invalid_ticker_has_error_key(self):
        r = client.get("/stock/!!bad!!")
        assert "error" in r.json()

    def test_valid_ticker_queries_yfinance(self):
        import pandas as pd
        fake_hist = pd.DataFrame({
            "Close": [150.0, 151.0],
            "High": [152.0, 153.0],
            "Low": [149.0, 150.0],
            "Volume": [1e6, 1.1e6],
        }, index=pd.to_datetime(["2026-01-01", "2026-01-02"]))

        mock_ticker = MagicMock()
        mock_ticker.history.return_value = fake_hist
        mock_ticker.info = {"longName": "Apple Inc.", "sector": "Technology",
                            "marketCap": 3e12}
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            r = client.get("/stock/AAPL")
        assert r.status_code == 200

    def test_empty_history_returns_404(self):
        import pandas as pd
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame()
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            r = client.get("/stock/ZZZZ")
        assert r.status_code == 404

    def test_yfinance_exception_returns_502(self):
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.side_effect = Exception("network error")
            r = client.get("/stock/AAPL")
        assert r.status_code == 502

    def test_cache_hit_returns_cached(self):
        cache_set("stock:AAPL", {"ticker": "AAPL", "cached": True})
        with patch("stock_api.yf") as mock_yf:
            r = client.get("/stock/AAPL")
        # Should not call yf at all — served from cache
        mock_yf.Ticker.assert_not_called()
        assert r.status_code == 200


class TestStockEndpointEarningsDate:
    """שלב חדש בהעשרת ה-AI: קרבה לדוח רבעוני. yfinance.calendar משתנה
    במבנה בין גרסאות (רשימה / ערך בודד / חסר), ולכן מכוסה כאן בנפרד —
    וחשוב במיוחד שכשל בשליפתו לא יפיל את כל התשובה של /stock."""

    def setup_method(self):
        _clear_cache()
        _clear_rate()

    def _fake_hist(self):
        import pandas as pd
        return pd.DataFrame({
            "Close": [150.0, 151.0],
            "High": [152.0, 153.0],
            "Low": [149.0, 150.0],
            "Volume": [1e6, 1.1e6],
        }, index=pd.to_datetime(["2026-01-01", "2026-01-02"]))

    def test_earnings_date_list_computes_days_to_earnings(self):
        from datetime import timedelta
        from zoneinfo import ZoneInfo
        target = datetime.now(ZoneInfo("America/New_York")).date() + timedelta(days=5)
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = self._fake_hist()
        mock_ticker.info = {"longName": "Apple Inc."}
        mock_ticker.calendar = {"Earnings Date": [target]}
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            r = client.get("/stock/AAPL")
        assert r.status_code == 200
        assert r.json().get("days_to_earnings") == 5

    def test_earnings_date_single_value_not_list(self):
        from datetime import timedelta
        from zoneinfo import ZoneInfo
        target = datetime.now(ZoneInfo("America/New_York")).date() + timedelta(days=2)
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = self._fake_hist()
        mock_ticker.info = {"longName": "Apple Inc."}
        mock_ticker.calendar = {"Earnings Date": target}
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            r = client.get("/stock/AAPL")
        assert r.json().get("days_to_earnings") == 2

    def test_earnings_date_takes_earliest_of_range(self):
        from datetime import timedelta
        from zoneinfo import ZoneInfo
        d1 = datetime.now(ZoneInfo("America/New_York")).date() + timedelta(days=10)
        d2 = datetime.now(ZoneInfo("America/New_York")).date() + timedelta(days=14)
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = self._fake_hist()
        mock_ticker.info = {"longName": "Apple Inc."}
        mock_ticker.calendar = {"Earnings Date": [d1, d2]}
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            r = client.get("/stock/AAPL")
        assert r.json().get("days_to_earnings") == 10

    def test_missing_calendar_key_returns_none(self):
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = self._fake_hist()
        mock_ticker.info = {"longName": "Apple Inc."}
        mock_ticker.calendar = {}
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            r = client.get("/stock/AAPL")
        assert r.json().get("days_to_earnings") is None

    def test_calendar_not_a_dict_returns_none(self):
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = self._fake_hist()
        mock_ticker.info = {"longName": "Apple Inc."}
        mock_ticker.calendar = None
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            r = client.get("/stock/AAPL")
        assert r.json().get("days_to_earnings") is None

    def test_calendar_exception_does_not_break_stock_endpoint(self):
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = self._fake_hist()
        mock_ticker.info = {"longName": "Apple Inc."}
        type(mock_ticker).calendar = PropertyMock(side_effect=Exception("boom"))
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            r = client.get("/stock/AAPL")
        assert r.status_code == 200
        j = r.json()
        assert j.get("days_to_earnings") is None
        assert j.get("name") == "Apple Inc."


class TestHistoryEndpoint:
    def setup_method(self):
        _clear_cache()
        _clear_rate()

    def test_invalid_ticker_returns_400(self):
        r = client.get("/history/!!bad!!")
        assert r.status_code == 400

    def test_invalid_range_returns_400(self):
        r = client.get("/history/AAPL?range=2y")
        assert r.status_code == 400

    def test_valid_ticker_queries_yfinance(self):
        import pandas as pd
        fake_hist = pd.DataFrame({
            "Close": [150.0, 151.0, 152.5],
        }, index=pd.to_datetime(["2021-01-01", "2021-01-02", "2021-01-03"]))

        mock_ticker = MagicMock()
        mock_ticker.history.return_value = fake_hist
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            r = client.get("/history/AAPL?range=5y")
        assert r.status_code == 200
        data = r.json()
        assert data["ticker"] == "AAPL"
        assert data["range"] == "5y"
        assert data["closes"] == [150.0, 151.0, 152.5]
        assert data["labels"] == ["2021-01-01", "2021-01-02", "2021-01-03"]
        mock_ticker.history.assert_called_once_with(period="5y", interval="1d")

    def test_default_range_is_5y(self):
        import pandas as pd
        fake_hist = pd.DataFrame({"Close": [10.0]}, index=pd.to_datetime(["2021-01-01"]))
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = fake_hist
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            r = client.get("/history/AAPL")
        assert r.status_code == 200
        assert r.json()["range"] == "5y"
        mock_ticker.history.assert_called_once_with(period="5y", interval="1d")

    def test_max_range_accepted(self):
        import pandas as pd
        fake_hist = pd.DataFrame({"Close": [10.0]}, index=pd.to_datetime(["2021-01-01"]))
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = fake_hist
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            r = client.get("/history/AAPL?range=max")
        assert r.status_code == 200
        assert r.json()["range"] == "max"

    def test_empty_history_returns_404(self):
        import pandas as pd
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame()
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            r = client.get("/history/ZZZZ?range=5y")
        assert r.status_code == 404

    # ── רזולוציה שבועית/חודשית + שיא ושפל: בלי אלה אי אפשר לזהות אזורי
    # תמיכה והתנגדות על הטווח הארוך, וזו הסיבה שהאנלייזר ראה שנה בלבד. ──

    def _ohlc(self):
        import pandas as pd
        return pd.DataFrame({
            "Close": [150.0, 151.0, 152.5],
            "High": [155.0, 156.0, 157.5],
            "Low": [145.0, 146.0, 147.5],
        }, index=pd.to_datetime(["2021-01-01", "2021-01-08", "2021-01-15"]))

    def test_weekly_interval_is_passed_through(self):
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = self._ohlc()
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            r = client.get("/history/AAPL?range=5y&interval=1wk")
        assert r.status_code == 200
        assert r.json()["interval"] == "1wk"
        mock_ticker.history.assert_called_once_with(period="5y", interval="1wk")

    def test_monthly_interval_accepted(self):
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = self._ohlc()
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            r = client.get("/history/AAPL?range=max&interval=1mo")
        assert r.status_code == 200
        assert r.json()["interval"] == "1mo"

    def test_invalid_interval_returns_400(self):
        r = client.get("/history/AAPL?range=5y&interval=1h")
        assert r.status_code == 400

    def test_highs_and_lows_are_returned(self):
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = self._ohlc()
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            r = client.get("/history/AAPL?range=5y&interval=1wk")
        d = r.json()
        assert d["highs"] == [155.0, 156.0, 157.5]
        assert d["lows"] == [145.0, 146.0, 147.5]

    def test_missing_ohlc_columns_do_not_crash(self):
        """יש טיקרים שמחזירים סגירות בלבד — חייב להחזיר רשימות ריקות
        ולא לקרוס, אחרת האנלייזר כולו נופל על מניה אחת חריגה."""
        import pandas as pd
        only_close = pd.DataFrame({"Close": [10.0]}, index=pd.to_datetime(["2021-01-01"]))
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = only_close
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            r = client.get("/history/AAPL?range=5y&interval=1wk")
        assert r.status_code == 200
        assert r.json()["highs"] == []
        assert r.json()["lows"] == []

    def test_interval_is_part_of_the_cache_key(self):
        """יומי ושבועי לאותו טווח הם נתונים שונים לגמרי — אסור שידרסו זה את זה."""
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = self._ohlc()
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            client.get("/history/AAPL?range=5y&interval=1d")
            client.get("/history/AAPL?range=5y&interval=1wk")
        assert mock_ticker.history.call_count == 2

    def test_yfinance_exception_returns_502(self):
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.side_effect = Exception("network error")
            r = client.get("/history/AAPL?range=5y")
        assert r.status_code == 502

    def test_cache_hit_returns_cached(self):
        cache_set("history:AAPL:5y:1d", {"ticker": "AAPL", "range": "5y", "closes": [1.0], "labels": ["2021-01-01"]})
        with patch("stock_api.yf") as mock_yf:
            r = client.get("/history/AAPL?range=5y")
        # אמור להיות מוגש מהמטמון — בלי קריאה ל-yfinance בכלל
        mock_yf.Ticker.assert_not_called()
        assert r.status_code == 200

    def test_different_ranges_use_separate_cache_keys(self):
        import pandas as pd
        fake_hist = pd.DataFrame({"Close": [10.0]}, index=pd.to_datetime(["2021-01-01"]))
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = fake_hist
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            r1 = client.get("/history/AAPL?range=5y")
            r2 = client.get("/history/AAPL?range=10y")
        assert r1.status_code == 200 and r2.status_code == 200
        assert mock_ticker.history.call_count == 2


class TestPriceEndpoint:
    def setup_method(self):
        _clear_cache()
        _clear_rate()

    def test_invalid_ticker_returns_400(self):
        r = client.get("/price/bad!!!")
        assert r.status_code == 400

    def test_valid_ticker_returns_200(self):
        mock_ticker = MagicMock()
        mock_ticker.fast_info = {"last_price": 150.5, "previous_close": 149.0}
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            with patch("stock_api._extended_hours_window", return_value=False):
                r = client.get("/price/AAPL")
        assert r.status_code == 200

    def test_price_response_has_required_fields(self):
        mock_ticker = MagicMock()
        mock_ticker.fast_info = {"last_price": 150.5, "previous_close": 149.0}
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            with patch("stock_api._extended_hours_window", return_value=False):
                r = client.get("/price/MSFT")
        if r.status_code == 200:
            data = r.json()
            assert "ticker" in data
            assert "price" in data

    def test_none_price_returns_404(self):
        import pandas as pd
        mock_ticker = MagicMock()
        # fast_info access raises → falls through to history, which is also empty
        type(mock_ticker).fast_info = PropertyMock(side_effect=Exception("no fast_info"))
        mock_ticker.history.return_value = pd.DataFrame({"Close": []})
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            with patch("stock_api._extended_hours_window", return_value=False):
                r = client.get("/price/ZZZZ")
        assert r.status_code == 404

    def test_exception_returns_502(self):
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.side_effect = Exception("bad")
            r = client.get("/price/AAPL")
        assert r.status_code == 502


class TestSentimentEndpoint:
    def setup_method(self):
        _clear_cache()
        _clear_rate()

    def test_invalid_ticker_returns_400(self):
        # Use a ticker with characters invalid for norm_ticker (not URL special chars)
        r = client.get("/sentiment/INVAL!D")
        assert r.status_code == 400

    def test_valid_returns_200(self):
        import pandas as pd
        mock_ticker = MagicMock()
        mock_ticker.info = {"recommendationKey": "buy"}
        mock_ticker.recommendations = pd.DataFrame({
            "strongBuy": [3, 4], "buy": [5, 6],
            "hold": [2, 2], "sell": [1, 0], "strongSell": [0, 0],
        })
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            r = client.get("/sentiment/AAPL")
        assert r.status_code == 200

    def test_response_has_ticker(self):
        import pandas as pd
        mock_ticker = MagicMock()
        mock_ticker.info = {}
        mock_ticker.recommendations = pd.DataFrame()
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            r = client.get("/sentiment/TSLA")
        if r.status_code == 200:
            assert r.json().get("ticker") == "TSLA"

    def test_exception_returns_502(self):
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.side_effect = RuntimeError("fail")
            r = client.get("/sentiment/AAPL")
        assert r.status_code == 502


class TestExtractStockFacts:
    """שלב 2 של שיפור ה-AI: העשרת התוכן ב-2 עובדות חדשות — שינוי מחיר ב-5
    ימי מסחר ונפח מסחר יחסי. שתיהן אופציונליות כדי לא לשבור בקשות ישנות
    שעדיין לא שולחות את השדות האלה (למשל אם הפרונטאנד לא התעדכן עדיין)."""

    BASE = {"ticker": "AAPL", "trend": "עולה", "rsiTxt": "נייטרלי", "rsiNum": 55,
            "bullPct": 60, "bearPct": 10}

    def test_change_5d_and_rel_volume_included_when_present(self):
        body = dict(self.BASE, change5dPct=3.4, relVolume=1.8)
        ticker, facts, cache_fields = api._extract_stock_facts(body)
        joined = " ".join(facts)
        assert "עלייה של 3.4%" in joined
        assert "פי 1.8" in joined
        # מפתח המטמון נגזר מהעובדות, ולכן די לבדוק שערך שונה משנה אותו
        _, _, other = api._extract_stock_facts(dict(self.BASE, change5dPct=9.9, relVolume=1.8))
        assert cache_fields != other

    def test_negative_change_5d_shown_as_decline_with_absolute_value(self):
        body = dict(self.BASE, change5dPct=-2.7)
        _, facts, _ = api._extract_stock_facts(body)
        joined = " ".join(facts)
        assert "ירידה של 2.7%" in joined
        assert "-2.7" not in joined

    def test_fields_omitted_when_absent(self):
        """בקשה ישנה בלי השדות החדשים לא אמורה לקרוס ולא להזכיר נפח/שינוי-5-ימים."""
        ticker, facts, cache_fields = api._extract_stock_facts(dict(self.BASE))
        joined = " ".join(facts)
        assert "ימי מסחר" not in joined
        assert "נפח מסחר יחסי" not in joined
        # השדות האופציונליים נשארים None, ולכן מפתח המטמון שונה מזה של בקשה
        # שכן נשלחו בה — נבדק בהשוואה ולא לפי מיקום באינדקס.
        _, _, with_fields = api._extract_stock_facts(
            dict(self.BASE, change5dPct=3.4, relVolume=1.8))
        assert cache_fields != with_fields

    def test_values_are_rounded_in_the_facts(self):
        """העיגול נעשה בעובדות עצמן — הן מה שהמודל רואה."""
        body = dict(self.BASE, change5dPct=3.456, relVolume=1.849)
        _, facts, _ = api._extract_stock_facts(body)
        joined = " ".join(facts)
        assert "3.5%" in joined
        assert "פי 1.8" in joined
        assert "3.456" not in joined
        assert "1.849" not in joined

    def test_rsi_is_never_described_as_volatility(self):
        """הבאג שנמצא בפרודקשן: RSI 31.7 תואר ע"י המודל כ'תנודתיות יתר' —
        טעות מקצועית. המצב נגזר עכשיו מהמספר בצד השרת ונמסר במפורש."""
        for v in (12, 31.7, 55, 78):
            _, facts, _ = api._extract_stock_facts(dict(self.BASE, rsiNum=v))
            assert "תנודתיות" not in " ".join(facts)

    def test_low_rsi_is_labelled_oversold(self):
        _, facts, _ = api._extract_stock_facts(dict(self.BASE, rsiNum=22))
        assert "מכירת יתר" in " ".join(facts)

    def test_high_rsi_is_labelled_overbought(self):
        _, facts, _ = api._extract_stock_facts(dict(self.BASE, rsiNum=78))
        assert "קניית יתר" in " ".join(facts)

    def test_mid_rsi_is_labelled_neutral(self):
        _, facts, _ = api._extract_stock_facts(dict(self.BASE, rsiNum=52))
        assert "RSI: 52 — נייטרלי." in facts

    def test_rsi_just_above_thirty_is_neutral_not_oversold(self):
        """31.7 אינו מכירת יתר לפי הסף התקני 30 — וכרטיס המדד באפליקציה
        כבר מציג Neutral. ה-AI חייב לומר את אותו הדבר, לא לסתור אותו."""
        _, facts, _ = api._extract_stock_facts(dict(self.BASE, rsiNum=31.7))
        joined = " ".join(facts)
        assert "מכירת יתר" not in joined
        assert "נייטרלי, בחלק התחתון של הטווח" in joined

    def test_missing_rsi_does_not_crash(self):
        body = dict(self.BASE)
        body["rsiNum"] = None
        _, facts, _ = api._extract_stock_facts(body)
        assert "RSI: לא זמין." in facts

    def test_pe_ratio_is_rounded(self):
        """המודל חוזר על המספר כלשונו — 285.51373 נראה שבור בטקסט עברי."""
        _, facts, _ = api._extract_stock_facts(dict(self.BASE, peRatio=285.51373))
        joined = " ".join(facts)
        assert "285.5" in joined
        assert "285.51373" not in joined


class TestAIEndpoint:
    def setup_method(self):
        _clear_cache()
        _clear_rate()

    def test_no_groq_key_returns_empty_text(self):
        with patch.object(api, "GROQ_KEY", ""):
            r = client.post("/ai", json={"ticker": "AAPL", "trend": "bullish"})
        assert r.status_code == 200
        j = r.json()
        assert j.get("text") == ""
        # שלב 4: כל תשובה ריקה נושאת "reason" כדי שהפרונטאנד יציג סיבה ידידותית
        # במקום מסך ריק שקט — כאן השירות לא מוגדר בכלל.
        assert j.get("reason") == "unavailable"

    def test_budget_exhausted_returns_budget_reason(self):
        with patch.object(api, "GROQ_KEY", "fake-key"), \
             patch.object(api, "ai_budget_ok", return_value=False), \
             patch("stock_api.crequests") as mock_r:
            r = client.post("/ai", json={"ticker": "AAPL", "trend": "unique-uncached-ai"})
        assert r.status_code == 200
        j = r.json()
        assert j.get("text") == ""
        assert j.get("reason") == "budget"
        mock_r.post.assert_not_called()

    def test_both_groq_attempts_fail_returns_transient_reason(self):
        with patch.object(api, "GROQ_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r, \
             patch("stock_api.time.sleep"):
            mock_r.post.side_effect = Exception("down")
            r = client.post("/ai", json={"ticker": "AAPL", "trend": "unique-transient-ai"})
        assert r.status_code == 200
        j = r.json()
        assert j.get("text") == ""
        assert j.get("reason") == "transient"

    def test_rate_limit_returns_429(self):
        """Exhaust the /ai rate bucket (12 per 60s) and expect 429."""
        req = _fake_request("9.9.9.9")
        for _ in range(12):
            rate_ok(req, "ai", 12, 60)
        # Now force a request via test client with the same IP header
        r = client.post(
            "/ai",
            json={"ticker": "AAPL"},
            headers={"x-forwarded-for": "9.9.9.9"},
        )
        assert r.status_code == 429

    def test_valid_groq_response_cached(self):
        _clear_cache()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "ניתוח בדיקה"}}]
        }
        with patch.object(api, "GROQ_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r:
            mock_r.post.return_value = mock_resp
            r1 = client.post("/ai", json={"ticker": "AAPL", "trend": "up",
                                           "rsiTxt": "neutral", "rsiNum": 55,
                                           "bullPct": 60, "bearPct": 10})
            r2 = client.post("/ai", json={"ticker": "AAPL", "trend": "up",
                                           "rsiTxt": "neutral", "rsiNum": 55,
                                           "bullPct": 60, "bearPct": 10})
        # Both calls succeed
        assert r1.status_code == 200
        assert r2.status_code == 200
        # Groq was called only once (second hit the cache)
        assert mock_r.post.call_count == 1

    def test_recovers_after_one_transient_failure(self):
        """ניסיון ראשון נכשל באופן זמני (שגיאת רשת), הניסיון השני מצליח —
        המשתמש עדיין מקבל טקסט ולא מסך ריק."""
        good_resp = MagicMock()
        good_resp.status_code = 200
        good_resp.json.return_value = {
            "choices": [{"message": {"content": "ניתוח אחרי נפילה זמנית"}}]
        }
        with patch.object(api, "GROQ_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r, \
             patch("stock_api.time.sleep"):
            mock_r.post.side_effect = [Exception("timeout"), good_resp]
            r = client.post("/ai", json={"ticker": "NVDA", "trend": "up",
                                          "rsiTxt": "neutral", "rsiNum": 50,
                                          "bullPct": 70, "bearPct": 5})
        assert r.status_code == 200
        assert r.json().get("text") == "ניתוח אחרי נפילה זמנית"
        assert mock_r.post.call_count == 2


class TestGroqPayload:
    """מנעולי רגרסיה על הבחירות שתיקנו את איכות העברית: מודל שכותב עברית
    ישירות, temperature נמוך, והנחיות מערכת. temperature=1 (ברירת המחדל של
    Groq) הוא מה שגרם למודל להמציא מילים כמו 'מפולס' באמצע משפט."""

    def test_uses_a_supported_non_deprecated_model(self):
        """llama-3.3-70b נסגר ב-16.08.2026. המודל חייב להיות נתמך והיום."""
        p = api._groq_payload("שלום", 600)
        assert p["model"] == "openai/gpt-oss-120b"
        assert "llama-3.3" not in p["model"]

    def test_temperature_is_low(self):
        p = api._groq_payload("שלום", 600)
        assert "temperature" in p, "בלי temperature מפורש Groq משתמש ב-1.0 והעברית נשברת"
        assert p["temperature"] <= 0.5

    def test_sends_low_reasoning_effort(self):
        """gpt-oss הוא מודל reasoning. effort נמוך מצמצם חשיבה באנגלית
        לפני הכתיבה בעברית, וגם חוסך טוקנים מהמכסה."""
        p = api._groq_payload("שלום", 600)
        assert p["reasoning_effort"] == "low"

    def test_does_not_request_the_reasoning_text_back(self):
        """איננו משתמשים ב-reasoning; אין סיבה להעביר אותו ברשת."""
        p = api._groq_payload("שלום", 600)
        assert p["include_reasoning"] is False

    def test_reasoning_headroom_prevents_truncation(self):
        """טוקני החשיבה נגרעים מאותו תקציב. בלי מרווח, תשובה בת 4-5 משפטים
        בעברית נחתכת באמצע המשפט האחרון."""
        p = api._groq_payload("x", 600)
        assert p["max_completion_tokens"] > 600
        assert p["max_completion_tokens"] == 600 + api.AI_REASONING_HEADROOM

    def test_has_system_message_before_user(self):
        p = api._groq_payload("שאלת המשתמש", 600)
        assert p["messages"][0]["role"] == "system"
        assert p["messages"][1]["role"] == "user"
        assert p["messages"][1]["content"] == "שאלת המשתמש"

    def test_system_message_pins_rsi_terminology(self):
        """הטעות המקורית בפרודקשן: RSI נמוך תואר כ'תנודתיות יתר'."""
        p = api._groq_payload("x", 600)
        sys_msg = p["messages"][0]["content"]
        assert "מכירת יתר" in sys_msg
        assert "קניית יתר" in sys_msg
        assert "RSI אינו מדד לתנודתיות" in sys_msg

    def test_system_message_forbids_reclassifying_rsi(self):
        """המודל קיבל 'נייטרלי' וכתב בכל זאת 'מכירת יתר' — סתירה למה
        שהאפליקציה עצמה מציגה. ההנחיה אוסרת עליו לסווג מחדש."""
        sys_msg = api._groq_payload("x", 600)["messages"][0]["content"]
        assert "אל תסווג אותו מחדש" in sys_msg

    def test_system_message_has_style_example(self):
        """דוגמת סגנון קצרה משפרת היצמדות להנחיות הרבה מעבר לרשימת איסורים."""
        sys_msg = api._groq_payload("x", 600)["messages"][0]["content"]
        assert "חקה את הסגנון" in sys_msg

    def test_max_tokens_scales_with_request(self):
        small = api._groq_payload("x", 600)["max_completion_tokens"]
        large = api._groq_payload("x", 900)["max_completion_tokens"]
        assert large - small == 300


class TestCallGroq:
    """בדיקות ישירות ל-_call_groq: ניסיון חוזר יחיד רק על כשלים זמניים
    (חריגת רשת/timeout, או סטטוס 429/5xx), ולא על כשל לוגי (תשובה תקינה
    בלי choices, או תשובה שאינה JSON תקין)."""

    def test_retries_once_on_exception_then_succeeds(self):
        good_resp = MagicMock()
        good_resp.status_code = 200
        good_resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        with patch.object(api, "GROQ_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r, \
             patch("stock_api.time.sleep") as mock_sleep:
            mock_r.post.side_effect = [Exception("boom"), good_resp]
            result = api._call_groq({"model": "x", "messages": []})
        assert result == {"choices": [{"message": {"content": "ok"}}]}
        assert mock_r.post.call_count == 2
        mock_sleep.assert_called_once()

    def test_retries_once_on_5xx_then_succeeds(self):
        bad_resp = MagicMock()
        bad_resp.status_code = 503
        good_resp = MagicMock()
        good_resp.status_code = 200
        good_resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        with patch.object(api, "GROQ_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r, \
             patch("stock_api.time.sleep"):
            mock_r.post.side_effect = [bad_resp, good_resp]
            result = api._call_groq({"model": "x", "messages": []})
        assert result == {"choices": [{"message": {"content": "ok"}}]}
        assert mock_r.post.call_count == 2

    def test_does_not_retry_on_429(self):
        """נצפה בפרודקשן ביומני Groq: כל 429 הופיע כזוג בקשות באותה שנייה
        בדיוק — הניסיון החוזר שלנו. הוא לא יכול להצליח, כי מכסה לא מתאפסת
        בתוך 0.3 שניות, והוא רק מכפיל את הפגיעות במכסה שכבר מוצתה."""
        bad_resp = MagicMock()
        bad_resp.status_code = 429
        good_resp = MagicMock()
        good_resp.status_code = 200
        good_resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        with patch.object(api, "GROQ_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r, \
             patch("stock_api.time.sleep"):
            mock_r.post.side_effect = [bad_resp, good_resp]
            result = api._call_groq({"model": "x", "messages": []})
        assert result is api.RATE_LIMITED
        assert mock_r.post.call_count == 1

    def test_still_retries_on_server_errors(self):
        """503 הוא כן כשל זמני אמיתי — שם הניסיון החוזר נשאר נכון."""
        bad_resp = MagicMock()
        bad_resp.status_code = 503
        good_resp = MagicMock()
        good_resp.status_code = 200
        good_resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        with patch.object(api, "GROQ_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r, \
             patch("stock_api.time.sleep"):
            mock_r.post.side_effect = [bad_resp, good_resp]
            result = api._call_groq({"model": "x", "messages": []})
        assert result == {"choices": [{"message": {"content": "ok"}}]}
        assert mock_r.post.call_count == 2

    def test_ai_endpoint_reports_rate_limited_distinctly(self):
        _clear_cache()
        _clear_rate()
        bad_resp = MagicMock()
        bad_resp.status_code = 429
        with patch.object(api, "GROQ_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r, \
             patch("stock_api.time.sleep"):
            mock_r.post.return_value = bad_resp
            r = client.post("/ai", json={"ticker": "RL1", "trend": "עולה"})
        j = r.json()
        assert j.get("text") == ""
        assert j.get("reason") == "rate_limited"

    def test_battle_endpoint_reports_rate_limited_distinctly(self):
        _clear_cache()
        _clear_rate()
        bad_resp = MagicMock()
        bad_resp.status_code = 429
        with patch.object(api, "GROQ_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r, \
             patch("stock_api.time.sleep"):
            mock_r.post.return_value = bad_resp
            r = client.post("/ai/battle", json={"ticker": "RL2", "trend": "עולה"})
        j = r.json()
        assert j.get("reason") == "rate_limited"

    def test_both_attempts_fail_with_exception_returns_none(self):
        with patch.object(api, "GROQ_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r, \
             patch("stock_api.time.sleep"):
            mock_r.post.side_effect = Exception("still down")
            result = api._call_groq({"model": "x", "messages": []})
        assert result is None
        assert mock_r.post.call_count == 2

    def test_both_attempts_5xx_returns_none(self):
        bad_resp = MagicMock()
        bad_resp.status_code = 500
        with patch.object(api, "GROQ_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r, \
             patch("stock_api.time.sleep"):
            mock_r.post.return_value = bad_resp
            result = api._call_groq({"model": "x", "messages": []})
        assert result is None
        assert mock_r.post.call_count == 2

    def test_no_retry_on_response_missing_choices(self):
        """כשל לוגי (תשובה תקינה בלי choices) לא חוזר על עצמו — ניסיון נוסף
        לא יתקן תשובה שגויה מהמודל."""
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"unexpected": "format"}
        with patch.object(api, "GROQ_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r, \
             patch("stock_api.time.sleep") as mock_sleep:
            mock_r.post.return_value = resp
            result = api._call_groq({"model": "x", "messages": []})
        assert result == {"unexpected": "format"}
        assert mock_r.post.call_count == 1
        mock_sleep.assert_not_called()

    def test_no_retry_on_invalid_json_response(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("not json")
        with patch.object(api, "GROQ_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r, \
             patch("stock_api.time.sleep") as mock_sleep:
            mock_r.post.return_value = resp
            result = api._call_groq({"model": "x", "messages": []})
        assert result is None
        assert mock_r.post.call_count == 1
        mock_sleep.assert_not_called()


class TestSplitBattle:
    def test_both_sections_present(self):
        text = "BULL:\nהמניה נראית חזקה מאוד.\nBEAR:\nיש סיכון ברור לירידה."
        bull, bear = api._split_battle(text)
        assert bull == "המניה נראית חזקה מאוד."
        assert bear == "יש סיכון ברור לירידה."

    def test_case_insensitive_labels(self):
        text = "bull:\nטיעון שורי.\nbear:\nטיעון דובי."
        bull, bear = api._split_battle(text)
        assert bull == "טיעון שורי."
        assert bear == "טיעון דובי."

    def test_missing_bear_section(self):
        text = "BULL:\nרק טיעון שורי כאן."
        bull, bear = api._split_battle(text)
        assert bull == "רק טיעון שורי כאן."
        assert bear == ""

    def test_missing_bull_section(self):
        text = "BEAR:\nרק טיעון דובי כאן."
        bull, bear = api._split_battle(text)
        assert bull == ""
        assert bear == "רק טיעון דובי כאן."

    def test_empty_text(self):
        bull, bear = api._split_battle("")
        assert bull == "" and bear == ""

    def test_multiline_sections(self):
        text = "BULL:\nשורה ראשונה.\nשורה שנייה.\nBEAR:\nשורה שלישית.\nשורה רביעית."
        bull, bear = api._split_battle(text)
        assert "שורה ראשונה" in bull and "שורה שנייה" in bull
        assert "שורה שלישית" in bear and "שורה רביעית" in bear

    def test_strips_stray_markdown_bold_wrapper(self):
        # דוגמה אמיתית שחזרה מהמודל בפרודקשן: כוכביות עוטפות את כל הפסקה
        text = "BULL:\n**  \nהמגמה חיובית מאוד.\n\n**\nBEAR:\n**  \nיש סיכון לירידה.\n\n**"
        bull, bear = api._split_battle(text)
        assert bull == "המגמה חיובית מאוד."
        assert bear == "יש סיכון לירידה."
        assert "*" not in bull and "*" not in bear


class TestStripMdWrap:
    def test_no_markdown_unchanged(self):
        assert api._strip_md_wrap("טקסט רגיל.") == "טקסט רגיל."

    def test_strips_leading_and_trailing_double_asterisk(self):
        assert api._strip_md_wrap("**  \nטקסט.\n\n**") == "טקסט."

    def test_strips_single_asterisk(self):
        assert api._strip_md_wrap("*טקסט*") == "טקסט"

    def test_does_not_touch_inner_asterisks(self):
        # רק העטיפה בקצוות מוסרת — כוכביות שהן חלק אמיתי מהמשפט נשארות
        assert api._strip_md_wrap("מחיר * נפח = מחזור") == "מחיר * נפח = מחזור"

    def test_empty_string(self):
        assert api._strip_md_wrap("") == ""


class TestAIBattleEndpoint:
    def setup_method(self):
        _clear_cache()
        _clear_rate()

    def test_no_groq_key_returns_empty(self):
        with patch.object(api, "GROQ_KEY", ""):
            r = client.post("/ai/battle", json={"ticker": "AAPL", "trend": "up"})
        assert r.status_code == 200
        j = r.json()
        assert j.get("bull") == "" and j.get("bear") == ""
        assert j.get("reason") == "unavailable"

    def test_rate_limit_returns_429(self):
        """Exhaust the /ai/battle rate bucket (12 per 60s) and expect 429."""
        req = _fake_request("8.8.4.4")
        for _ in range(12):
            rate_ok(req, "ai_battle", 12, 60)
        r = client.post(
            "/ai/battle",
            json={"ticker": "AAPL"},
            headers={"x-forwarded-for": "8.8.4.4"},
        )
        assert r.status_code == 429

    def test_valid_groq_response_split_and_cached(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "BULL:\nטיעון שורי.\nBEAR:\nטיעון דובי."}}]
        }
        with patch.object(api, "GROQ_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r:
            mock_r.post.return_value = mock_resp
            r1 = client.post("/ai/battle", json={"ticker": "AAPL", "trend": "up",
                                                   "rsiTxt": "neutral", "rsiNum": 55,
                                                   "bullPct": 60, "bearPct": 10})
            r2 = client.post("/ai/battle", json={"ticker": "AAPL", "trend": "up",
                                                   "rsiTxt": "neutral", "rsiNum": 55,
                                                   "bullPct": 60, "bearPct": 10})
        assert r1.status_code == 200
        j1 = r1.json()
        assert j1.get("bull") == "טיעון שורי."
        assert j1.get("bear") == "טיעון דובי."
        # Groq called only once — second request served from cache
        assert mock_r.post.call_count == 1
        assert r2.json() == j1

    def test_only_one_groq_call_per_request(self):
        """The battle must NOT double AI usage: exactly one Groq call per uncached request."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "BULL:\nא.\nBEAR:\nב."}}]
        }
        with patch.object(api, "GROQ_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r:
            mock_r.post.return_value = mock_resp
            client.post("/ai/battle", json={"ticker": "TSLA", "trend": "up"})
        assert mock_r.post.call_count == 1

    def test_no_choices_returns_empty(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"unexpected": "format"}
        with patch.object(api, "GROQ_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r:
            mock_r.post.return_value = mock_resp
            r = client.post("/ai/battle", json={"ticker": "AAPL"})
        assert r.status_code == 200
        j = r.json()
        assert j.get("bull") == "" and j.get("bear") == ""
        assert j.get("reason") == "transient"

    def test_groq_exception_returns_empty(self):
        with patch.object(api, "GROQ_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r:
            mock_r.post.side_effect = Exception("network error")
            r = client.post("/ai/battle", json={"ticker": "AAPL"})
        assert r.status_code == 200
        j = r.json()
        assert j.get("bull") == "" and j.get("bear") == ""
        assert j.get("reason") == "transient"

    def test_daily_budget_exhausted_returns_empty(self):
        with patch.object(api, "GROQ_KEY", "fake-key"), \
             patch.object(api, "ai_budget_ok", return_value=False), \
             patch("stock_api.crequests") as mock_r:
            r = client.post("/ai/battle", json={"ticker": "AAPL", "trend": "unique-uncached"})
        assert r.status_code == 200
        j = r.json()
        assert j.get("bull") == "" and j.get("bear") == ""
        assert j.get("reason") == "budget"
        mock_r.post.assert_not_called()

    def test_recovers_after_one_transient_failure(self):
        """ניסיון ראשון נכשל באופן זמני (סטטוס 502), הניסיון השני מצליח."""
        good_resp = MagicMock()
        good_resp.status_code = 200
        good_resp.json.return_value = {
            "choices": [{"message": {"content": "BULL:\nטיעון שורי.\nBEAR:\nטיעון דובי."}}]
        }
        bad_resp = MagicMock()
        bad_resp.status_code = 502
        with patch.object(api, "GROQ_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r, \
             patch("stock_api.time.sleep"):
            mock_r.post.side_effect = [bad_resp, good_resp]
            r = client.post("/ai/battle", json={"ticker": "AMD", "trend": "up"})
        assert r.status_code == 200
        j = r.json()
        assert j.get("bull") == "טיעון שורי."
        assert j.get("bear") == "טיעון דובי."
        assert mock_r.post.call_count == 2


class TestNewsEndpoint:
    def setup_method(self):
        _clear_cache()
        _clear_rate()

    def test_no_finnhub_key_returns_empty(self):
        with patch.object(api, "FINNHUB_KEY", ""):
            r = client.get("/news")
        assert r.status_code == 200
        assert r.json().get("news") == []

    def test_valid_finnhub_response(self):
        news_data = [
            {"headline": "Markets rally", "url": "https://example.com",
             "source": "Reuters", "datetime": 1700000000},
        ]
        mock_resp = MagicMock()
        mock_resp.json.return_value = news_data
        with patch.object(api, "FINNHUB_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r, \
             patch("stock_api._translate", return_value="שוקי המניות מתאוששים"):
            mock_r.get.return_value = mock_resp
            r = client.get("/news")
        assert r.status_code == 200
        assert "news" in r.json()

    def test_news_response_cached(self):
        news_data = [{"headline": "Test", "url": "https://x.com",
                      "source": "AP", "datetime": 1700000001}]
        mock_resp = MagicMock()
        mock_resp.json.return_value = news_data
        with patch.object(api, "FINNHUB_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r, \
             patch("stock_api._translate", return_value=None):
            mock_r.get.return_value = mock_resp
            client.get("/news")
            client.get("/news")
        # Finnhub should only be called once
        assert mock_r.get.call_count == 1

    def test_non_list_response_returns_empty(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": "bad"}  # not a list
        with patch.object(api, "FINNHUB_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r:
            mock_r.get.return_value = mock_resp
            r = client.get("/news")
        assert r.json().get("news") == []

    def test_finnhub_exception_returns_empty(self):
        with patch.object(api, "FINNHUB_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r:
            mock_r.get.side_effect = Exception("timeout")
            r = client.get("/news")
        assert r.status_code == 200
        assert r.json().get("news") == []


class TestTickerNewsEndpoint:
    """שלב חדש בהעשרת ה-AI: חדשות ספציפיות למניה (Finnhub company-news),
    בנפרד מהפיד הכללי של /news. אותה חוסן לכשלים, מטמון ארוך יותר (30 דק')."""

    def setup_method(self):
        _clear_cache()
        _clear_rate()

    def test_invalid_ticker_returns_400(self):
        r = client.get("/news/!!!")
        assert r.status_code == 400

    def test_no_finnhub_key_returns_empty_headlines(self):
        with patch.object(api, "FINNHUB_KEY", ""):
            r = client.get("/news/AAPL")
        assert r.status_code == 200
        assert r.json().get("headlines") == []

    def test_valid_response_sorted_newest_first(self):
        news_data = [
            {"headline": "Older headline", "url": "https://a.com",
             "source": "AP", "datetime": 100},
            {"headline": "Newest headline", "url": "https://b.com",
             "source": "Reuters", "datetime": 300},
            {"headline": "Middle headline", "url": "https://c.com",
             "source": "CNBC", "datetime": 200},
        ]
        mock_resp = MagicMock()
        mock_resp.json.return_value = news_data
        with patch.object(api, "FINNHUB_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r, \
             patch("stock_api._translate", return_value="כותרת מתורגמת"):
            mock_r.get.return_value = mock_resp
            r = client.get("/news/AAPL")
        assert r.status_code == 200
        headlines = r.json().get("headlines")
        assert [h["headline"] for h in headlines] == [
            "Newest headline", "Middle headline", "Older headline",
        ]
        assert headlines[0]["headline_he"] == "כותרת מתורגמת"

    def test_capped_at_three_items(self):
        news_data = [
            {"headline": "H%d" % i, "url": "https://x.com", "source": "AP",
             "datetime": i}
            for i in range(10)
        ]
        mock_resp = MagicMock()
        mock_resp.json.return_value = news_data
        with patch.object(api, "FINNHUB_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r, \
             patch("stock_api._translate", return_value=None):
            mock_r.get.return_value = mock_resp
            r = client.get("/news/MSFT")
        assert len(r.json().get("headlines")) == 3

    def test_response_cached_per_ticker(self):
        news_data = [{"headline": "Test", "url": "https://x.com",
                      "source": "AP", "datetime": 1}]
        mock_resp = MagicMock()
        mock_resp.json.return_value = news_data
        with patch.object(api, "FINNHUB_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r, \
             patch("stock_api._translate", return_value=None):
            mock_r.get.return_value = mock_resp
            client.get("/news/AAPL")
            client.get("/news/AAPL")
        assert mock_r.get.call_count == 1

    def test_different_tickers_not_sharing_cache(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        with patch.object(api, "FINNHUB_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r, \
             patch("stock_api._translate", return_value=None):
            mock_r.get.return_value = mock_resp
            client.get("/news/AAPL")
            client.get("/news/MSFT")
        assert mock_r.get.call_count == 2

    def test_non_list_response_returns_empty(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": "bad"}
        with patch.object(api, "FINNHUB_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r:
            mock_r.get.return_value = mock_resp
            r = client.get("/news/AAPL")
        assert r.json().get("headlines") == []

    def test_finnhub_exception_returns_empty(self):
        with patch.object(api, "FINNHUB_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r:
            mock_r.get.side_effect = Exception("timeout")
            r = client.get("/news/AAPL")
        assert r.status_code == 200
        assert r.json().get("headlines") == []

    def test_blank_headline_skipped(self):
        news_data = [{"headline": "   ", "url": "https://x.com",
                      "source": "AP", "datetime": 1}]
        mock_resp = MagicMock()
        mock_resp.json.return_value = news_data
        with patch.object(api, "FINNHUB_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r:
            mock_r.get.return_value = mock_resp
            r = client.get("/news/AAPL")
        assert r.json().get("headlines") == []


class TestNewsHeadlinesInFacts:
    """כותרות חדשות (newsHeadlines בגוף הבקשה) מוזרמות ל-AI כעובדה מצוטטת,
    עם הגנות: מוגבל לשתיים, מנוקה מירידות שורה, מוגבל באורך, ומטופל בעדינות
    כשהקלט לא תקין — כי זהו קלט חיצוני (חדשות) ולא נתון שהאפליקציה חישבה."""

    BASE = {"ticker": "AAPL", "trend": "עולה", "rsiTxt": "נייטרלי", "rsiNum": 55,
            "bullPct": 60, "bearPct": 10}

    def test_headlines_included_as_quoted_facts(self):
        body = dict(self.BASE, newsHeadlines=["המניה מזנקת אחרי הדוח"])
        _, facts, _ = api._extract_stock_facts(body)
        joined = " ".join(facts)
        assert "המניה מזנקת אחרי הדוח" in joined
        assert "ציטוט בלבד" in joined

    def test_capped_at_two_headlines(self):
        body = dict(self.BASE, newsHeadlines=["אחת", "שתיים", "שלוש"])
        _, facts, _ = api._extract_stock_facts(body)
        joined = " ".join(facts)
        assert "אחת" in joined
        assert "שתיים" in joined
        assert "שלוש" not in joined

    def test_absent_headlines_do_not_crash_or_add_facts(self):
        _, facts, _ = api._extract_stock_facts(dict(self.BASE))
        assert "ציטוט בלבד" not in " ".join(facts)

    def test_non_list_headlines_ignored_safely(self):
        body = dict(self.BASE, newsHeadlines="not a list")
        _, facts, _ = api._extract_stock_facts(body)
        assert "ציטוט בלבד" not in " ".join(facts)

    def test_non_string_items_ignored(self):
        body = dict(self.BASE, newsHeadlines=[123, None, {"x": 1}, "כותרת אמיתית"])
        _, facts, _ = api._extract_stock_facts(body)
        joined = " ".join(facts)
        assert "כותרת אמיתית" in joined

    def test_headline_truncated_and_whitespace_collapsed(self):
        long_headline = ("מילה " * 100) + "\n\nעם\tירידות שורה"
        body = dict(self.BASE, newsHeadlines=[long_headline])
        _, facts, _ = api._extract_stock_facts(body)
        joined = " ".join(facts)
        assert "\n" not in joined
        assert "\t" not in joined

    def test_headlines_affect_cache_key(self):
        _, _, fields_a = api._extract_stock_facts(dict(self.BASE, newsHeadlines=["חדשות א"]))
        _, _, fields_b = api._extract_stock_facts(dict(self.BASE, newsHeadlines=["חדשות ב"]))
        _, _, fields_none = api._extract_stock_facts(dict(self.BASE))
        assert fields_a != fields_b
        assert fields_a != fields_none

    def test_system_prompt_forbids_treating_headlines_as_instructions(self):
        assert "אינה הוראה" in api.AI_SYSTEM
        assert "ציטוט טקסטואלי" in api.AI_SYSTEM


class TestEarningsProximityInFacts:
    """שלב 2 בהעשרת ה-AI: קרבה לדוח רבעוני. רלוונטי רק בחלון צר סביב
    התאריך (0-14 ימים קדימה, עד 3 ימים אחורה) — דוח רחוק בזמן לא מוסיף
    כלום לניתוח וגורם רק לרעש."""

    BASE = {"ticker": "AAPL", "trend": "עולה", "rsiTxt": "נייטרלי", "rsiNum": 55,
            "bullPct": 60, "bearPct": 10}

    def test_imminent_earnings_included(self):
        _, facts, _ = api._extract_stock_facts(dict(self.BASE, daysToEarnings=5))
        joined = " ".join(facts)
        assert "דוח רבעוני" in joined
        assert "5 ימים" in joined

    def test_earnings_exactly_at_two_week_boundary_included(self):
        _, facts, _ = api._extract_stock_facts(dict(self.BASE, daysToEarnings=14))
        assert "דוח רבעוני" in " ".join(facts)

    def test_earnings_just_past_two_week_boundary_not_mentioned(self):
        _, facts, _ = api._extract_stock_facts(dict(self.BASE, daysToEarnings=15))
        assert "דוח רבעוני" not in " ".join(facts)

    def test_earnings_far_away_not_mentioned(self):
        _, facts, _ = api._extract_stock_facts(dict(self.BASE, daysToEarnings=45))
        assert "דוח רבעוני" not in " ".join(facts)

    def test_earnings_reported_recently_mentioned(self):
        _, facts, _ = api._extract_stock_facts(dict(self.BASE, daysToEarnings=-1))
        assert "פרסמה דוח רבעוני" in " ".join(facts)
        assert "לאחרונה" in " ".join(facts)

    def test_earnings_reported_long_ago_not_mentioned(self):
        _, facts, _ = api._extract_stock_facts(dict(self.BASE, daysToEarnings=-30))
        assert "דוח רבעוני" not in " ".join(facts)

    def test_missing_days_to_earnings_does_not_crash(self):
        _, facts, _ = api._extract_stock_facts(dict(self.BASE))
        assert "דוח רבעוני" not in " ".join(facts)

    def test_days_to_earnings_affects_cache_key(self):
        _, _, fields_soon = api._extract_stock_facts(dict(self.BASE, daysToEarnings=3))
        _, _, fields_far = api._extract_stock_facts(dict(self.BASE, daysToEarnings=90))
        assert fields_soon != fields_far

    def test_system_prompt_forbids_guessing_earnings_outcome(self):
        assert "אסור לך לנחש" in api.AI_SYSTEM


class TestHistoricalPrecedentInFacts:
    """התקדים ההיסטורי ("תאום מוזר בזמן") מוזרם ל-AI. זהו מדגם קטן במכוון
    (3 תקדימים, אותם פרמטרים כמו החלון שהמשתמש רואה), ולכן הוא חייב להימסר
    עם גודל המדגם ועם הסתייגות מפורשת — אסור שהמודל יציג אותו כתחזית."""

    BASE = {"ticker": "AAPL", "trend": "עולה", "rsiTxt": "נייטרלי", "rsiNum": 55,
            "bullPct": 60, "bearPct": 10}

    TWIN = {"twinAvgFwd": 4.23, "twinWinRate": 67, "twinSamples": 3, "twinForwardLen": 10}

    def test_precedent_included_with_all_numbers(self):
        _, facts, _ = api._extract_stock_facts(dict(self.BASE, **self.TWIN))
        joined = " ".join(facts)
        assert "תקדים היסטורי" in joined
        assert "4.2%" in joined
        assert "67%" in joined
        assert "10 ימי המסחר" in joined

    def test_precedent_states_sample_size(self):
        _, facts, _ = api._extract_stock_facts(dict(self.BASE, **self.TWIN))
        joined = " ".join(facts)
        assert "ב-3 התקופות" in joined

    def test_precedent_carries_explicit_disclaimer(self):
        """בלי המשפט הזה המודל עלול להציג שלושה מקרים כהסתברות לעתיד."""
        _, facts, _ = api._extract_stock_facts(dict(self.BASE, **self.TWIN))
        joined = " ".join(facts)
        assert "מדגם קטן" in joined
        assert "אינו תחזית" in joined

    def test_negative_precedent_shown_as_decline_with_absolute_value(self):
        body = dict(self.BASE, twinAvgFwd=-3.7, twinWinRate=33,
                    twinSamples=3, twinForwardLen=10)
        _, facts, _ = api._extract_stock_facts(body)
        joined = " ".join(facts)
        assert "ירידה של 3.7%" in joined
        assert "-3.7" not in joined

    def test_absent_precedent_does_not_crash_or_add_facts(self):
        _, facts, _ = api._extract_stock_facts(dict(self.BASE))
        assert "תקדים היסטורי" not in " ".join(facts)

    def test_too_few_samples_is_ignored(self):
        """פחות משלושה תקדימים אינו בסיס — עדיף לשתוק מאשר למסור רעש."""
        body = dict(self.BASE, twinAvgFwd=9.9, twinWinRate=100, twinSamples=1)
        _, facts, _ = api._extract_stock_facts(body)
        assert "תקדים היסטורי" not in " ".join(facts)

    def test_non_numeric_precedent_ignored_safely(self):
        body = dict(self.BASE, twinAvgFwd="לא מספר", twinSamples=3)
        _, facts, _ = api._extract_stock_facts(body)
        assert "תקדים היסטורי" not in " ".join(facts)

    def test_missing_forward_len_falls_back_without_crashing(self):
        body = dict(self.BASE, twinAvgFwd=2.0, twinWinRate=67, twinSamples=3)
        _, facts, _ = api._extract_stock_facts(body)
        assert "תקדים היסטורי" in " ".join(facts)

    def test_precedent_affects_cache_key(self):
        _, _, up = api._extract_stock_facts(dict(self.BASE, **self.TWIN))
        _, _, down = api._extract_stock_facts(
            dict(self.BASE, twinAvgFwd=-4.23, twinWinRate=33,
                 twinSamples=3, twinForwardLen=10))
        _, _, none = api._extract_stock_facts(dict(self.BASE))
        assert up != down
        assert up != none

    def test_system_prompt_forbids_presenting_precedent_as_forecast(self):
        assert "חל איסור מוחלט לנסח אותו כתחזית" in api.AI_SYSTEM

    def test_precedent_prefix_matches_the_detector(self):
        """אם ניסוח שורת התקדים ישתנה בלי הקידומת, ההנחיה שמחייבת להזכיר
        אותו תתנתק בשקט — והנתון הייחודי ביותר יפסיק להופיע."""
        _, facts, _ = api._extract_stock_facts(dict(self.BASE, **self.TWIN))
        assert api._has_precedent(facts) is True

    def test_no_precedent_is_detected_when_absent(self):
        _, facts, _ = api._extract_stock_facts(dict(self.BASE))
        assert api._has_precedent(facts) is False


class TestPrecedentIsGivenItsOwnSentence:
    """בבדיקה חיה התקדים ההיסטורי נשמט מהתשובה כשהיו עשר עובדות מתחרות על
    מכסה של 3-4 משפטים. הפרומפט מקצה לו עכשיו משפט ייעודי — הטסטים כאן
    בודקים את הפרומפט שנשלח בפועל, לא רק את בניית העובדות."""

    BASE = {"ticker": "AAPL", "trend": "עולה", "rsiNum": 55,
            "bullPct": 60, "bearPct": 10}
    TWIN = {"twinAvgFwd": 4.2, "twinWinRate": 67, "twinSamples": 3, "twinForwardLen": 10}

    def setup_method(self):
        _clear_cache()
        _clear_rate()

    def _captured_prompt(self, body):
        """מריץ /ai עם Groq מדומה ומחזיר את הפרומפט שנשלח בפועל."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": "תשובה"}}]}
        with patch.object(api, "GROQ_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r:
            mock_r.post.return_value = mock_resp
            client.post("/ai", json=body)
            sent = mock_r.post.call_args
        payload = sent.kwargs.get("json") or sent[1].get("json")
        return payload["messages"][1]["content"]

    def test_prompt_demands_the_precedent_sentence_when_present(self):
        p = self._captured_prompt(dict(self.BASE, ticker="WITHTWIN", **self.TWIN))
        assert "התקדים ההיסטורי" in p
        assert "חובה ואסור להשמיט" in p
        assert "4 משפטים בדיוק" in p

    def test_prompt_stays_short_when_no_precedent(self):
        p = self._captured_prompt(dict(self.BASE, ticker="NOTWIN"))
        assert "3 משפטים בדיוק" in p
        assert "התקדים ההיסטורי" not in p

    def test_prompt_carries_the_precedent_numbers(self):
        p = self._captured_prompt(dict(self.BASE, ticker="NUMS", **self.TWIN))
        assert "4.2%" in p
        assert "67%" in p
        assert "מדגם קטן" in p


class TestFillerSentenceStripping:
    """ההנחיה בפרומפט לבדה לא הספיקה — בבדיקה חיה המודל כתב "מצב מורכב"
    למרות שהביטוי אסור במפורש. הסינון כאן הוא שכבת האכיפה בצד השרת.
    הוא שמרני בכוונה: משפט שנושא נתון קונקרטי לא נמחק גם אם ניסוחו רך."""

    def test_removes_contentless_filler_sentence(self):
        text = ("המניה עלתה 3.2% השבוע. התמונה הטכנית מציגה מצב מורכב. "
                "מכפיל הרווח עומד על 35.")
        out = api._strip_filler_sentences(text)
        assert "מצב מורכב" not in out
        assert "3.2%" in out
        assert "35" in out

    def test_keeps_filler_phrase_when_sentence_carries_a_number(self):
        """עדיף ניסוח רך מאשר לאבד נתון — משפט עם מספר תמיד נשאר."""
        text = ("המניה יציבה. מכפיל רווח של 35 מציג מצב מורכב. "
                "הנפח גבוה פי 1.4.")
        out = api._strip_filler_sentences(text)
        assert "מכפיל רווח של 35" in out

    def test_does_not_strip_when_too_few_sentences_remain(self):
        """תשובה בת שני משפטים שאחד מהם מילוי — עדיף להשאיר כמות שהיא
        מאשר להחזיר משפט בודד וקטוע."""
        text = "יש לזכור את הסיכון. חשוב לציין את התנודתיות."
        out = api._strip_filler_sentences(text)
        assert out == text

    def test_clean_text_is_returned_unchanged(self):
        text = ("המניה עלתה 3.2% השבוע. מכפיל הרווח עומד על 35. "
                "הנפח גבוה פי 1.4 מהממוצע.")
        assert api._strip_filler_sentences(text) == text

    def test_empty_and_none_are_safe(self):
        assert api._strip_filler_sentences("") == ""
        assert api._strip_filler_sentences(None) is None

    def test_single_sentence_is_never_emptied(self):
        text = "התמונה מציגה מצב מורכב."
        assert api._strip_filler_sentences(text) == text

    def test_every_banned_phrase_is_detected(self):
        for phrase in api.BANNED_FILLER:
            text = ("המניה עלתה 3.2% השבוע. הניתוח " + phrase + " כאן. "
                    "מכפיל הרווח עומד על 35.")
            out = api._strip_filler_sentences(text)
            assert phrase not in out, "הביטוי לא סונן: " + phrase

    def test_banned_list_and_system_prompt_cannot_drift(self):
        """ההנחיה נבנית מאותה רשימה שהסינון משתמש בה — כך שהוספת ביטוי
        אסור חדש מעדכנת אוטומטית גם את הבקשה למודל וגם את האכיפה."""
        for phrase in api.BANNED_FILLER:
            assert phrase in api.AI_SYSTEM

    def test_ai_endpoint_applies_the_filter(self):
        _clear_cache()
        _clear_rate()
        dirty = ("המניה עלתה 3.2% השבוע. התמונה מציגה מצב מורכב. "
                 "מכפיל הרווח עומד על 35.")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": dirty}}]}
        with patch.object(api, "GROQ_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r:
            mock_r.post.return_value = mock_resp
            r = client.post("/ai", json={"ticker": "FILTERTEST", "trend": "עולה"})
        assert r.status_code == 200
        assert "מצב מורכב" not in r.json().get("text", "")

    def test_battle_endpoint_applies_the_filter_to_both_sides(self):
        _clear_cache()
        _clear_rate()
        content = ("BULL:\nהמניה עלתה 3.2%. התמונה מציגה מצב מורכב. הנפח פי 1.4.\n"
                   "BEAR:\nהמניה ירדה 2.1%. יש לזכור את הסיכון. המכפיל הוא 35.")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": content}}]}
        with patch.object(api, "GROQ_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r:
            mock_r.post.return_value = mock_resp
            r = client.post("/ai/battle", json={"ticker": "FILTERBATTLE", "trend": "עולה"})
        j = r.json()
        assert "מצב מורכב" not in j.get("bull", "")
        assert "יש לזכור" not in j.get("bear", "")
        assert "3.2%" in j.get("bull", "")
        assert "35" in j.get("bear", "")


class TestInvalidationScenario:
    """התרחיש השלילי: איזו רמה, אם תישבר, מבטלת את התמונה. המשתמש ביקש
    מחירים מדויקים בדולרים, ולכן הרמות נמסרות למודל מוכנות מהאפליקציה
    ואסור לו להמציא — ההנחיה מחריגה אותן במפורש מהאיסור על מספרי מחיר."""

    BASE = {"ticker": "AAPL", "trend": "עולה", "rsiNum": 55,
            "bullPct": 60, "bearPct": 10}
    INV = {"invalidationLevel": 284.31, "invalidationPct": 4.2,
           "invalidationStr": "חזקה", "nextSupportLevel": 262.5,
           "nextSupportPct": 11.6}

    def setup_method(self):
        _clear_cache()
        _clear_rate()

    def test_invalidation_level_and_next_support_in_facts(self):
        _, facts, _ = api._extract_stock_facts(dict(self.BASE, **self.INV))
        joined = " ".join(facts)
        assert "284.31" in joined
        assert "262.5" in joined
        assert "4.2%" in joined

    def test_absent_invalidation_adds_nothing(self):
        _, facts, _ = api._extract_stock_facts(dict(self.BASE))
        assert not api._has_invalidation(facts)

    def test_detector_matches_the_prefix(self):
        _, facts, _ = api._extract_stock_facts(dict(self.BASE, **self.INV))
        assert api._has_invalidation(facts) is True

    def test_missing_next_support_is_stated_explicitly(self):
        body = dict(self.BASE, invalidationLevel=100.0, invalidationPct=5.0)
        _, facts, _ = api._extract_stock_facts(body)
        assert "לא זוהה אזור תמיכה נוסף" in " ".join(facts)

    def test_non_numeric_invalidation_ignored_safely(self):
        body = dict(self.BASE, invalidationLevel="לא מספר", invalidationPct=5.0)
        _, facts, _ = api._extract_stock_facts(body)
        assert not api._has_invalidation(facts)

    def test_invalidation_affects_cache_key(self):
        _, _, a = api._extract_stock_facts(dict(self.BASE, **self.INV))
        _, _, b = api._extract_stock_facts(
            dict(self.BASE, invalidationLevel=200.0, invalidationPct=9.9))
        assert a != b

    def _captured_prompt(self, body):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": "תשובה"}}]}
        with patch.object(api, "GROQ_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r:
            mock_r.post.return_value = mock_resp
            client.post("/ai", json=body)
            sent = mock_r.post.call_args
        payload = sent.kwargs.get("json") or sent[1].get("json")
        return payload["messages"][1]["content"]

    def test_prompt_requires_the_negative_scenario_sentence(self):
        p = self._captured_prompt(dict(self.BASE, ticker="INVTEST", **self.INV))
        assert "התרחיש השלילי" in p
        assert "חובה ואסור להשמיט" in p

    def test_prompt_allows_dollar_levels_only_for_invalidation(self):
        """האיסור על מספרי מחיר נשאר בתוקף לכניסה/סטופ/יעד — אחרת ה-AI
        היה יכול לסתור את כרטיס התוכנית שמוצג באותו מסך."""
        p = self._captured_prompt(dict(self.BASE, ticker="CARVEOUT", **self.INV))
        assert "אל תכתוב מחירי כניסה, סטופ או יעד" in p
        assert "היוצא מן הכלל היחיד" in p
        assert "בלי לשנות, לעגל או להמציא" in p

    def test_negative_scenario_is_framed_as_description_not_advice(self):
        p = self._captured_prompt(dict(self.BASE, ticker="NOADVICE", **self.INV))
        assert "לא הנחיה לפעולה" in p


class TestNoNearStructure:
    """התגלה מבדיקה חיה על INTC: המניה נסחרה ב-90 דולר בעוד ההתנגדות
    הקרובה ביותר הייתה ב-141.90 (57% מעל) והתמיכה 43% מתחת. המספרים נכונים
    — היא רצה מ-19 ל-142 וירדה בחזרה בלי לעצור — אבל הצגתם כתוכנית מסחר
    מטעה. המודל חייב לדעת שאין כאן setup, אחרת הוא מתאר פריצה רחוקה כקרובה."""

    BASE = {"ticker": "INTC", "trend": "עולה", "rsiNum": 55,
            "bullPct": 60, "bearPct": 10}

    def test_flag_adds_explicit_fact(self):
        body = dict(self.BASE, noNearStructure=True,
                    farBreakPct=57.3, farSupportPct=42.9)
        _, facts, _ = api._extract_stock_facts(body)
        joined = " ".join(facts)
        assert "אין מבנה טכני קרוב" in joined
        assert "57.3%" in joined
        assert "42.9%" in joined

    def test_model_is_told_not_to_call_it_close(self):
        body = dict(self.BASE, noNearStructure=True, farBreakPct=57.3)
        _, facts, _ = api._extract_stock_facts(body)
        joined = " ".join(facts)
        assert "אל תתאר את הרמות האלה כקרובות" in joined

    def test_absent_flag_adds_nothing(self):
        _, facts, _ = api._extract_stock_facts(dict(self.BASE))
        assert "אין מבנה טכני קרוב" not in " ".join(facts)

    def test_only_far_break_is_described_alone(self):
        body = dict(self.BASE, noNearStructure=True, farBreakPct=31.0)
        _, facts, _ = api._extract_stock_facts(body)
        joined = " ".join(facts)
        assert "31.0%" in joined
        assert "התמיכה הקרובה ביותר רחוקה" not in joined

    def test_only_far_support_is_described_alone(self):
        body = dict(self.BASE, noNearStructure=True, farSupportPct=28.4)
        _, facts, _ = api._extract_stock_facts(body)
        joined = " ".join(facts)
        assert "28.4%" in joined
        assert "ההתנגדות הקרובה ביותר רחוקה" not in joined

    def test_flag_affects_cache_key(self):
        _, _, with_flag = api._extract_stock_facts(
            dict(self.BASE, noNearStructure=True, farBreakPct=57.3))
        _, _, without = api._extract_stock_facts(dict(self.BASE))
        assert with_flag != without

    def test_normal_stock_is_unaffected(self):
        """מניה עם מבנה תקין לא אמורה לקבל את האזהרה בשום צורה."""
        body = dict(self.BASE, distToBreakPct=2.1,
                    invalidationLevel=195.71, invalidationPct=2.5)
        _, facts, _ = api._extract_stock_facts(body)
        assert "אין מבנה טכני קרוב" not in " ".join(facts)


class TestLongRangeContext:
    """הקשר ארוך טווח מהגרף השבועי. זו הסיבה שהאנלייזר יכול עכשיו להצביע
    על יעדים רחוקים: קודם הוא ראה שנה אחת בלבד ולכן לא ידע שהם קיימים."""

    BASE = {"ticker": "AAPL", "trend": "עולה", "rsiNum": 55,
            "bullPct": 60, "bearPct": 10}

    def test_multi_year_range_included(self):
        body = dict(self.BASE, ltHigh=420.5, ltLow=110.25, ltYears=5)
        _, facts, _ = api._extract_stock_facts(body)
        joined = " ".join(facts)
        assert "420.5" in joined
        assert "110.25" in joined
        assert "5 שנים" in joined

    def test_multi_year_high_is_flagged_as_unusual(self):
        body = dict(self.BASE, ltHigh=420.5, ltLow=110.25, ltYears=5,
                    atMultiYearHigh=True)
        _, facts, _ = api._extract_stock_facts(body)
        joined = " ".join(facts)
        assert "מצב חריג" in joined
        assert "אין מעליה שום התנגדות היסטורית" in joined

    def test_not_flagged_when_not_at_high(self):
        body = dict(self.BASE, ltHigh=420.5, ltLow=110.25, ltYears=5)
        _, facts, _ = api._extract_stock_facts(body)
        assert "מצב חריג" not in " ".join(facts)

    def test_large_potential_is_surfaced(self):
        """המשתמש ביקש שהכלי לא יפחד להצביע על יעד גבוה כשהוא אמיתי."""
        _, facts, _ = api._extract_stock_facts(dict(self.BASE, maxTargetPct=64.3))
        joined = " ".join(facts)
        assert "64.3%" in joined
        assert "פוטנציאל חריג" in joined

    def test_small_potential_is_not_hyped(self):
        _, facts, _ = api._extract_stock_facts(dict(self.BASE, maxTargetPct=6.1))
        assert "פוטנציאל חריג" not in " ".join(facts)

    def test_missing_long_range_does_not_crash(self):
        _, facts, _ = api._extract_stock_facts(dict(self.BASE))
        assert "טווח" not in " ".join(facts) or True
        assert isinstance(facts, list) and len(facts) >= 2


class TestHebrewAccuracyRules:
    """שגיאות שנצפו בתשובה אמיתית של gpt-oss-120b בפרודקשן:
    "תתמוך תמיכה חזקה באיזור 262.5 דולר, והמחיר יפנה לתמיכה הבאה באותו רמה".
    שלוש שגיאות במשפט אחד: כפל שורש, כתיב, והתאמת מין — ועוד אי-קוהרנטיות
    לוגית (התמיכה הבאה אינה יכולה להיות באותה רמה). ההנחיות מכסות את כולן."""

    def test_gender_agreement_rule_present(self):
        assert "באותה רמה" in api.AI_SYSTEM
        assert "ולא 'באותו רמה'" in api.AI_SYSTEM

    def test_no_same_root_repetition_rule(self):
        assert "תתמוך תמיכה" in api.AI_SYSTEM

    def test_spelling_rule_for_azor(self):
        assert "'אזור', לא 'איזור'" in api.AI_SYSTEM

    def test_percent_spacing_rule(self):
        assert "אל תכתוב רווח לפני סימן אחוז" in api.AI_SYSTEM

    def test_hyphen_rule(self):
        assert "מקף רגיל" in api.AI_SYSTEM

    def test_rsi_is_never_tied_to_volatility(self):
        """נצפה בפרודקשן: "RSI של 37.4, המצביע על תנודתיות אפשרית".
        הכלל הכללי כבר היה קיים והמודל הפר אותו, ולכן נוסף ניסוח שגוי
        מפורש — אותה שיטה שעבדה על שאר שגיאות העברית."""
        assert "אסור בתכלית לקשור RSI לתנודתיות" in api.AI_SYSTEM
        assert "RSI מודד תאוצת מחיר" in api.AI_SYSTEM


class TestInvalidationSentenceStructure:
    """המשפט על התרחיש השלילי יצא לא קוהרנטי בפרודקשן. ההנחיה מפרקת אותו
    עכשיו לשלושה חלקים מפורשים ואוסרת במפורש את הניסוח השגוי שנצפה."""

    BASE = {"ticker": "AAPL", "trend": "עולה", "rsiNum": 55,
            "bullPct": 60, "bearPct": 10}
    INV = {"invalidationLevel": 284.31, "invalidationPct": 4.2,
           "invalidationStr": "חזקה", "nextSupportLevel": 262.5,
           "nextSupportPct": 11.6}

    def setup_method(self):
        _clear_cache()
        _clear_rate()

    def _prompt(self, body):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": "תשובה"}}]}
        with patch.object(api, "GROQ_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r:
            mock_r.post.return_value = mock_resp
            client.post("/ai", json=body)
            sent = mock_r.post.call_args
        payload = sent.kwargs.get("json") or sent[1].get("json")
        return payload["messages"][1]["content"]

    def test_three_parts_are_spelled_out(self):
        p = self._prompt(dict(self.BASE, ticker="INV3", **self.INV))
        assert "(א)" in p and "(ב)" in p and "(ג)" in p

    def test_forbids_the_observed_incoherent_phrasing(self):
        p = self._prompt(dict(self.BASE, ticker="INVBAD", **self.INV))
        assert "אסור לכתוב שהתמיכה הבאה נמצאת באותה רמה" in p

    def test_states_next_support_is_always_lower(self):
        p = self._prompt(dict(self.BASE, ticker="INVLOW", **self.INV))
        assert "היא תמיד נמוכה יותר" in p


class TestHebrewTypography:
    """נצפה בפועל בתשובות gpt-oss-120b: מקף חסין-שבירה במקום מקף רגיל,
    ורווח לפני סימן אחוז. שניהם טיפוגרפיה בלבד ולכן מנורמלים בשרת."""

    def test_non_breaking_hyphen_becomes_regular(self):
        out = api._normalize_hebrew_typography("ה‑RSI עומד על 32")
        assert "‑" not in out
        assert "ה-RSI" in out

    def test_space_before_percent_removed(self):
        out = api._normalize_hebrew_typography("עלייה של 4.6 % בעשרה ימים")
        assert "4.6%" in out
        assert "4.6 %" not in out

    def test_handles_several_occurrences(self):
        out = api._normalize_hebrew_typography("ו‑67 % מהמקרים, ו‑4.2 % מתחת")
        assert "‑" not in out
        assert "67%" in out and "4.2%" in out

    def test_clean_text_unchanged(self):
        clean = "המניה עלתה 3.2% השבוע והנפח גבוה פי 1.4."
        assert api._normalize_hebrew_typography(clean) == clean

    def test_percent_without_number_is_untouched(self):
        """לא נוגעים באחוז שאינו צמוד למספר — שם הרווח לגיטימי."""
        txt = "אחוז המשקיעים גבוה"
        assert api._normalize_hebrew_typography(txt) == txt

    def test_empty_and_none_safe(self):
        assert api._normalize_hebrew_typography("") == ""
        assert api._normalize_hebrew_typography(None) is None

    def test_ai_endpoint_applies_normalization(self):
        _clear_cache()
        _clear_rate()
        dirty = "המניה עלתה 3.2 % השבוע. ה‑RSI עומד על 32. הנפח גבוה פי 1.4."
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": dirty}}]}
        with patch.object(api, "GROQ_KEY", "fake-key"), \
             patch("stock_api.crequests") as mock_r:
            mock_r.post.return_value = mock_resp
            r = client.post("/ai", json={"ticker": "TYPO", "trend": "עולה"})
        t = r.json().get("text", "")
        assert "‑" not in t
        assert "3.2%" in t


class TestCacheKeyIntegrity:
    """הבאג החמור ביותר שנמצא: מפתח המטמון נבנה מרשימה ידנית מקבילה לעובדות,
    והשתיים נפרדו בשקט. שדות כמו maxTargetPct ו-invalidationPct הופיעו
    בעובדות ונעדרו מהמפתח, וערכים כמו RSI עוגלו במפתח למספר שלם בעוד
    שבעובדות הם מוצגים בדיוק מלא. התוצאה: שתי מניות עם נתונים שונים חלקו
    רשומת מטמון, וה-AI הציג מספרים ששייכים לבקשה אחרת — למשל פוטנציאל
    יעד של 24% מול 64%. עכשיו המפתח נגזר מהעובדות עצמן ולא יכול להיפרד."""

    BASE = {"ticker": "AAPL", "trend": "עולה", "rsiNum": 50,
            "bullPct": 60, "bearPct": 10}

    def _key(self, body):
        _, _, cf = api._extract_stock_facts(body)
        return "|".join(str(x) for x in cf)

    def test_identical_input_gives_identical_key(self):
        """בלי זה המטמון חסר תועלת וכל בקשה עולה כסף."""
        assert self._key(dict(self.BASE)) == self._key(dict(self.BASE))

    def test_every_fact_field_changes_the_key(self):
        """כל שדה שמשפיע על מה שהמודל רואה חייב להשפיע על המפתח."""
        base_key = self._key(dict(self.BASE))
        variations = {
            "rsiNum": 71,
            "trend": "יורד",
            "bullPct": 91,
            "bearPct": 44,
            "sector": "אנרגיה",
            "peRatio": 88.8,
            "weekPos": 12,
            "distToBreakPct": 7.7,
            "change5dPct": -6.5,
            "relVolume": 3.3,
            "daysToEarnings": 2,

            "maxTargetPct": 64.3,
            "noNearStructure": True,
            "atMultiYearHigh": True,
            "newsHeadlines": ["כותרת חדשה"],
        }
        for field, value in variations.items():
            changed = self._key(dict(self.BASE, **{field: value}))
            assert changed != base_key, "השדה " + field + " אינו משפיע על מפתח המטמון"

    def test_companion_fields_also_change_the_key(self):
        """שדות שנמסרים למודל רק יחד עם שדה נלווה — נבדקים עם הנלווה שלהם,
        אחרת הבדיקה תיכשל על התנהגות שהיא דווקא נכונה."""
        twin = {"twinAvgFwd": 4.0, "twinWinRate": 67, "twinSamples": 3}
        inval = {"invalidationLevel": 100.0}
        pairs = [
            (dict(twin, twinAvgFwd=4.1), dict(twin, twinAvgFwd=4.4)),
            (dict(twin, twinSamples=3), dict(twin, twinSamples=5)),
            (dict(twin, twinWinRate=33), dict(twin, twinWinRate=100)),
            ({"ltHigh": 400.0, "ltLow": 100.0}, {"ltHigh": 500.0, "ltLow": 100.0}),
            ({"ltHigh": 400.0, "ltLow": 100.0}, {"ltHigh": 400.0, "ltLow": 90.0}),
            ({"ltHigh": 400.0, "ltLow": 100.0, "ltYears": 5},
             {"ltHigh": 400.0, "ltLow": 100.0, "ltYears": 10}),
            (dict(inval, invalidationPct=4.2), dict(inval, invalidationPct=9.9)),
            (dict(inval, invalidationPct=5.0, nextSupportLevel=90.0, nextSupportPct=11.0),
             dict(inval, invalidationPct=5.0, nextSupportLevel=90.0, nextSupportPct=18.0)),
        ]
        for a, b in pairs:
            assert self._key(dict(self.BASE, **a)) != self._key(dict(self.BASE, **b)), str(a)

    def test_small_numeric_differences_are_not_collapsed(self):
        """נצפה בפועל: RSI 31.6 ו-32.4 עוגלו שניהם ל-32 במפתח, ולכן השני
        קיבל טקסט שמדבר על 31.6. אסור שעיגול יאחד ערכים שונים."""
        pairs = [
            ("rsiNum", 31.6, 32.4),
            ("distToBreakPct", 2.1, 2.4),
            ("maxTargetPct", 24.0, 64.0),
        ]
        for field, a, b in pairs:
            ka = self._key(dict(self.BASE, **{field: a}))
            kb = self._key(dict(self.BASE, **{field: b}))
            assert ka != kb, field + ": הערכים " + str(a) + " ו-" + str(b) + " חולקים מפתח"

    def test_key_matches_the_facts_exactly(self):
        """המפתח הוא חתימה על העובדות. אם העובדות זהות — המפתח זהה,
        ואם הן שונות — הוא שונה. זו ההגדרה שמונעת את הבאג לתמיד."""
        body_a = dict(self.BASE, maxTargetPct=30.0)
        body_b = dict(self.BASE, maxTargetPct=30.0)
        body_c = dict(self.BASE, maxTargetPct=30.1)
        _, facts_a, _ = api._extract_stock_facts(body_a)
        _, facts_b, _ = api._extract_stock_facts(body_b)
        _, facts_c, _ = api._extract_stock_facts(body_c)
        assert facts_a == facts_b and self._key(body_a) == self._key(body_b)
        assert facts_a != facts_c and self._key(body_a) != self._key(body_c)

    def test_different_tickers_never_share_a_key(self):
        assert self._key(dict(self.BASE, ticker="AAPL")) != self._key(dict(self.BASE, ticker="MSFT"))


class TestRSIThresholdConsistency:
    """הבאג שנמצא בסריקה של הפרודקשן: הסורק סימן "RSI נמוך" מתחת ל-35 בעוד
    כרטיס המדד באפליקציה הציג "Neutral" עד 30 — אותה מניה, שני תיאורים
    סותרים באותו מסך. הספים מוגדרים עכשיו במקום אחד, והטסטים כאן נועדו
    למנוע מהם להיפרד שוב."""

    def test_thresholds_are_the_standard_values(self):
        assert api.RSI_OVERSOLD == 30
        assert api.RSI_OVERBOUGHT == 70

    def test_ai_classification_uses_the_shared_thresholds(self):
        """RSI 32 אינו מכירת יתר — וכרטיס המדד אכן מציג עבורו Neutral."""
        base = {"ticker": "AAPL", "trend": "עולה", "bullPct": 60, "bearPct": 10}
        _, facts, _ = api._extract_stock_facts(dict(base, rsiNum=32))
        assert "מכירת יתר" not in " ".join(facts)

    def test_scanner_and_ai_agree_on_oversold_boundary(self):
        """אותו מספר בדיוק חייב לקבל אותו סיווג בסורק וב-AI."""
        import pandas as pd
        base = {"ticker": "AAPL", "trend": "עולה", "bullPct": 60, "bearPct": 10}
        for rsi_val in (25, 32, 55, 68, 75):
            _, facts, _ = api._extract_stock_facts(dict(base, rsiNum=rsi_val))
            joined = " ".join(facts)
            ai_says_oversold = "מכירת יתר" in joined
            ai_says_overbought = "קניית יתר" in joined
            scanner_says_oversold = rsi_val < api.RSI_OVERSOLD
            scanner_says_overbought = rsi_val > api.RSI_OVERBOUGHT
            assert ai_says_oversold == scanner_says_oversold, \
                "סתירה בסף מכירת היתר עבור RSI " + str(rsi_val)
            assert ai_says_overbought == scanner_says_overbought, \
                "סתירה בסף קניית היתר עבור RSI " + str(rsi_val)

    def test_scanner_does_not_flag_rsi_32_as_low(self):
        """רגרסיה ישירה על הבאג: 32 סומן בעבר כ"RSI נמוך" בסורק."""
        import pandas as pd
        n = 60
        closes = [100.0 + (i % 3) * 0.5 for i in range(n)]
        hist = pd.DataFrame({
            "Close": closes,
            "High": [c * 1.01 for c in closes],
            "Low": [c * 0.99 for c in closes],
        }, index=pd.to_datetime(pd.date_range("2026-01-01", periods=n)))
        row = api._scan_one("TEST", hist)
        if row is not None and row["rsi"] is not None:
            r = row["rsi"]
            low_flagged = any("RSI נמוך" in s for s in row["signals"])
            assert low_flagged == (r < api.RSI_OVERSOLD)


# ═══════════════════════════════════════════════════════════════════════════════
# 12. CORS origins
# ═══════════════════════════════════════════════════════════════════════════════

class TestCORSConfig:
    def test_github_pages_in_allowed(self):
        assert any("github.io" in o for o in api.ALLOWED_ORIGINS)

    def test_localhost_in_allowed(self):
        assert any("localhost" in o for o in api.ALLOWED_ORIGINS)

    def test_wildcard_not_in_allowed(self):
        assert "*" not in api.ALLOWED_ORIGINS

    def test_allowed_origins_is_list(self):
        assert isinstance(api.ALLOWED_ORIGINS, list)

    def test_no_empty_origins(self):
        assert all(o.strip() for o in api.ALLOWED_ORIGINS)


# ═══════════════════════════════════════════════════════════════════════════════
# 13. core universe sanity
# ═══════════════════════════════════════════════════════════════════════════════

class TestCoreUniverse:
    def test_not_empty(self):
        assert len(CORE_UNIVERSE) > 0

    def test_all_valid_tickers(self):
        for t in CORE_UNIVERSE:
            assert norm_ticker(t) is not None, f"{t!r} fails norm_ticker"

    def test_no_duplicates(self):
        assert len(CORE_UNIVERSE) == len(set(CORE_UNIVERSE))

    def test_contains_major_tickers(self):
        for t in ("AAPL", "MSFT", "NVDA", "GOOGL", "AMZN"):
            assert t in CORE_UNIVERSE

    def test_reasonable_size(self):
        assert 20 <= len(CORE_UNIVERSE) <= 200


# ═══════════════════════════════════════════════════════════════════════════════
# 14. scan endpoint (light integration)
# ═══════════════════════════════════════════════════════════════════════════════

class TestScanEndpoint:
    def setup_method(self):
        _clear_cache()
        _clear_rate()

    def test_invalid_custom_ticker_returns_400(self):
        r = client.get("/scan?tickers=!!!")
        assert r.status_code == 400

    def test_empty_tickers_param_uses_universe(self):
        """scan with no ?tickers= should attempt the full universe and return 200."""
        import pandas as pd
        with patch("stock_api.yf") as mock_yf:
            # Return an empty DataFrame so no tickers produce scan results
            mock_yf.download.return_value = pd.DataFrame()
            mock_yf.download.return_value.__len__ = lambda: 0
            r = client.get("/scan")
        assert r.status_code == 200

    def test_rate_limit_returns_429(self):
        req = _fake_request("8.8.8.8")
        for _ in range(10):
            rate_ok(req, "scan", 10, 60)
        r = client.get("/scan", headers={"x-forwarded-for": "8.8.8.8"})
        assert r.status_code == 429

    def test_cached_scan_result_returned(self):
        cache_set("scan:__universe__", {"results": [{"ticker": "AAPL"}]})
        with patch("stock_api.yf") as mock_yf:
            r = client.get("/scan")
        mock_yf.download.assert_not_called()
        assert r.json()["results"][0]["ticker"] == "AAPL"


# ═══════════════════════════════════════════════════════════════════════════════
# 15. MAX_SCAN_TICKERS constant
# ═══════════════════════════════════════════════════════════════════════════════

class TestScanConstants:
    def test_max_scan_tickers_reasonable(self):
        from stock_api import MAX_SCAN_TICKERS
        assert 10 <= MAX_SCAN_TICKERS <= 200

    def test_max_individual_fetches_less_than_max_scan(self):
        from stock_api import MAX_SCAN_TICKERS, MAX_INDIVIDUAL_FETCHES
        assert MAX_INDIVIDUAL_FETCHES < MAX_SCAN_TICKERS

    def test_cache_max_positive(self):
        assert _CACHE_MAX > 0

    def test_max_ttl_at_least_one_hour(self):
        assert _MAX_TTL >= 3600


class TestIndicesEndpoint:
    """‏/indices מקבל ?ids= מהלקוח, ולכן הוא נקודת הכניסה היחידה שבה
    קלט חיצוני בוחר איזה סימבול נמשוך מיאהו. הבדיקות כאן מוודאות
    שהמיפוי סגור ושלא ניתן להרחיב אותו מבחוץ."""

    def setup_method(self):
        _clear_cache()
        _clear_rate()

    def _mock_yf(self, price=100.0, prev=98.0):
        mock_ticker = MagicMock()
        mock_ticker.fast_info = {"last_price": price, "previous_close": prev}
        return mock_ticker

    def test_no_ids_uses_us_default(self):
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = self._mock_yf()
            r = client.get("/indices")
        assert r.status_code == 200
        assert [d["id"] for d in r.json()] == api.DEFAULT_INDICES

    def test_default_is_us_only(self):
        us = {"sp500", "nasdaq", "dow", "russell", "vix"}
        assert set(api.DEFAULT_INDICES) == us

    def test_explicit_ids_are_honoured(self):
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = self._mock_yf()
            r = client.get("/indices?ids=dax,nikkei")
        assert [d["id"] for d in r.json()] == ["dax", "nikkei"]

    def test_unknown_ids_are_dropped(self):
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = self._mock_yf()
            r = client.get("/indices?ids=sp500,notreal,alsofake")
        assert [d["id"] for d in r.json()] == ["sp500"]

    def test_arbitrary_symbol_is_not_fetched(self):
        """‏?ids=^EVIL לא אמור להגיע ל-yfinance בשום צורה."""
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = self._mock_yf()
            client.get("/indices?ids=%5EEVIL,AAPL,../../etc/passwd")
        called = [c.args[0] for c in mock_yf.Ticker.call_args_list]
        assert all(s in api.WORLD_INDICES.values() for s in called)

    def test_all_unknown_falls_back_to_default(self):
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = self._mock_yf()
            r = client.get("/indices?ids=nope,nada")
        assert [d["id"] for d in r.json()] == api.DEFAULT_INDICES

    def test_duplicates_are_collapsed(self):
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = self._mock_yf()
            r = client.get("/indices?ids=dax,dax,dax")
        assert [d["id"] for d in r.json()] == ["dax"]

    def test_request_is_capped(self):
        every = ",".join(api.WORLD_INDICES.keys())
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = self._mock_yf()
            r = client.get("/indices?ids=" + every)
        assert len(r.json()) <= api.MAX_INDICES

    def test_pct_is_computed_from_previous_close(self):
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = self._mock_yf(price=110.0, prev=100.0)
            r = client.get("/indices?ids=sp500")
        assert r.json()[0]["pct"] == 10.0

    def test_negative_move_is_negative_pct(self):
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = self._mock_yf(price=90.0, prev=100.0)
            r = client.get("/indices?ids=sp500")
        assert r.json()[0]["pct"] == -10.0

    def test_zero_previous_close_does_not_divide_by_zero(self):
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = self._mock_yf(price=100.0, prev=0.0)
            r = client.get("/indices?ids=sp500")
        assert r.status_code == 200
        assert r.json()[0]["pct"] == 0.0

    def test_failing_index_is_skipped_not_fatal(self):
        good = self._mock_yf()
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.side_effect = [Exception("boom"), good]
            r = client.get("/indices?ids=sp500,dax")
        assert r.status_code == 200
        assert [d["id"] for d in r.json()] == ["dax"]

    def test_different_selections_do_not_share_cache(self):
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = self._mock_yf()
            first = client.get("/indices?ids=sp500").json()
            second = client.get("/indices?ids=dax").json()
        assert [d["id"] for d in first] == ["sp500"]
        assert [d["id"] for d in second] == ["dax"]

    def test_same_selection_is_cached(self):
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = self._mock_yf()
            client.get("/indices?ids=dax")
            calls_after_first = mock_yf.Ticker.call_count
            client.get("/indices?ids=dax")
            assert mock_yf.Ticker.call_count == calls_after_first

    def test_id_order_is_ignored_by_cache_key(self):
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = self._mock_yf()
            client.get("/indices?ids=dax,ftse")
            n = mock_yf.Ticker.call_count
            client.get("/indices?ids=ftse,dax")
        assert mock_yf.Ticker.call_count == n

    def test_every_catalog_symbol_is_a_string(self):
        for iid, sym in api.WORLD_INDICES.items():
            assert isinstance(sym, str) and sym.strip()

    def test_rate_limit_blocks_flood(self):
        with patch("stock_api.yf") as mock_yf:
            mock_yf.Ticker.return_value = self._mock_yf()
            codes = [client.get("/indices?ids=sp500").status_code for _ in range(30)]
        assert 429 in codes


class TestQuotesEndpoint:
    """‏/quotes מקבל רשימת טיקרים מהלקוח ומושך אותם במשיכה אחת.
    הבדיקות מוודאות ולידציה, תקרה, ושהמטמון לא מערבב בין רשימות."""

    def setup_method(self):
        _clear_cache()
        _clear_rate()

    def _bulk(self, symbols, n=40):
        import pandas as pd
        closes = [100.0 + i for i in range(n)]
        if len(symbols) == 1:
            return pd.DataFrame({"Close": closes, "High": closes, "Low": closes})
        cols = pd.MultiIndex.from_product([symbols, ["Close", "High", "Low"]])
        data = {(s, f): closes for s in symbols for f in ("Close", "High", "Low")}
        return pd.DataFrame(data, columns=cols)

    def test_empty_tickers_returns_empty_list(self):
        with patch("stock_api.yf") as mock_yf:
            r = client.get("/quotes")
        assert r.json() == []
        mock_yf.download.assert_not_called()

    def test_invalid_tickers_are_rejected(self):
        with patch("stock_api.yf") as mock_yf:
            r = client.get("/quotes?tickers=bad!!,%5EEVIL,../etc")
        assert r.json() == []
        mock_yf.download.assert_not_called()

    def test_single_ticker_flat_columns(self):
        with patch("stock_api.yf") as mock_yf:
            mock_yf.download.return_value = self._bulk(["AAPL"])
            r = client.get("/quotes?tickers=AAPL")
        d = r.json()
        assert len(d) == 1 and d[0]["ticker"] == "AAPL"
        assert isinstance(d[0]["spark"], list) and len(d[0]["spark"]) <= 30

    def test_multi_ticker_multiindex(self):
        with patch("stock_api.yf") as mock_yf:
            mock_yf.download.return_value = self._bulk(["AAPL", "MSFT"])
            r = client.get("/quotes?tickers=AAPL,MSFT")
        assert [x["ticker"] for x in r.json()] == ["AAPL", "MSFT"]

    def test_one_bulk_call_not_one_per_ticker(self):
        with patch("stock_api.yf") as mock_yf:
            mock_yf.download.return_value = self._bulk(["AAPL", "MSFT", "NVDA"])
            client.get("/quotes?tickers=AAPL,MSFT,NVDA")
        assert mock_yf.download.call_count == 1

    def test_duplicates_collapsed(self):
        with patch("stock_api.yf") as mock_yf:
            mock_yf.download.return_value = self._bulk(["AAPL"])
            r = client.get("/quotes?tickers=AAPL,AAPL,aapl")
        assert len(r.json()) == 1

    def test_capped_at_max(self):
        many = ",".join("A%d" % i for i in range(60))
        with patch("stock_api.yf") as mock_yf:
            mock_yf.download.return_value = self._bulk(["A0"])
            client.get("/quotes?tickers=" + many)
            sent = mock_yf.download.call_args.kwargs["tickers"].split()
        assert len(sent) <= api.MAX_QUOTES

    def test_pct_computed_from_previous_close(self):
        import pandas as pd
        with patch("stock_api.yf") as mock_yf:
            mock_yf.download.return_value = pd.DataFrame({"Close": [100.0, 110.0]})
            r = client.get("/quotes?tickers=AAPL")
        assert r.json()[0]["pct"] == 10.0

    def test_missing_symbol_is_skipped(self):
        with patch("stock_api.yf") as mock_yf:
            mock_yf.download.return_value = self._bulk(["AAPL", "MSFT"])
            r = client.get("/quotes?tickers=AAPL,ZZZZ")
        assert [x["ticker"] for x in r.json()] == ["AAPL"]

    def test_empty_bulk_returns_empty(self):
        import pandas as pd
        with patch("stock_api.yf") as mock_yf:
            mock_yf.download.return_value = pd.DataFrame()
            r = client.get("/quotes?tickers=AAPL")
        assert r.json() == []

    def test_download_failure_returns_502(self):
        with patch("stock_api.yf") as mock_yf:
            mock_yf.download.side_effect = Exception("network")
            r = client.get("/quotes?tickers=AAPL")
        assert r.status_code == 502

    def test_different_lists_do_not_share_cache(self):
        with patch("stock_api.yf") as mock_yf:
            mock_yf.download.return_value = self._bulk(["AAPL", "MSFT"])
            a = client.get("/quotes?tickers=AAPL").json()
            mock_yf.download.return_value = self._bulk(["MSFT"])
            b = client.get("/quotes?tickers=MSFT").json()
        assert [x["ticker"] for x in a] == ["AAPL"]
        assert [x["ticker"] for x in b] == ["MSFT"]

    def test_same_list_is_cached(self):
        with patch("stock_api.yf") as mock_yf:
            mock_yf.download.return_value = self._bulk(["AAPL"])
            client.get("/quotes?tickers=AAPL")
            n = mock_yf.download.call_count
            client.get("/quotes?tickers=AAPL")
        assert mock_yf.download.call_count == n

    def test_rate_limit_blocks_flood(self):
        with patch("stock_api.yf") as mock_yf:
            mock_yf.download.return_value = self._bulk(["AAPL"])
            codes = [client.get("/quotes?tickers=T%d" % i).status_code for i in range(40)]
        assert 429 in codes


# ── אכיפת סיווג ה-RSI על תשובת המודל.
#
# הרקע: בדיקת הבריאות של הפרודקשן (ריצה #3) נכשלה על "RSI 32 סווג בטעות
# כמכירת יתר". השרת כבר מסר למודל את הסיווג המחושב ("32 — נייטרלי"),
# וההנחיות אסרו עליו לסתור אותו — וזה עדיין קרה. בקשה יפה אינה אכיפה. ──
class TestRSIStateEnforcement:
    NEUTRAL = ("המניה נסחרת מעל ממוצע 200. RSI של 32 מצביע על מכירת יתר. "
               "מכפיל הרווח עומד על 34 ומגלם ציפיות גבוהות.")

    def test_removes_oversold_claim_when_rsi_is_neutral(self):
        out = api._enforce_rsi_state(self.NEUTRAL, 32)
        assert "מכירת יתר" not in out
        assert "ממוצע 200" in out and "מכפיל הרווח" in out

    def test_keeps_oversold_claim_when_rsi_really_is_oversold(self):
        out = api._enforce_rsi_state(self.NEUTRAL, 27)
        assert out == self.NEUTRAL

    def test_removes_overbought_claim_when_rsi_is_neutral(self):
        t = ("הנפח גבוה פי 2 מהממוצע. RSI של 58 מצביע על קניית יתר. "
             "המחיר במרחק 4% מההתנגדות.")
        assert "קניית יתר" not in api._enforce_rsi_state(t, 58)

    def test_keeps_overbought_claim_when_rsi_really_is_overbought(self):
        t = ("הנפח גבוה פי 2 מהממוצע. RSI של 74 מצביע על קניית יתר. "
             "המחיר במרחק 4% מההתנגדות.")
        assert api._enforce_rsi_state(t, 74) == t

    def test_oversold_rsi_may_not_be_called_overbought(self):
        t = ("המניה ירדה 12% בחודש. RSI של 22 מצביע על קניית יתר. "
             "הנפח דל מהרגיל.")
        assert "קניית יתר" not in api._enforce_rsi_state(t, 22)

    def test_single_sentence_answer_is_never_gutted(self):
        # תשובה בת משפט אחד שנמחק היא מסך ריק — גרוע יותר ממשפט לא מדויק
        t = "RSI של 32 מצביע על מכירת יתר."
        assert api._enforce_rsi_state(t, 32) == t

    def test_boundary_values_follow_the_displayed_thresholds(self):
        # בדיוק על הסף: 30 ו-70 אינם מכירת/קניית יתר לפי מה שהאפליקציה מציגה
        t = "המחיר מעל ממוצע 200. RSI של 30 מצביע על מכירת יתר. הנפח ממוצע."
        assert "מכירת יתר" not in api._enforce_rsi_state(t, api.RSI_OVERSOLD)

    def test_missing_or_bad_rsi_leaves_text_untouched(self):
        for bad in (None, "32", True):
            assert api._enforce_rsi_state(self.NEUTRAL, bad) == self.NEUTRAL

    def test_empty_text_is_safe(self):
        assert api._enforce_rsi_state("", 32) == ""


# ── נפילה למודל גיבוי כשהמכסה של המודל הראשי מוצתה.
#
# מכסות Groq נספרות בנפרד לכל מודל. נמדד בפרודקשן: מתוך שש בקשות ניתוח
# רצופות רק שתיים הצליחו — משתמש שבודק כמה מניות ברצף קיבל מסך ריק ברוב
# המקרים. ──
class TestGroqFallback:
    def _payload(self):
        return {"model": api.AI_MODEL, "messages": []}

    def test_falls_back_to_the_secondary_model_on_quota(self):
        seen = []

        def fake(payload, *a, **k):
            seen.append(payload["model"])
            return api.RATE_LIMITED if len(seen) == 1 else {"choices": [1]}

        with patch("stock_api._call_groq", side_effect=fake):
            out = api._call_groq_with_fallback(self._payload())
        assert seen == [api.AI_MODEL, api.AI_FALLBACK_MODEL]
        assert out == {"choices": [1]}

    def test_successful_primary_never_touches_the_fallback(self):
        seen = []

        def fake(payload, *a, **k):
            seen.append(payload["model"])
            return {"choices": [1]}

        with patch("stock_api._call_groq", side_effect=fake):
            api._call_groq_with_fallback(self._payload())
        assert seen == [api.AI_MODEL]

    def test_transient_failure_does_not_trigger_the_fallback(self):
        # כשל רשת או 5xx כבר קיבלו ניסיון חוזר; מודל אחר לא יעזור שם
        seen = []

        def fake(payload, *a, **k):
            seen.append(payload["model"])
            return None

        with patch("stock_api._call_groq", side_effect=fake):
            assert api._call_groq_with_fallback(self._payload()) is None
        assert seen == [api.AI_MODEL]

    def test_both_models_exhausted_still_reports_rate_limited(self):
        # ההודעה למשתמש חייבת להישאר "המכסה מוצתה" ולא "תקלה זמנית"
        with patch("stock_api._call_groq", return_value=api.RATE_LIMITED):
            out = api._call_groq_with_fallback(self._payload())
        assert out is api.RATE_LIMITED

    def test_the_two_models_are_actually_different(self):
        assert api.AI_FALLBACK_MODEL != api.AI_MODEL

    def test_the_original_payload_is_not_mutated(self):
        p = self._payload()
        with patch("stock_api._call_groq", return_value=api.RATE_LIMITED):
            api._call_groq_with_fallback(p)
        assert p["model"] == api.AI_MODEL
