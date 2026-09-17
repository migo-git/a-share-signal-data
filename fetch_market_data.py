#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
A-share Signal Axis V0.1 data bridge - v2

Authoritative history: BaoStock (qfq, adjustflag=2)
Cross-check priority: Tencent qfq -> Eastmoney qfq

Outputs:
  data/latest.json
  data/status.json
  data/<symbol>.csv
"""

from __future__ import annotations

import csv
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import List

import requests
import baostock as bs

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

STOCKS = {
    "600309": {"name": "万华化学", "market": "sh"},
    "688012": {"name": "中微公司", "market": "sh"},
    "601899": {"name": "紫金矿业", "market": "sh"},
    "600183": {"name": "生益科技", "market": "sh"},
    "603259": {"name": "药明康德", "market": "sh"},
    "601138": {"name": "工业富联", "market": "sh"},
}

SAVE_LIMIT = 180
MIN_VALID_BARS = 120
LOOKBACK_DAYS = 430
TIMEOUT = 20

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"

TENCENT_URLS = [
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
    "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get",
    "https://ifzq.gtimg.cn/appstock/app/fqkline/get",
]

EASTMONEY_URLS = [
    "https://push2his.eastmoney.com/api/qt/stock/kline/get",
    "https://33.push2his.eastmoney.com/api/qt/stock/kline/get",
]


def fnum(x):
    if x in (None, "", "-", "null"):
        return None
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def inum(x):
    v = fnum(x)
    return None if v is None else int(round(v))


def clean_rows(rows: List[dict]) -> List[dict]:
    by_date = {}
    for row in rows:
        try:
            datetime.strptime(row["date"], "%Y-%m-%d")
        except Exception:
            continue
        vals = [row.get("open"), row.get("high"), row.get("low"), row.get("close")]
        if any(v is None or float(v) <= 0 for v in vals):
            continue
        if float(row["high"]) < float(row["low"]):
            continue
        by_date[row["date"]] = row
    return [by_date[d] for d in sorted(by_date)]


def trim_to_date(rows: List[dict], end_date: str) -> List[dict]:
    return [r for r in rows if r["date"] <= end_date]


def now_china_iso() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def fetch_baostock(symbol: str, market: str) -> List[dict]:
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {lg.error_msg}")
    try:
        end = datetime.now(timezone(timedelta(hours=8))).date()
        start = end - timedelta(days=LOOKBACK_DAYS)
        code = f"{market}.{symbol}"
        fields = "date,code,open,high,low,close,volume,amount,adjustflag,turn,tradestatus,pctChg"
        rs = bs.query_history_k_data_plus(
            code,
            fields,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            frequency="d",
            adjustflag="2",
        )
        if rs.error_code != "0":
            raise RuntimeError(f"BaoStock query failed: {rs.error_msg}")
        names = fields.split(",")
        rows = []
        while rs.error_code == "0" and rs.next():
            d = dict(zip(names, rs.get_row_data()))
            if d.get("tradestatus") != "1":
                continue
            row = {
                "date": d["date"],
                "open": fnum(d["open"]),
                "close": fnum(d["close"]),
                "high": fnum(d["high"]),
                "low": fnum(d["low"]),
                "volume": inum(d["volume"]),
                "amount": fnum(d["amount"]),
                "turnover_pct": fnum(d["turn"]),
                "pct_change": fnum(d["pctChg"]),
            }
            if all(row[k] is not None for k in ("open", "high", "low", "close", "volume", "amount")):
                rows.append(row)
        rows = clean_rows(rows)
        if len(rows) < MIN_VALID_BARS:
            raise RuntimeError(f"BaoStock only has {len(rows)} valid bars")
        return rows[-SAVE_LIMIT:]
    finally:
        try:
            bs.logout()
        except Exception:
            pass


def fetch_tencent(symbol: str, market: str, completed_date: str) -> List[dict]:
    api_symbol = f"{market}{symbol}"
    params = {"param": f"{api_symbol},day,,,{SAVE_LIMIT + 30},qfq"}
    headers = {
        "User-Agent": UA,
        "Referer": "https://gu.qq.com/",
        "Accept": "application/json,text/plain,*/*",
    }
    errors = []
    for base_url in TENCENT_URLS:
        for attempt in range(2):
            try:
                r = requests.get(base_url, params=params, headers=headers, timeout=TIMEOUT)
                r.raise_for_status()
                text = r.text.strip()
                if "=" in text and not text.startswith("{"):
                    text = text.split("=", 1)[1].strip().rstrip(";")
                payload = json.loads(text)
                stock_data = (payload.get("data") or {}).get(api_symbol) or {}
                raw = stock_data.get("qfqday") or stock_data.get("day") or []
                if not raw:
                    raise RuntimeError("Tencent returned no qfqday/day rows")
                rows = []
                for k in raw:
                    if len(k) < 6:
                        continue
                    row = {
                        "date": str(k[0]),
                        "open": fnum(k[1]),
                        "close": fnum(k[2]),
                        "high": fnum(k[3]),
                        "low": fnum(k[4]),
                        "volume_raw": fnum(k[5]),
                    }
                    if all(row[x] is not None for x in ("open", "close", "high", "low")):
                        rows.append(row)
                rows = trim_to_date(clean_rows(rows), completed_date)
                if len(rows) < MIN_VALID_BARS:
                    raise RuntimeError(f"Tencent only has {len(rows)} completed bars")
                return rows[-SAVE_LIMIT:]
            except Exception as e:
                errors.append(f"{base_url}: {e}")
                time.sleep(1.0 + attempt)
    raise RuntimeError("Tencent failed: " + " | ".join(errors[-4:]))


def eastmoney_secid(symbol: str, market: str) -> str:
    return f"{'1' if market == 'sh' else '0'}.{symbol}"


def fetch_eastmoney(symbol: str, market: str, completed_date: str) -> List[dict]:
    params = {
        "secid": eastmoney_secid(symbol, market),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "1",
        "beg": "0",
        "end": completed_date.replace("-", ""),
        "lmt": str(SAVE_LIMIT + 30),
    }
    headers = {
        "User-Agent": UA,
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://quote.eastmoney.com/",
    }
    errors = []
    for base_url in EASTMONEY_URLS:
        for attempt in range(2):
            try:
                r = requests.get(base_url, params=params, headers=headers, timeout=TIMEOUT)
                r.raise_for_status()
                payload = r.json()
                klines = ((payload.get("data") or {}).get("klines") or [])
                if not klines:
                    raise RuntimeError("Eastmoney returned no klines")
                rows = []
                for line in klines:
                    p = line.split(",")
                    if len(p) < 11:
                        continue
                    row = {
                        "date": p[0],
                        "open": fnum(p[1]),
                        "close": fnum(p[2]),
                        "high": fnum(p[3]),
                        "low": fnum(p[4]),
                        "volume": inum(p[5]),
                        "amount": fnum(p[6]),
                        "amplitude_pct": fnum(p[7]),
                        "pct_change": fnum(p[8]),
                        "change": fnum(p[9]),
                        "turnover_pct": fnum(p[10]),
                    }
                    if all(row[k] is not None for k in ("open", "high", "low", "close")):
                        rows.append(row)
                rows = trim_to_date(clean_rows(rows), completed_date)
                if len(rows) < MIN_VALID_BARS:
                    raise RuntimeError(f"Eastmoney only has {len(rows)} completed bars")
                return rows[-SAVE_LIMIT:]
            except Exception as e:
                errors.append(f"{base_url}: {e}")
                time.sleep(1.0 + attempt)
    raise RuntimeError("Eastmoney failed: " + " | ".join(errors[-4:]))


def compare_qfq(authoritative: List[dict], validator: List[dict]) -> dict:
    a = {r["date"]: r for r in authoritative}
    b = {r["date"]: r for r in validator}
    common = sorted(set(a) & set(b))
    if len(common) < 30:
        return {"ok": False, "reason": f"only {len(common)} overlapping completed dates", "common_dates": len(common)}
    a_last = authoritative[-1]
    b_last = validator[-1]
    if a_last["date"] != b_last["date"]:
        return {"ok": False, "reason": f"latest completed date mismatch: {a_last['date']} vs {b_last['date']}"}
    latest_diff = abs(a_last["close"] - b_last["close"]) / max(abs(a_last["close"]), 1e-9)
    if latest_diff > 0.005:
        return {
            "ok": False,
            "reason": f"latest close mismatch: {latest_diff:.2%}",
            "authoritative_close": a_last["close"],
            "validator_close": b_last["close"],
        }
    ratios, rel_diffs = [], []
    for d in common[-30:]:
        ac = a[d]["close"]
        bc = b[d]["close"]
        if ac and bc:
            ratios.append(ac / bc)
            rel_diffs.append(abs(ac - bc) / max(abs(ac), 1e-9))
    ratio_med = median(ratios)
    ratio_spread = max(abs(x / ratio_med - 1) for x in ratios)
    med_diff = median(rel_diffs)
    max_diff = max(rel_diffs)
    ok = ratio_spread <= 0.015 and med_diff <= 0.015 and max_diff <= 0.04
    return {
        "ok": ok,
        "reason": "ok" if ok else (
            f"qfq continuity mismatch: ratio_spread={ratio_spread:.2%}, "
            f"median_diff={med_diff:.2%}, max_diff={max_diff:.2%}"
        ),
        "common_dates": len(common),
        "latest_completed_date": a_last["date"],
        "authoritative_close": a_last["close"],
        "validator_close": b_last["close"],
        "latest_close_rel_diff": latest_diff,
        "recent_ratio_spread": ratio_spread,
        "recent_median_rel_diff": med_diff,
        "recent_max_rel_diff": max_diff,
    }


def write_csv(symbol: str, rows: List[dict]) -> None:
    path = DATA_DIR / f"{symbol}.csv"
    cols = ["date", "open", "high", "low", "close", "volume", "amount", "turnover_pct", "pct_change"]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def main() -> int:
    output = {
        "schema_version": 2,
        "generated_at_china": now_china_iso(),
        "adjustment": "qfq/forward-adjusted",
        "authoritative_source": "baostock",
        "validator_priority": ["tencent", "eastmoney"],
        "required_valid_bars": MIN_VALID_BARS,
        "saved_bars_per_stock": SAVE_LIMIT,
        "stocks": {},
    }
    status = {
        "schema_version": 2,
        "generated_at_china": output["generated_at_china"],
        "all_valid_for_signal_axis": True,
        "stocks": {},
    }

    for symbol, meta in STOCKS.items():
        name, market = meta["name"], meta["market"]
        bs_rows = tx_rows = em_rows = None
        bs_error = tx_error = em_error = None
        validator_name = None
        validator_rows = None
        cross = None
        valid = False
        invalid_reason = None

        try:
            bs_rows = fetch_baostock(symbol, market)
        except Exception as e:
            bs_error = str(e)

        completed_date = bs_rows[-1]["date"] if bs_rows else None

        if bs_rows:
            try:
                tx_rows = fetch_tencent(symbol, market, completed_date)
                validator_name, validator_rows = "tencent", tx_rows
            except Exception as e:
                tx_error = str(e)

            if validator_rows is None:
                try:
                    em_rows = fetch_eastmoney(symbol, market, completed_date)
                    validator_name, validator_rows = "eastmoney", em_rows
                except Exception as e:
                    em_error = str(e)

        if not bs_rows:
            invalid_reason = "BaoStock authoritative history unavailable"
        elif validator_rows is None:
            invalid_reason = "no independent qfq validator available"
        else:
            cross = compare_qfq(bs_rows, validator_rows)
            valid = bool(cross.get("ok"))
            if not valid:
                invalid_reason = cross.get("reason")

        bars = (bs_rows or [])[-SAVE_LIMIT:]
        latest = bars[-1] if bars else None

        if bars:
            write_csv(symbol, bars)

        rec = {
            "symbol": symbol,
            "name": name,
            "market": market,
            "valid_for_signal_axis": valid,
            "reason_if_invalid": invalid_reason,
            "latest_completed_date": completed_date,
            "latest": latest,
            "bar_count": len(bars),
            "authoritative_source": "baostock",
            "validator_used": validator_name,
            "sources": {
                "baostock": {"ok": bs_rows is not None, "bar_count": len(bs_rows or []), "error": bs_error},
                "tencent": {"ok": tx_rows is not None, "bar_count": len(tx_rows or []), "error": tx_error},
                "eastmoney": {
                    "ok": em_rows is not None,
                    "bar_count": len(em_rows or []),
                    "error": em_error,
                    "note": "queried only if Tencent validator failed",
                },
            },
            "cross_check": cross,
            "bars": bars,
        }
        output["stocks"][symbol] = rec
        status["stocks"][symbol] = {k: v for k, v in rec.items() if k != "bars"}

        if not valid:
            status["all_valid_for_signal_axis"] = False

    (DATA_DIR / "latest.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA_DIR / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
