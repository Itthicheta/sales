"""Daily Bangkok rain/temperature from Open-Meteo (free, no key) -> sales_web.weather_daily.
Archive API lags ~5 days; the forecast API's past_days covers the gap. Run nightly."""
import json, datetime as dt, sys, urllib.request
from pathlib import Path
BACKBONE = Path.home() / "mamapook-data"  # backbone repo = dependency (shared.*, .env)
sys.path.insert(0, str(BACKBONE))
from shared.env import load
from shared.backbone import Backbone

LAT, LON = 13.7563, 100.5018   # Bangkok (Silom/Sathorn cluster)
START = "2026-07-01"

def fetch(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)

def main():
    today = dt.date.today()
    rows = {}
    arc = fetch(f"https://archive-api.open-meteo.com/v1/archive?latitude={LAT}&longitude={LON}"
                f"&start_date={START}&end_date={today}&daily=precipitation_sum,temperature_2m_max&timezone=Asia/Bangkok")
    for d, p, t in zip(arc["daily"]["time"], arc["daily"]["precipitation_sum"], arc["daily"]["temperature_2m_max"]):
        if p is not None: rows[d] = (p, t)
    fc = fetch(f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}"
               f"&past_days=10&forecast_days=1&daily=precipitation_sum,temperature_2m_max&timezone=Asia/Bangkok")
    for d, p, t in zip(fc["daily"]["time"], fc["daily"]["precipitation_sum"], fc["daily"]["temperature_2m_max"]):
        if p is not None and d not in rows and d <= str(today): rows[d] = (p, t)
    bb = Backbone(load())
    with bb.conn.cursor() as cur:
        cur.execute("create table if not exists sales_web.weather_daily (d date primary key, rain_mm numeric, tmax_c numeric)")
        for d, (p, t) in rows.items():
            cur.execute("""insert into sales_web.weather_daily values (%s,%s,%s)
                           on conflict (d) do update set rain_mm=excluded.rain_mm, tmax_c=excluded.tmax_c""", (d, p, t))
    bb.conn.commit()
    print(f"weather_daily: {len(rows)} days through {max(rows)}")

if __name__ == "__main__":
    main()
