"""Wikidata items located on the first AOI, with zh + en Wikipedia page views.

Output: data/raw/notable.json
Page-view window: Nov 2025 – Oct 2026 (monthly totals).
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from aoi import RAW, reload, wgs84_bounds  # noqa: E402

reload()

OUT = Path(RAW) / "notable.json"
UA = {"User-Agent": "shanghai-voxels/0.1 (local research; https://github.com/stared/warsaw-vibe-voxel-adapted)"}
SPAN = "20251101/20261031"
SPARQL = """
SELECT ?item ?at ?zh ?en ?desc ?zhwiki ?enwiki ?wikis ?site (GROUP_CONCAT(DISTINCT ?typeLabel; separator="|") AS ?types) WHERE {
  SERVICE wikibase:box { ?item wdt:P625 ?at. bd:serviceParam wikibase:cornerSouthWest "Point(%f %f)"^^geo:wktLiteral;
                                                  wikibase:cornerNorthEast "Point(%f %f)"^^geo:wktLiteral. }
  ?item wikibase:sitelinks ?wikis.
  OPTIONAL { ?item rdfs:label ?zh FILTER(LANG(?zh) = "zh") }
  OPTIONAL { ?item rdfs:label ?en FILTER(LANG(?en) = "en") }
  OPTIONAL { ?item schema:description ?desc FILTER(LANG(?desc) = "en") }
  OPTIONAL { ?zhw schema:about ?item; schema:isPartOf <https://zh.wikipedia.org/>; schema:name ?zhwiki }
  OPTIONAL { ?enw schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>; schema:name ?enwiki }
  OPTIONAL { ?item wdt:P856 ?site }
  OPTIONAL { ?item wdt:P31 ?type. ?type rdfs:label ?typeLabel FILTER(LANG(?typeLabel) = "en") }
} GROUP BY ?item ?at ?zh ?en ?desc ?zhwiki ?enwiki ?wikis ?site
"""


def get(url, data=None, timeout=120):
    for wait in (5, 20, 60, 120, 240):
        try:
            req = urllib.request.Request(url, data=data, headers=UA)
            return json.load(urllib.request.urlopen(req, timeout=timeout))
        except urllib.error.HTTPError as err:
            if err.code not in (429, 503):
                raise
            time.sleep(int(err.headers.get("Retry-After") or wait))
    raise RuntimeError(f"rate-limited: {url[:80]}")


def items():
    S, W_, N, E = wgs84_bounds(200)
    # SPARQL box: SW lon lat, NE lon lat
    q = SPARQL % (W_, S, E, N)
    print("SPARQL bbox", W_, S, E, N)
    res = get("https://query.wikidata.org/sparql?format=json",
              urllib.parse.urlencode(dict(query=q)).encode(), timeout=180)
    out = {}
    for b in res["results"]["bindings"]:
        v = {k: x["value"] for k, x in b.items()}
        qid = v["item"].rsplit("/", 1)[1]
        lon, lat = map(float, v["at"].removeprefix("Point(").rstrip(")").split())
        e = out.setdefault(qid, dict(zh=v.get("zh"), en=v.get("en"), desc=v.get("desc"),
                                     types=v.get("types", ""), at=[lon, lat],
                                     zhwiki=v.get("zhwiki"), enwiki=v.get("enwiki"),
                                     wikis=int(float(v["wikis"])), site=v.get("site")))
        e["site"] = e["site"] or v.get("site")
        if v.get("types") and v["types"] not in (e.get("types") or ""):
            e["types"] = "|".join(filter(None, [e.get("types"), v["types"]]))
    return out


def views(lang, title):
    t = urllib.parse.quote(title.replace(" ", "_"), safe="")
    try:
        res = get(f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{lang}.wikipedia/all-access/user/{t}/monthly/{SPAN}")
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return 0
        raise
    return sum(i["views"] for i in res["items"])


def pageviews(lang, titles):
    titles = sorted(set(titles))
    print(f"pageviews {lang}: {len(titles)} titles")
    with ThreadPoolExecutor(6) as pool:
        return dict(zip(titles, pool.map(lambda t: views(lang, t), titles)))


def main():
    Path(RAW).mkdir(parents=True, exist_ok=True)
    wd = items()
    print(len(wd), "items on the map")
    for lang in ("zh", "en"):
        key = f"{lang}wiki"
        pv = pageviews(lang, [e[key] for e in wd.values() if e.get(key)])
        for e in wd.values():
            e[f"views_{lang}"] = pv.get(e.get(key), 0) if e.get(key) else 0
        time.sleep(0.2)
    for e in wd.values():
        e["views"] = int(e.get("views_zh", 0)) + int(e.get("views_en", 0))
    OUT.write_text(json.dumps(wd, ensure_ascii=False))
    print("wrote", OUT)
    for qid, e in sorted(wd.items(), key=lambda kv: -kv[1]["views"])[:25]:
        print(f"  {e['views']:8d}  {e.get('zh') or e.get('en')} | {e.get('types','')[:60]}")


if __name__ == "__main__":
    main()
