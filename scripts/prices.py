"""Ticker price retrieval. Owned by T03 after T00 extraction."""
from __future__ import annotations

import math
import sys
import urllib.parse

log = lambda *a: print(*a, file=sys.stderr, flush=True)  # noqa: E731


def price_moves(fetcher, tickers: list[dict]) -> list[dict]:
    rows = []
    try:
        import yfinance as yf
    except ImportError:
        yf = None

    for t in tickers:
        sym, label = t["symbol"], t.get("label", t["symbol"])
        row = {"symbol": sym, "label": label, "price": None, "change_pct": None, "currency": None}
        try:
            if yf is not None and not fetcher.fixtures:
                h = yf.Ticker(sym).history(period="5d", interval="1d")
                closes = [float(c) for c in h["Close"].tolist() if c == c]
                if len(closes) >= 2:
                    last, prev = closes[-1], closes[-2]
                    row.update(price=round(last, 2), change_pct=round((last / prev - 1) * 100, 2))
                    try:
                        row["currency"] = yf.Ticker(sym).fast_info.get("currency")
                    except Exception:
                        pass
            else:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range=5d&interval=1d"
                j = fetcher.get_json(url)
                res = (j or {}).get("chart", {}).get("result") or []
                if res:
                    meta = res[0].get("meta", {})
                    closes = [c for c in res[0]["indicators"]["quote"][0].get("close", []) if c is not None]
                    if len(closes) >= 2:
                        last, prev = closes[-1], closes[-2]
                        row.update(
                            price=round(last, 2),
                            change_pct=round((last / prev - 1) * 100, 2),
                            currency=meta.get("currency"),
                        )
        except Exception as e:
            log(f"  [price failed] {sym}: {e.__class__.__name__}")
        for k in ("price", "change_pct"):
            v = row[k]
            if v is not None and (not isinstance(v, (int, float)) or not math.isfinite(v)):
                row[k] = None
        if row["price"] is None:
            log(f"  [no price] {sym}")
        rows.append(row)
    return rows
