
import csv
import io
import json
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

STOCKS = {
    "2330": "台積電",
    "2408": "南亞科",
    "2303": "聯電",
    "2891": "中信金",
}
TZ = ZoneInfo("Asia/Taipei")
OUT = Path("data")
OUT.mkdir(exist_ok=True)

def months_back(today, count=7):
    y, m = today.year, today.month
    for _ in range(count):
        yield f"{y}{m:02d}01"
        m -= 1
        if m == 0:
            y, m = y - 1, 12

def get_month(stock, month):
    url = (
        "https://www.twse.com.tw/exchangeReport/"
        f"STOCK_DAY?response=json&date={month}"
        f"&stockNo={stock}"
    )
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0",
                 "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=25) as r:
        result = json.load(r)

    if result.get("stat") != "OK":
        raise ValueError(
            f"{stock} {month}: {result.get('stat')}"
        )

    rows = []
    for x in result.get("data", []):
        try:
            roc_y, m, d = map(int, x[0].split("/"))
            date = f"{roc_y+1911:04d}-{m:02d}-{d:02d}"
            num = lambda s: float(s.replace(",", ""))
            volume = int(x[1].replace(",", ""))
            o, h, l, c = [num(x[i]) for i in (3,4,5,6)]
            if not (l <= o <= h and l <= c <= h):
                raise ValueError("Invalid OHLC")
            rows.append({
                "date": date, "open": o, "high": h,
                "low": l, "close": c,
                "volume_shares": volume,
                "volume_lots": volume / 1000
            })
        except (ValueError, IndexError, TypeError):
            continue
    return rows

def add_indicators(rows):
    for i, row in enumerate(rows):
        for n in (5, 10, 20, 60):
            window = rows[max(0, i-n+1):i+1]
            row[f"ma{n}"] = (
                round(sum(x["close"] for x in window)/n, 3)
                if len(window) == n else None
            )
        window = rows[max(0, i-19):i+1]
        row["high20"] = (
            max(x["high"] for x in window)
            if len(window) == 20 else None
        )
        row["low20"] = (
            min(x["low"] for x in window)
            if len(window) == 20 else None
        )
        row["avg_volume20"] = (
            round(sum(x["volume_shares"] for x in window)/20)
            if len(window) == 20 else None
        )

today = datetime.now(TZ).date()
summary = {}

for stock, name in STOCKS.items():
    existing_file = OUT / f"{stock}.json"
    old_rows = []
    if existing_file.exists():
        old_rows = json.loads(
            existing_file.read_text(encoding="utf-8")
        ).get("rows", [])

    all_rows = {r["date"]: r for r in old_rows}
    success = 0

    for month in months_back(today):
        for attempt in range(3):
            try:
                new_rows = get_month(stock, month)
                for r in new_rows:
                    all_rows[r["date"]] = r
                success += 1
                break
            except Exception as e:
                print(f"Retry {stock} {month}: {e}")
                time.sleep(3 * (attempt + 1))
        time.sleep(2)

    if success == 0:
        raise RuntimeError(f"{stock}: all API calls failed")

    rows = sorted(all_rows.values(), key=lambda r: r["date"])
    rows = [r for r in rows if r["date"] <= today.isoformat()]
    rows = rows[-120:]
    add_indicators(rows)

    if len(rows) < 60:
        raise RuntimeError(
            f"{stock}: only {len(rows)} trading days"
        )

    payload = {
        "stock": stock,
        "name": name,
        "source": "TWSE STOCK_DAY",
        "adjusted": False,
        "generated_at": datetime.now(TZ).isoformat(),
        "latest_date": rows[-1]["date"],
        "count": len(rows),
        "rows": rows
    }
    existing_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    summary[stock] = {
        "name": name,
        "latest_date": rows[-1]["date"],
        "count": len(rows),
        "close": rows[-1]["close"],
        "ma20": rows[-1]["ma20"],
        "ma60": rows[-1]["ma60"]
    }
    print(stock, summary[stock])

(OUT / "summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2),
    encoding="utf-8"
)
