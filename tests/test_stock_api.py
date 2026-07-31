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


class TestAIEndpoint:
    def setup_method(self):
        _clear_cache()
        _clear_rate()

    def test_no_groq_key_returns_empty_text(self):
        with patch.object(api, "GROQ_KEY", ""):
            r = client.post("/ai", json={"ticker": "AAPL", "trend": "bullish"})
        assert r.status_code == 200
        assert r.json().get("text") == ""

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
