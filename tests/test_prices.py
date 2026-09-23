import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import prices


class _Series:
    def __init__(self, pairs):
        self._pairs = list(pairs)

    def items(self):
        return iter(self._pairs)


class _Frame:
    def __init__(self, pairs):
        self._pairs = list(pairs)
        self.empty = not self._pairs

    def __getitem__(self, key):
        if key != "Close":
            raise KeyError(key)
        return _Series(self._pairs)


class _Ticker:
    def __init__(self, module, symbol):
        self.module = module
        self.symbol = symbol

    def history(self, **kwargs):
        self.module.calls.append(("history", self.symbol, kwargs))
        if self.module.error:
            raise self.module.error
        return self.module.frames.get(self.symbol, _Frame([]))

    @property
    def fast_info(self):
        if self.module.currency_error:
            raise self.module.currency_error
        return {"currency": self.module.currency}


class _YFinance:
    def __init__(self, frames, error=None, currency="USD", currency_error=None):
        self.frames = frames
        self.error = error
        self.currency = currency
        self.currency_error = currency_error
        self.calls = []

    def Ticker(self, symbol):
        self.calls.append(("ticker", symbol))
        return _Ticker(self, symbol)


class _Fetcher:
    def __init__(self, payloads=None, fixtures=None):
        self.payloads = payloads or {}
        self.fixtures = fixtures
        self.urls = []

    def get_json(self, url):
        self.urls.append(url)
        if isinstance(self.payloads, Exception):
            raise self.payloads
        return self.payloads.get(url)


def _chart(symbol, closes, times=None, **meta):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d"
    payload = {"chart": {"result": [{"meta": meta, "timestamp": times or [], "indicators": {"quote": [{"close": closes}]}}]}}
    return url, payload


