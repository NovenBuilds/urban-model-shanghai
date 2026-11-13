"""Typical weather for each day of the year in central Shanghai (Open-Meteo ERA5, 1995–2024).

Same fields as the Warsaw viewer: clear / cloudy / rain / snow shares, snow cover, snowy flag.
"""
import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from aoi import RAW  # noqa: E402

API = ("https://archive-api.open-meteo.com/v1/archive?latitude=31.24&longitude=121.50"
       "&start_date=1995-01-01&end_date=2024-12-31&timezone=Asia%2FShanghai")
DAILY = Path(RAW) / "climate" / "shanghai_daily.json"
OUT = ROOT / "web" / "public" / "data" / "climate.json"
WINDOW = 10


def fetch(path, query):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        print("fetching Open-Meteo climate…")
        urllib.request.urlretrieve(f"{API}&{query}", path)
    return json.load(open(path))


def per_day(times, x):
    doy = np.minimum([dt.date.fromisoformat(t[:10]).timetuple().tm_yday for t in times], 365) - 1
    k = np.ones(2 * WINDOW + 1)
    wrap = lambda a: np.convolve(np.r_[a[-WINDOW:], a, a[:WINDOW]], k, "valid")
    return wrap(np.bincount(doy, x, minlength=365)) / wrap(np.bincount(doy, minlength=365))


def stub():
    """Humid-subtropical fallback if the API is unreachable. Not used when the fetch succeeds."""
    clear = np.linspace(0.45, 0.20, 365)
    # more rain in June–September
    rain = 0.10 + 0.25 * np.exp(-0.5 * ((np.arange(365) - 200) / 50) ** 2)
    cloudy = np.clip(1 - clear - rain - 0.01, 0, 1)
    snow = np.zeros(365)
    snow[:40] = 0.02
    snow[350:] = 0.02
    s = clear + cloudy + rain + snow
    clear, cloudy, rain, snow = clear / s, cloudy / s, rain / s, snow / s
    snowy = [bool(i < 50 or i > 340) for i in range(365)]
    cover = [0.0] * 365
    r = lambda p: [round(float(x), 3) for x in p]
    return dict(source="stub (Open-Meteo unreachable): typical humid-subtropical Shanghai",
                clear=r(clear), cloudy=r(cloudy), rain=r(rain), snow=r(snow),
                cover=cover, snowy=snowy)


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    try:
        d = fetch(DAILY, "daily=weather_code,snowfall_sum,rain_sum,cloud_cover_mean,snow_depth_mean")["daily"]
        a = lambda k: np.array([v if v is not None else 0 for v in d[k]], float)
        sf, rain, cloud, depth = a("snowfall_sum"), a("rain_sum"), a("cloud_cover_mean"), a("snow_depth_mean")
        snow = sf >= 1
        wet = ~snow & (rain >= 2)
        grey = ~snow & ~wet & (cloud >= 75)
        kinds = {k: per_day(d["time"], v.astype(float)) for k, v in
                 dict(clear=~(snow | wet | grey), cloudy=grey, rain=wet, snow=snow).items()}
        snowy = per_day(d["time"], (sf > 0.2).astype(float)) > 0
        r = lambda p: [round(float(x), 3) for x in p]
        out = dict(source="Open-Meteo historical weather API (ERA5), central Shanghai, 1995-2024",
                   **{k: r(v) for k, v in kinds.items()},
                   cover=r(per_day(d["time"], (depth >= 0.02).astype(float))),
                   snowy=[bool(x) for x in snowy])
    except Exception as e:
        print("climate fetch failed, using stub:", e)
        out = stub()
    json.dump(out, open(OUT, "w"))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
