"""Ticker price retrieval. Owned by T03 after T00 extraction."""
from __future__ import annotations

import math
import sys
import urllib.parse
from datetime import datetime, timezone

log = lambda *a: print(*a, file=sys.stderr, flush=True)  # noqa: E731

# Daily closes from the chart request. Not the briefing clock.
COMPARISON_BASIS = "prior_daily_close"


def _import_yfinance():
    import yfinance as yf

    return yf


def _utc_iso(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return None


def _unix_iso(value) -> str | None:
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return datetime.fromtimestamp(int(value), timezone.utc).isoformat()


def _finite_price(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _blank(symbol: str, label: str) -> dict:
    return {
        "symbol": symbol,
        "label": label,
        "price": None,
        "change_pct": None,
        "currency": None,
        "as_of": None,
        "comparison_basis": None,
        "session": None,
        "quote_source": None,
    }


def _quote_from_closes(closes: list[tuple[float, object]], *, currency, source: str, session: str | None, as_of: str | None) -> dict | None:
    if len(closes) < 2:
        return None
    last, prev = closes[-1][0], closes[-2][0]
    change = (last / prev - 1) * 100
    if not math.isfinite(change):
        return None
    return {
        "price": round(last, 2),
        "change_pct": round(change, 2),
        "currency": currency,
        "as_of": as_of,
        "comparison_basis": COMPARISON_BASIS,
        "session": session,
        "quote_source": source,
    }


def _session_at(periods: dict | None, instant: int | None) -> str | None:
    if not isinstance(periods, dict) or instant is None:
        return None
    for name in ("pre", "regular", "post"):
        window = periods.get(name) or {}
        start, end = window.get("start"), window.get("end")
        if isinstance(start, (int, float)) and isinstance(end, (int, float)) and start <= instant < end and start != end:
            return name
    return None


def quote_from_yfinance(yf, symbol: str) -> dict | None:
    """One daily-history read. None means the caller should try Yahoo."""
    ticker = yf.Ticker(symbol)
    history = ticker.history(period="5d", interval="1d")
    if history is None or getattr(history, "empty", True):
        return None
    try:
        series = history["Close"]
    except (KeyError, TypeError):
        return None
    pairs: list[tuple[float, object]] = []
    items = series.items() if hasattr(series, "items") else ()
    if not items:
        values = series.tolist() if hasattr(series, "tolist") else list(series)
        items = [(None, value) for value in values]
    for stamp, value in items:
        price = _finite_price(value)
        if price is not None:
            pairs.append((price, stamp))
    if len(pairs) < 2:
        return None
    currency = None
    try:
        info = ticker.fast_info
        currency = info.get("currency") if hasattr(info, "get") else None
    except Exception:
        currency = None
    return _quote_from_closes(
        pairs,
        currency=currency,
        source="yfinance",
        session="regular",
        as_of=_utc_iso(pairs[-1][1]),
    )


def quote_from_yahoo_chart(payload: dict | None) -> dict | None:
    """Parse a Yahoo v8 chart body. Accepts the recorded fixture shape too."""
    result = ((payload or {}).get("chart") or {}).get("result") or []
    if not result or not isinstance(result[0], dict):
        return None
    block = result[0]
    meta = block.get("meta") or {}
    if not isinstance(meta, dict):
        meta = {}
    indicators = block.get("indicators") or meta.get("indicators") or {}
    quotes = indicators.get("quote") or []
    if not quotes or not isinstance(quotes[0], dict):
        return None
    raw_closes = quotes[0].get("close") or []
    timestamps = block.get("timestamp") or []
    pairs: list[tuple[float, int | None]] = []
    for index, value in enumerate(raw_closes):
        price = _finite_price(value)
        if price is None:
            continue
        stamp = timestamps[index] if index < len(timestamps) else None
        pairs.append((price, int(stamp) if isinstance(stamp, (int, float)) and math.isfinite(stamp) else None))
    if len(pairs) < 2:
        return None
    last, last_bar = pairs[-1]
    as_of_unix = None
    market_time = meta.get("regularMarketTime")
    market_price = _finite_price(meta.get("regularMarketPrice"))
    if isinstance(market_time, (int, float)) and math.isfinite(market_time) and market_price is not None:
        if abs(market_price - last) <= max(0.02, abs(last) * 1e-4):
            as_of_unix = int(market_time)
    if as_of_unix is None and last_bar is not None:
        as_of_unix = last_bar
    session = _session_at(meta.get("currentTradingPeriod"), as_of_unix)
    if session is None and meta.get("dataGranularity") == "1d":
        session = "regular"
    return _quote_from_closes(
        pairs,
        currency=meta.get("currency"),
        source="yahoo_chart",
        session=session,
        as_of=_unix_iso(as_of_unix),
    )


def _yahoo_chart(fetcher, symbol: str) -> dict | None:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?range=5d&interval=1d"
    try:
        payload = fetcher.get_json(url)
    except Exception as exc:
        log(f"  [price failed] {symbol}: yahoo {exc.__class__.__name__}")
        return None
    return quote_from_yahoo_chart(payload)


def price_moves(fetcher, tickers: list[dict]) -> list[dict]:
    """Return paired price, change and source time.

    Installed yfinance is tried first outside fixture mode. Exceptions, empty
    history, fewer than two finite closes, and non-finite results fall through
    to one direct Yahoo chart read. A valid yfinance quote does not call Yahoo.
    `as_of` is the source bar or regular-market time, never the briefing clock.
    """
    yf = None
    if not getattr(fetcher, "fixtures", None):
        try:
            yf = _import_yfinance()
        except ImportError:
            yf = None

    rows = []
    for ticker in tickers:
        symbol, label = ticker["symbol"], ticker.get("label", ticker["symbol"])
        row = _blank(symbol, label)
        quote = None
        if yf is not None:
            try:
                quote = quote_from_yfinance(yf, symbol)
            except Exception as exc:
                log(f"  [price failed] {symbol}: yfinance {exc.__class__.__name__}")
                quote = None
            if quote is None:
                log(f"  [price fallback] {symbol}: Yahoo chart")
                quote = _yahoo_chart(fetcher, symbol)
        else:
            quote = _yahoo_chart(fetcher, symbol)
        if quote:
            row.update(quote)
        else:
            log(f"  [no price] {symbol}")
        rows.append(row)
    return rows