class PriceFallbackTests(unittest.TestCase):
    def test_valid_yfinance_quote_does_not_call_yahoo(self):
        stamp = datetime(2020, 1, 2, 21, 0, tzinfo=timezone.utc)
        earlier = datetime(2020, 1, 1, 21, 0, tzinfo=timezone.utc)
        yf = _YFinance({"AAA": _Frame([(earlier, 100.0), (stamp, 110.0)])})
        fetcher = _Fetcher()
        with patch.object(prices, "_import_yfinance", return_value=yf):
            rows = prices.price_moves(fetcher, [{"symbol": "AAA", "label": "Aaa"}])
        self.assertEqual(fetcher.urls, [])
        self.assertEqual(rows[0]["price"], 110.0)
        self.assertEqual(rows[0]["change_pct"], 10.0)
        self.assertEqual(rows[0]["as_of"], stamp.astimezone(timezone.utc).isoformat())
        self.assertNotEqual(rows[0]["as_of"][:4], "2026")
        self.assertEqual(rows[0]["comparison_basis"], "prior_daily_close")
        self.assertEqual(rows[0]["session"], "regular")
        self.assertEqual(rows[0]["quote_source"], "yfinance")
        self.assertEqual(rows[0]["currency"], "USD")

    def test_yfinance_exception_empty_and_short_history_use_yahoo(self):
        url, payload = _chart(
            "AAA",
            [10.0, 12.5],
            times=[1_600_000_000, 1_600_086_400],
            currency="CHF",
            regularMarketTime=1_600_090_000,
            regularMarketPrice=12.5,
            dataGranularity="1d",
            currentTradingPeriod={"regular": {"start": 1_600_086_400, "end": 1_600_120_000}},
        )
        cases = [
            _YFinance({"AAA": _Frame([])}, error=RuntimeError("down")),
            _YFinance({"AAA": _Frame([])}),
            _YFinance({"AAA": _Frame([(datetime(2020, 1, 1, tzinfo=timezone.utc), 9.0)])}),
            _YFinance({"AAA": _Frame([(None, float("nan")), (None, float("inf"))])}),
        ]
        for yf in cases:
            fetcher = _Fetcher({url: payload})
            with patch.object(prices, "_import_yfinance", return_value=yf):
                rows = prices.price_moves(fetcher, [{"symbol": "AAA", "label": "Aaa"}])
            self.assertEqual(fetcher.urls, [url], yf.error or yf.frames)
            self.assertEqual(rows[0]["price"], 12.5)
            self.assertEqual(rows[0]["quote_source"], "yahoo_chart")
            self.assertEqual(rows[0]["as_of"], datetime.fromtimestamp(1_600_090_000, timezone.utc).isoformat())
            self.assertEqual(rows[0]["session"], "regular")
            self.assertEqual(rows[0]["comparison_basis"], "prior_daily_close")
            self.assertEqual(rows[0]["currency"], "CHF")
            self.assertEqual(rows[0]["change_pct"], 25.0)

    def test_quotes_keep_their_own_timestamps(self):
        url_a, payload_a = _chart("AAA", [1, 2], times=[1_000, 2_000], currency="USD", dataGranularity="1d")
        url_b, payload_b = _chart("BBB", [4, 2], times=[3_000, 4_000], currency="EUR", dataGranularity="1d")
        yf = _YFinance({}, error=RuntimeError("down"))
        fetcher = _Fetcher({url_a: payload_a, url_b: payload_b})
        with patch.object(prices, "_import_yfinance", return_value=yf):
            rows = prices.price_moves(fetcher, [{"symbol": "AAA"}, {"symbol": "BBB"}])
        self.assertEqual(rows[0]["as_of"], datetime.fromtimestamp(2_000, timezone.utc).isoformat())
        self.assertEqual(rows[1]["as_of"], datetime.fromtimestamp(4_000, timezone.utc).isoformat())
        self.assertEqual(rows[0]["price"], 2.0)
        self.assertEqual(rows[1]["price"], 2.0)
        self.assertEqual(rows[1]["change_pct"], -50.0)

    def test_currency_lookup_failure_still_keeps_the_yfinance_quote(self):
        stamp = datetime(2020, 5, 5, tzinfo=timezone.utc)
        yf = _YFinance(
            {"AAA": _Frame([(datetime(2020, 5, 4, tzinfo=timezone.utc), 50.0), (stamp, 55.0)])},
            currency_error=RuntimeError("fast_info"),
        )
        fetcher = _Fetcher()
        with patch.object(prices, "_import_yfinance", return_value=yf):
            rows = prices.price_moves(fetcher, [{"symbol": "AAA"}])
        self.assertEqual(fetcher.urls, [])
        self.assertEqual(rows[0]["price"], 55.0)
        self.assertIsNone(rows[0]["currency"])
        self.assertEqual(rows[0]["as_of"], stamp.isoformat())

    def test_fixture_mode_skips_yfinance_and_missing_time_stays_missing(self):
        url, payload = _chart("AAA", [3, 4], currency="USD")
        payload["chart"]["result"][0]["meta"]["indicators"] = payload["chart"]["result"][0].pop("indicators")
        payload["chart"]["result"][0].pop("timestamp")
        fetcher = _Fetcher({url: payload}, fixtures=Path("fixtures"))

        def explode():
            raise AssertionError("yfinance should not load for fixtures")

        with patch.object(prices, "_import_yfinance", explode):
            rows = prices.price_moves(fetcher, [{"symbol": "AAA", "label": "Aaa"}])
        self.assertEqual(rows[0]["price"], 4.0)
        self.assertIsNone(rows[0]["as_of"])
        self.assertEqual(rows[0]["comparison_basis"], "prior_daily_close")
        self.assertIsNone(rows[0]["session"])
        self.assertEqual(rows[0]["quote_source"], "yahoo_chart")

    def test_yahoo_failure_after_yfinance_failure_does_not_invent_a_time(self):
        yf = _YFinance({}, error=RuntimeError("down"))
        fetcher = _Fetcher({})
        with patch.object(prices, "_import_yfinance", return_value=yf):
            rows = prices.price_moves(fetcher, [{"symbol": "AAA"}])
        self.assertIsNone(rows[0]["price"])
        self.assertIsNone(rows[0]["as_of"])
        self.assertIsNone(rows[0]["comparison_basis"])
        self.assertIsNone(rows[0]["quote_source"])

    def test_one_ticker_failure_does_not_drop_the_next(self):
        stamp = datetime(2020, 1, 2, tzinfo=timezone.utc)
        yf = _YFinance({"BBB": _Frame([(datetime(2020, 1, 1, tzinfo=timezone.utc), 8.0), (stamp, 9.0)])})

        class _BoomTicker(_Ticker):
            def history(self, **kwargs):
                if self.symbol == "AAA":
                    raise TimeoutError("slow")
                return super().history(**kwargs)

        yf.Ticker = lambda symbol: _BoomTicker(yf, symbol)  # type: ignore[method-assign]
        fetcher = _Fetcher()
        with patch.object(prices, "_import_yfinance", return_value=yf):
            rows = prices.price_moves(fetcher, [{"symbol": "AAA"}, {"symbol": "BBB"}])
        self.assertIsNone(rows[0]["price"])
        self.assertEqual(rows[1]["price"], 9.0)
        self.assertEqual(rows[1]["quote_source"], "yfinance")


if __name__ == "__main__":
    unittest.main()
