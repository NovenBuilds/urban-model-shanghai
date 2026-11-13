"""Assemble map labels and camera presets, ranked like Warsaw's labels.py.

far (metres of camera distance at which a label still shows) comes first from FAR_TIERS,
then from zh+en Wikipedia page views in data/raw/notable.json (scripts/notable.py).

This script can run standalone against an existing web/public/data/meta.json, and is
also imported at the end of voxelize.py when notable.json is present.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from pyproj import Transformer
from shapely.geometry import Point, box

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from aoi import CRS, D, RAW, W, reload, to_grid  # noqa: E402

reload()

NOTABLE = Path(ROOT / RAW) / "notable.json"
META = ROOT / "web" / "public" / "data" / "meta.json"
OUT = META
_T = Transformer.from_crs(4326, CRS, always_xy=True)

# how far away (m) each label shows. City-defining first, then major towers / Bund, then the rest.
FAR_TIERS = {
    9000: "外滩, The Bund, 东方明珠, Oriental Pearl, Oriental Pearl Tower, 東方明珠廣播電視塔, 上海中心大厦, Shanghai Tower, 黄浦江, Huangpu River, 黄浦江, Huangpu",
    5000: "金茂大厦, Jin Mao Tower, 上海环球金融中心, Shanghai World Financial Center, SWFC, 南京东路, East Nanjing Road, Nanjing Road, 南京路, 陆家嘴, Lujiazui, 陆家嘴金融贸易区, 人民广场, People's Square, 静安寺, Jing'an Temple, Jingan Temple",
    3500: "和平饭店, Peace Hotel, 上海海关, Customs House, Customs House, Shanghai, 江海关, 外白渡桥, Waibaidu Bridge, Garden Bridge, 汇丰银行大楼, HSBC Building, 滙豐銀行大樓, 中国银行大楼, Bank of China Building, 沙逊大厦, Sassoon House, 上海博物馆, Shanghai Museum, 北外滩, North Bund, 豫园, Yu Garden, Yuyuan Garden, 老城厢, Old City",
    2500: "上海国际金融中心, Shanghai IFC, 上海大会堂, 上海国际会议中心, Oriental Riverside, 陈毅雕像, Chen Yi statue, 黄浦公园, 黃浦公園, Huangpu Park, 陆家嘴中心绿地, Lujiazui Central Green Space, 延安东路隧道, 人民公园, People's Park, 百老汇大厦, Broadway Mansions, 上海大厦, 上海城市规划展示馆, Shanghai Urban Planning Exhibition Hall, 国际饭店, Park Hotel, 上海音乐厅, Shanghai Concert Hall, 大世界, The Great World, 南京路步行街, Nanjing Road Pedestrian Street, 静安公园, Jing'an Park, Jingan Park, 南京西路, West Nanjing Road, Nanjing Road West, 上海商城, Shanghai Centre, 静安嘉里中心, Jing An Kerry Centre, 国际客运中心, International Cruise Terminal, 上海港国际客运中心, 北外滩来福士, Raffles City The Bund, 苏州河北岸, North bank of Suzhou Creek, 新天地, Xintiandi, 城隍庙, 上海城隍庙, City God Temple, 上海文庙, Shanghai Confucian Temple, 中共一大会址, Site of the First CPC National Congress, 大境阁, Dajing Pavilion",
    1500: "上海海洋水族馆, Shanghai Ocean Aquarium, 金辉大厦, 香格里拉, Shangri-La, 浦东美术馆, 上海自然博物馆",
}

FAR = {n.strip(): far for far, names in FAR_TIERS.items() for n in names.split(",") if n.strip()}

# prefer the short, commonly used name on the pin
NAMES = {
    "東方明珠廣播電視塔": ("东方明珠", "Oriental Pearl Tower", "tower"),
    "东方明珠广播电视塔": ("东方明珠", "Oriental Pearl Tower", "tower"),
    "滙豐銀行大樓": ("汇丰银行大楼", "HSBC Building", "building"),
    "汇丰银行大楼": ("汇丰银行大楼", "HSBC Building", "building"),
    "江海关": ("上海海关", "Customs House", "building"),
    "陆家嘴金融贸易区": ("陆家嘴", "Lujiazui", "neighborhood"),
    "南京路": ("南京路", "Nanjing Road", "street"),
    "南京东路": ("南京东路", "East Nanjing Road", "street"),
    "上海环球金融中心": ("上海环球金融中心", "Shanghai World Financial Center", "tower"),
    "上海中心大厦": ("上海中心大厦", "Shanghai Tower", "tower"),
    "金茂大厦": ("金茂大厦", "Jin Mao Tower", "tower"),
    "外滩": ("外滩", "The Bund", "street"),
    "外白渡桥": ("外白渡桥", "Waibaidu Bridge", "bridge"),
    "上海外滩华尔道夫酒店": ("华尔道夫酒店", "Waldorf Astoria Shanghai on the Bund", "hotel"),
    "圣三一堂 (上海)": ("圣三一堂", "Holy Trinity Church", "church"),
    "上海证券大厦": ("上海证券大厦", "Shanghai Securities Building", "tower"),
    "Nine-Zigzag Bridge": ("九曲桥", "Nine-Zigzag Bridge", "bridge"),
    "和平饭店": ("和平饭店", "Peace Hotel", "hotel"),
    "沙逊大厦": ("沙逊大厦", "Sassoon House", "building"),
    "人民广场": ("人民广场", "People's Square", "square"),
    "人民公园": ("人民公园", "People's Park", "park"),
    "上海博物馆": ("上海博物馆", "Shanghai Museum", "museum"),
    "上海城市规划展示馆": ("上海城市规划展示馆", "Shanghai Urban Planning Exhibition Hall", "museum"),
    "国际饭店": ("国际饭店", "Park Hotel", "hotel"),
    "上海音乐厅": ("上海音乐厅", "Shanghai Concert Hall", "building"),
    "大世界": ("大世界", "The Great World", "building"),
    "南京路步行街": ("南京路步行街", "Nanjing Road Pedestrian Street", "street"),
    "静安寺": ("静安寺", "Jing'an Temple", "temple"),
    "静安公园": ("静安公园", "Jing'an Park", "park"),
    "南京西路": ("南京西路", "West Nanjing Road", "street"),
    "上海商城": ("上海商城", "Shanghai Centre", "tower"),
    "静安嘉里中心": ("静安嘉里中心", "Jing An Kerry Centre", "building"),
    "北外滩": ("北外滩", "North Bund", "neighborhood"),
    "北外滩街道": ("北外滩", "North Bund", "neighborhood"),
    "国际客运中心": ("国际客运中心", "International Cruise Terminal", "building"),
    "上海港国际客运中心": ("国际客运中心", "International Cruise Terminal", "building"),
    "北外滩来福士": ("北外滩来福士", "Raffles City The Bund", "building"),
    "苏州河北岸": ("苏州河北岸", "North bank of Suzhou Creek", "waterfront"),
    "百老汇大厦": ("百老汇大厦", "Broadway Mansions", "hotel"),
    "上海大厦": ("百老汇大厦", "Broadway Mansions", "hotel"),
    "豫园": ("豫园", "Yu Garden", "garden"),
    "上海城隍庙": ("上海城隍庙", "City God Temple", "temple"),
    "上海城隍廟": ("上海城隍庙", "City God Temple", "temple"),
    "城隍庙": ("上海城隍庙", "City God Temple", "temple"),
    "老城厢": ("老城厢", "Old City", "neighborhood"),
    "新天地": ("新天地", "Xintiandi", "neighborhood"),
    "上海文庙": ("上海文庙", "Shanghai Confucian Temple", "temple"),
    "文庙上海文庙": ("上海文庙", "Shanghai Confucian Temple", "temple"),
    "中共一大会址": ("中共一大会址", "Site of the First CPC National Congress", "historic"),
    "中共一大纪念馆": ("中共一大纪念馆", "Memorial of the First CPC National Congress", "museum"),
    "大境阁": ("大境阁", "Dajing Pavilion", "temple"),
}



# types we never auto-label (people, events, orgs, media…); monuments and named streets on the allow-list are kept
SKIP_TYPE = (
    "human", "people", "event", "recurring event", "organization", "company", "business",
    "enterprise", "Wikimedia", "scholarly article", "academic journal", "film", "album",
    "song", "television", "election", "political party", "family name", "given name",
    "taxon", "chemical", "metro station", "subway station", "bus stop", "tram stop",
    "stampede", "ghetto", "district of China", "subdistrict", "slaughterhouse",
    "marriage market", "restaurant", "misbombing", "interchange station",
    "underground station", "ship", "warship", "wreck", "gunboat",
    "metro line", "rapid transit railway line", "railway line",
)
KEEP_STREETS = {"外滩", "The Bund", "南京路", "南京东路", "Nanjing Road", "East Nanjing Road", "中山东一路"}
DROP_NAMES = {
    "海燕號", "HMS Peterel", "上海地铁4号线", "Shanghai Metro Line 4",
    "东岸绿道",  # a path, not a bridge
    "Lujiazui Ring Road Circular Footbridge",  # English-only OSM footbridge; cluttered the skyline
    "陆家嘴环路人行天桥",
    "人民公园相亲角", "Shanghai Marriage Market",
    "大世界墜彈慘案", "Bloody Saturday (Shanghai)",
    "大上海计划", "Greater Shanghai Plan",
    "上海英租界", "British concession of Shanghai",
    "黑色圣诞之夜空难", "Black Christmas disaster",
    "南北高架路", "North-South Elevated Road",
    "延安高架路", "Yan'an Elevated Road",
    "北横通道", "Beiheng Passageway",
}

KIND_FROM_TYPE = {
    "skyscraper": "tower", "tower": "tower", "tower block": "tower",
    "hotel": "hotel", "museum": "museum", "art museum": "museum",
    "park": "park", "garden": "garden", "public park": "park", "urban park": "park",
    "bridge": "bridge", "iron bridge": "bridge", "steel bridge": "bridge",
    "monument": "monument", "statue": "monument", "memorial": "monument", "sculpture": "monument",
    "square": "square", "plaza": "square",
    "church": "church", "cathedral": "church", "temple": "temple", "buddhist temple": "temple",
    "pagoda": "pagoda", "synagogue": "synagogue",
    "river": "river", "watercourse": "river",
    "historic building": "building", "palace": "building", "building": "building",
    "office building": "building", "department store": "building", "shopping center": "building",
    "theatre": "theatre", "theater": "theatre", "opera house": "theatre",
    "aquarium": "aquarium", "convention center": "building",
}


def far_from_views(v: int) -> int:
    if v >= 400_000:
        return 5000
    if v >= 150_000:
        return 3500
    if v >= 60_000:
        return 2500
    if v >= 20_000:
        return 1500
    if v >= 8_000:
        return 1000
    if v >= 2_000:
        return 700
    if v >= 600:
        return 450
    return 300


def _skip_types(types: str, name: str) -> bool:
    if name in KEEP_STREETS or name in FAR:
        return False
    tl = (types or "").lower()
    return any(s.lower() in tl for s in SKIP_TYPE)


def _kind(types: str, default="building") -> str:
    parts = [p.strip().lower() for p in (types or "").split("|") if p.strip()]
    for p in parts:
        if p in KIND_FROM_TYPE:
            return KIND_FROM_TYPE[p]
    for p in parts:
        for k, kind in KIND_FROM_TYPE.items():
            if k in p:
                return kind
    return default


def lonlat_to_grid(lon, lat):
    x, y = _T.transform(float(lon), float(lat))
    return to_grid(x, y)


def extent():
    """Streamed coverage: x in [x0, W), y in [y0, D). y0 may be negative."""
    if META.exists():
        m = json.loads(META.read_text())
        return int(m.get("x0", 0)), int(m.get("y0", 0)), int(m.get("W", W)), int(m.get("D", D))
    return 0, 0, W, D


def inside(i, j, m=4):
    x0, y0, w, d = extent()
    return x0 + m <= i < w - m and y0 + m <= j < d - m


def sample_g(g, r, c):
    """g is either the first-window (D,W) raster or the stitched ground_all (D-y0, W-x0)."""
    x0, y0, w, d = extent()
    if g.shape[0] == d - y0 and y0 != 0:
        rr = int(np.clip(r - y0, 0, g.shape[0] - 1))
        cc = int(np.clip(c - x0, 0, g.shape[1] - 1))
    else:
        rr = int(np.clip(r, 0, g.shape[0] - 1))
        cc = int(np.clip(c, 0, g.shape[1] - 1))
    return int(g[rr, cc])


def top_at(g, r, c, h=12):
    return sample_g(g, r, c) + h


def site_of(e, tags=None):
    return (e or {}).get("site") or (tags or {}).get("website") or (tags or {}).get("contact:website")


def view(label, target, pos):
    return dict(label=label, target=[float(target[0]), float(target[1]), float(target[2])],
                pos=[float(pos[0]), float(pos[1]), float(pos[2])])


def load_osm():
    import osm as osm_mod
    for name in ("osm_expanded.json", "osm.json"):
        path = Path(RAW) / name
        if path.exists():
            return osm_mod.OSM(str(path))
    return osm_mod.OSM(f"{RAW}/osm.json")


def index_osm(osm):
    """qid -> list of (tags, geom_or_None, kind_hint, cx, cy) and name -> same."""
    by_qid, by_name = {}, {}

    def add(qid, name, rec):
        if qid:
            by_qid.setdefault(qid, []).append(rec)
        if name:
            by_name.setdefault(name, []).append(rec)

    def centroid(geom):
        try:
            p = geom.representative_point()
            return float(p.x), float(p.y)
        except Exception:
            return None

    for oid, typ, t, geom in osm.areas:
        c = centroid(geom)
        if not c:
            continue
        rec = dict(tags=t, x=c[0], y=c[1], kind="area", height=_num(t.get("height")) or (
            _num(t.get("building:levels")) or 0) * 3.2 or None)
        add(t.get("wikidata"), t.get("name"), rec)
        if t.get("name:en"):
            add(None, t.get("name:en"), rec)
        if t.get("name:zh"):
            add(None, t.get("name:zh"), rec)
    for oid, t, geom in osm.nodes:
        try:
            x, y = float(geom.x), float(geom.y)
        except Exception:
            continue
        rec = dict(tags=t, x=x, y=y, kind="node", height=None)
        add(t.get("wikidata"), t.get("name"), rec)
    for oid, t, geom in osm.lines:
        try:
            p = geom.interpolate(0.5, normalized=True)
            x, y = float(p.x), float(p.y)
        except Exception:
            continue
        rec = dict(tags=t, x=x, y=y, kind="line", height=None)
        add(t.get("wikidata"), t.get("name"), rec)
        if t.get("name:en"):
            add(None, t.get("name:en"), rec)
    return by_qid, by_name


def _num(v, default=None):
    try:
        return float(str(v).split(";")[0].split()[0].replace(",", "."))
    except (ValueError, IndexError, AttributeError, TypeError):
        return default


def pick_place(e, qid, by_qid, by_name):
    """Prefer an OSM feature with this wikidata id; else a name match inside the AOI; else P625."""
    recs = list(by_qid.get(qid) or [])
    names = [n for n in (e.get("zh"), e.get("en"), e.get("zhwiki"), e.get("enwiki")) if n]
    for n in names:
        recs.extend(by_name.get(n) or [])
    # official English names are often longer than the OSM tag
    # ("Waldorf Astoria" vs "Waldorf Astoria Shanghai on the Bund"). Do not
    # substring-match short Chinese names: 陆家嘴 would steal 陆家嘴中心绿地.
    for key, rec_list in by_name.items():
        if not key or len(key) < 10:
            continue
        if not any(c.isascii() and c.isalpha() for c in key):
            continue
        if any(key in n or n in key for n in names if n and len(n) >= 10):
            recs.extend(rec_list)
    # unique by (x,y)
    seen, uniq = set(), []
    for r in recs:
        key = (round(r["x"], 1), round(r["y"], 1))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    # prefer areas, then something clearly on the Bund (y ~ 200-800) for Bund historic names
    bundish = any(s in (e.get("zh") or "") + (e.get("en") or "") for s in
                  ("汇丰", "HSBC", "海关", "Customs", "和平饭店", "Peace Hotel", "中国银行", "Bank of China",
                   "沙逊", "Sassoon", "外滩", "Bund", "海关大楼", "华尔道夫", "Waldorf", "总会", "Shanghai Club"))
    scored = []
    for r in uniq:
        if not inside(r["x"], r["y"]):
            continue
        score = 0
        if r["kind"] == "area":
            score += 3
        if r["tags"].get("wikidata") == qid:
            score += 5
        if bundish and 200 <= r["y"] <= 900:
            score += 8
        if bundish and r["y"] > 1200:  # Pudong lookalikes for Bund buildings
            score -= 6
        scored.append((score, r))
    if scored:
        scored.sort(key=lambda kv: -kv[0])
        best = scored[0][1]
        # historic Bund names sometimes share an English name with a Pudong office tower
        if bundish and best["y"] > 1100:
            bund_hits = [r for s, r in scored if r["y"] <= 900]
            if bund_hits:
                return bund_hits[0]
            # fall through to P625
        else:
            return best
    lon, lat = e.get("at") or [None, None]
    if lon is None:
        return None
    x, y = lonlat_to_grid(lon, lat)
    if not inside(x, y):
        return None
    return dict(tags={}, x=float(x), y=float(y), kind="wd", height=None)


def greens(osm, labels, g):
    GREEN = {("leisure", "park"): "park", ("leisure", "garden"): "garden",
             ("leisure", "marina"): "marina"}
    frame = box(0, 0, W, D)
    shapes = [(t, geom) for _, typ, t, geom in osm.areas]
    names = {l["name"] for l in labels}
    out = []
    for t, geom in shapes:
        kind = next((k for (key, v), k in GREEN.items() if t.get(key) == v), None)
        if not kind or not t.get("name") or t["name"] in names:
            continue
        if any(s in t["name"] for s in ("中心", "广场", "商场", "大厦")) and t.get("landuse") in ("retail", "commercial"):
            continue
        if t["name"] in ("星荟中心", "双辉广场"):
            continue
        try:
            gg = geom.buffer(0).intersection(frame)
            if gg.is_empty or gg.area < 8000:
                continue
            p = gg.representative_point()
        except Exception:
            continue
        if any(((p.x - l["x"]) ** 2 + (p.y - l["y"]) ** 2) < 40 ** 2 and l.get("kind") in
               ("park", "garden", "marina") for l in labels + out):
            continue
        out.append(dict(name=t["name"], en=t.get("name:en"), kind=kind, views=0,
                        site=site_of({}, t), auto=True, far=900,
                        x=int(p.x), y=int(p.y), z=top_at(g, p.y, p.x, 8)))
        names.add(t["name"])
    return out


def bridges(osm, labels, g):
    names = {l["name"] for l in labels}
    out = []
    for oid, t, geom in osm.lines:
        if t.get("bridge") in (None, "no") or not t.get("name"):
            continue
        if t.get("name") in names:
            continue
        if t.get("highway") is None and t.get("railway") is None:
            continue
        if t.get("railway") in ("subway", "rail", "light_rail") or "地铁" in t.get("name", ""):
            continue
        if "绿道" in (t.get("name") or "") or t.get("highway") == "cycleway":
            continue
        try:
            if geom.length < 80:
                continue
            p = geom.interpolate(0.5, normalized=True)
        except Exception:
            continue
        if not inside(p.x, p.y):
            continue
        out.append(dict(name=t["name"], en=t.get("name:en"), kind="bridge", views=0,
                        site=site_of({}, t), auto=True, far=1200,
                        x=int(p.x), y=int(p.y), z=top_at(g, p.y, p.x, 16)))
        names.add(t["name"])
    return out


def build(g, osm=None, notable_path=NOTABLE):
    """Return a finished labels list (no views field) ready for meta.json."""
    osm = osm or load_osm()
    wd = json.loads(Path(notable_path).read_text()) if Path(notable_path).exists() else {}
    by_qid, by_name = index_osm(osm)
    labels = []
    for qid, e in wd.items():
        name = e.get("zh") or e.get("en") or e.get("zhwiki") or e.get("enwiki")
        if not name:
            continue
        if name in DROP_NAMES or (e.get("en") in DROP_NAMES):
            continue
        if _skip_types(e.get("types") or "", name):
            continue
        rec = pick_place(e, qid, by_qid, by_name)
        if not rec:
            continue
        h = rec.get("height") or 12
        # towers / well-known heights from OSM or a generous default
        kind = _kind(e.get("types") or "", "building")
        if kind == "river":
            h = 8
        z = top_at(g, rec["y"], rec["x"], h if h < 800 else 80)
        # for very tall buildings, put the label near mid-height so it reads against the shaft
        if h and h >= 200:
            z = top_at(g, rec["y"], rec["x"], min(h, 400))
        labels.append(dict(
            name=name,
            en=e.get("en") or rec["tags"].get("name:en"),
            kind=kind,
            views=int(e.get("views") or 0),
            site=site_of(e, rec["tags"]),
            auto=True,
            x=int(round(rec["x"])),
            y=int(round(rec["y"])),
            z=int(z),
            g=sample_g(g, rec["y"], rec["x"]),
            alt=" ".join(filter(None, [e.get("zh"), e.get("en")])),
            qid=qid,
        ))

    labels.extend(greens(osm, labels, g))
    labels.extend(bridges(osm, labels, g))

    # landmarks in the west block that Wikidata's first-window SPARQL box missed
    names = {l["name"] for l in labels}
    for want, en, kind, extra_h in (
        ("人民广场", "People's Square", "square", 8),
        ("人民公园", "People's Park", "park", 6),
        ("上海博物馆", "Shanghai Museum", "museum", 28),
        ("上海城市规划展示馆", "Shanghai Urban Planning Exhibition Hall", "museum", 32),
        ("国际饭店", "Park Hotel", "hotel", 80),
        ("上海音乐厅", "Shanghai Concert Hall", "building", 24),
        ("大世界", "The Great World", "building", 28),
        ("南京路步行街", "Nanjing Road Pedestrian Street", "street", 6),
        ("静安寺", "Jing'an Temple", "temple", 30),
        ("静安公园", "Jing'an Park", "park", 8),
        ("南京西路", "West Nanjing Road", "street", 6),
        ("上海商城", "Shanghai Centre", "tower", 160),
        ("静安嘉里中心", "Jing An Kerry Centre", "building", 80),
        ("北外滩", "North Bund", "neighborhood", 12),
        ("国际客运中心", "International Cruise Terminal", "building", 40),
        ("上海港国际客运中心", "International Cruise Terminal", "building", 40),
        ("北外滩来福士", "Raffles City The Bund", "building", 80),
        ("豫园", "Yu Garden", "garden", 10),
        ("上海城隍庙", "City God Temple", "temple", 16),
        ("老城厢", "Old City", "neighborhood", 12),
        ("新天地", "Xintiandi", "neighborhood", 10),
        ("上海文庙", "Shanghai Confucian Temple", "temple", 16),
        ("中共一大会址", "Site of the First CPC National Congress", "historic", 10),
        ("大境阁", "Dajing Pavilion", "temple", 20),
    ):
        if want in names or en in names:
            continue
        hit = None
        cov = box(*extent())
        for _oid, typ, tg, geom in osm.areas:
            n = tg.get("name") or tg.get("name:zh") or ""
            if n == want or tg.get("name:en") == en:
                # prefer a point that already sits in the streamed coverage
                try:
                    inter = geom.intersection(cov)
                    if not inter.is_empty:
                        hit = inter.representative_point(); break
                except Exception:
                    pass
                hit = geom.representative_point(); break
        if hit is None:
            for _oid, tg, geom in osm.nodes:
                n = tg.get("name") or tg.get("name:zh") or ""
                if (n == want or tg.get("name:en") == en) and not tg.get("highway") and not tg.get("public_transport") and tg.get("amenity") not in ("bus_stop", "parking"):
                    hit = geom; break
        if hit is None:
            for _oid, tg, geom in osm.lines:
                n = tg.get("name") or tg.get("name:zh") or ""
                if n == want or tg.get("name:en") == en:
                    # pin a point of the line that already sits in coverage
                    try:
                        clipped = geom.intersection(box(*extent()))
                        if not clipped.is_empty:
                            hit = clipped.interpolate(0.5, normalized=True) if clipped.geom_type in ("LineString", "MultiLineString") else clipped.representative_point()
                            break
                    except Exception:
                        hit = geom.interpolate(0.5, normalized=True)
                        break
        if hit is None:
            continue
        if not inside(hit.x, hit.y):
            continue
        labels.append(dict(name=want, en=en, kind=kind, views=0, auto=True,
                           far=FAR.get(want) or FAR.get(en, 2500),
                           x=int(hit.x), y=int(hit.y),
                           z=top_at(g, hit.y, hit.x, extra_h),
                           g=sample_g(g, hit.y, hit.x)))
        names.add(want)

    # typological pin at the centre of the Renmin / Zhonghua Road ring (老城厢)
    if "老城厢" not in names and inside(-310, 157):
        labels.append(dict(name="老城厢", en="Old City", kind="neighborhood",
                           views=0, auto=True, far=FAR.get("老城厢", 3500),
                           x=-310, y=157, z=top_at(g, 157, -310, 12),
                           g=sample_g(g, 157, -310)))
        names.add("老城厢")

    # typological pin on the north bank of Suzhou Creek, just north of Waibaidu
    if "苏州河北岸" not in names and inside(2160, 520):
        labels.append(dict(name="苏州河北岸", en="North bank of Suzhou Creek", kind="waterfront",
                           views=0, auto=True, far=FAR.get("苏州河北岸", 2500),
                           x=2160, y=520, z=top_at(g, 520, 2160, 8),
                           g=sample_g(g, 520, 2160)))
        names.add("苏州河北岸")
    # alias: keep 百老汇大厦 pin, expose 上海大厦 in alt
    for l in labels:
        if l["name"] == "百老汇大厦":
            alt = l.get("alt") or ""
            if "上海大厦" not in alt:
                l["alt"] = (alt + " 上海大厦").strip()

    # named monuments / statues on OSM that Wikidata may miss
    names = {l["name"] for l in labels}
    for _oid, t, geom in osm.nodes:
        if not t.get("name"):
            continue
        if t.get("historic") not in ("memorial", "monument", "statue") and t.get("tourism") != "artwork" and t.get("memorial") is None:
            continue
        if t["name"] in names:
            continue
        if not inside(geom.x, geom.y):
            continue
        far = FAR.get(t["name"]) or FAR.get(t.get("name:en") or "", 1500)
        labels.append(dict(name=t["name"], en=t.get("name:en"), kind="monument", views=0,
                           auto=True, far=far, x=int(geom.x), y=int(geom.y),
                           z=top_at(g, geom.y, geom.x, 8)))
        names.add(t["name"])

    # Huangpu River often has no Wikidata point inside the bbox; pin it from OSM water
    if not any(l["name"] in ("黄浦江", "Huangpu River") for l in labels):
        river = None
        for _oid, _typ, t, geom in osm.areas:
            if t.get("name") in ("黄浦江", "Huangpu River", "黃浦江") or t.get("name:en") == "Huangpu River":
                river = geom
                break
        if river is None:
            for _oid, t, geom in osm.lines:
                if t.get("name") in ("黄浦江", "Huangpu River", "黃浦江") or t.get("name:en") == "Huangpu River":
                    river = geom
                    break
        if river is not None:
            try:
                clipped = river.intersection(box(*extent()))
                pnt = clipped.interpolate(0.5, normalized=True) if clipped.geom_type in ("LineString", "MultiLineString") else clipped.representative_point()
                labels.append(dict(name="黄浦江", en="Huangpu River", kind="river", views=200000,
                                   auto=True, far=9000, x=int(pnt.x), y=int(pnt.y),
                                   z=top_at(g, pnt.y, pnt.x, 6),
                                   g=sample_g(g, pnt.y, pnt.x)))
            except Exception:
                pass

    # Suzhou Creek: prefer the Bund confluence, not a midpoint of a long west reach
    for l in [x for x in labels if x["name"] in ("吴淞江", "苏州河", "Suzhou Creek")]:
        if l["y"] < 200 or l["y"] > 900 or l["x"] < 1500:
            labels.remove(l)
    if not any(x["name"] in ("吴淞江", "苏州河", "Suzhou Creek") for x in labels):
        labels.append(dict(name="吴淞江", en="Suzhou Creek", kind="river", views=5000,
                           auto=True, far=2500, x=1994, y=622, z=16, g=12))

    # 人民广场: the park-tagged way's representative point sits in the grass.
    # Prefer the paved pedestrian square (OSM relation/way, highway=pedestrian).
    paved = None
    for oid, typ, tg, geom in osm.areas:
        n = tg.get("name") or ""
        if n != "人民广场" and tg.get("name:en") != "People's Square":
            continue
        if typ == "relation" and (tg.get("highway") == "pedestrian" or tg.get("place") == "square"):
            paved = geom
            break
        if tg.get("highway") == "pedestrian" or tg.get("place") == "square":
            paved = geom
    if paved is not None:
        pt = paved.representative_point()
        if inside(pt.x, pt.y):
            for l in labels:
                if l["name"] == "人民广场" or l.get("en") == "People's Square":
                    l["x"], l["y"] = int(round(pt.x)), int(round(pt.y))
                    l["g"] = sample_g(g, l["y"], l["x"])
                    l["z"] = int(top_at(g, l["y"], l["x"], 8))
                    l["kind"] = "square"

    # finish: canonical names, far, sort, drop overlaps
    labels = [l for l in labels if l["name"] not in DROP_NAMES and (l.get("en") or "") not in DROP_NAMES]
    for l in labels:
        if l["name"] in NAMES:
            pl, en, kind = NAMES[l["name"]]
            l["alt"] = " ".join(filter(None, [l["name"] if pl != l["name"] else "", l.get("en"), l.get("alt")]))
            l.update(name=pl, en=en, kind=kind)
        key_names = [l["name"], l.get("en") or ""]
        far = next((FAR[n] for n in key_names if n in FAR), None)
        l["far"] = far or far_from_views(int(l.get("views") or 0))
        if l["kind"] == "park" and l["far"] < 900:
            l["far"] = 900
        if l["kind"] == "bridge" and l["far"] < 1000:
            l["far"] = max(l["far"], 1000)
    labels.sort(key=lambda l: (-l["far"], -int(l.get("views") or 0)))
    if any(l["name"] == "南京东路" for l in labels):
        labels = [l for l in labels if l["name"] != "南京路"]

    # one pin per canonical name (two Wikidata items sometimes describe the same building)
    uniq, seen_n = [], set()
    for l in labels:
        if l["name"] in seen_n:
            continue
        seen_n.add(l["name"])
        uniq.append(l)
    labels = uniq
    kept = []
    for l in labels:
        if all((l["x"] - k["x"]) ** 2 + (l["y"] - k["y"]) ** 2 > 20 ** 2 for k in kept):
            kept.append(l)
    # cap auto labels so the skyline stays readable; always keep far >= 1500
    hard, soft = [], []
    for l in kept:
        (hard if l["far"] >= 1500 else soft).append(l)
    labels = hard + soft[:40]

    for l in labels:
        l.pop("views", None)
        l.pop("auto", None)
        l.pop("qid", None)
        for k in [k for k, v in l.items() if v is None or v == ""]:
            del l[k]
    return labels


def find_label(labels, *needles):
    for l in labels:
        blob = " ".join(filter(None, [l.get("name"), l.get("en"), l.get("alt")]))
        if any(n and n in blob for n in needles):
            return l
    return None


def camera_presets(labels, existing=None):
    """Keep existing overview/bund/skyline etc. and add the requested ones."""
    views = dict(existing or {})

    def z_of(l, default=30):
        return int(l.get("g") or 10) + 8 if l else default

    pearl = find_label(labels, "东方明珠", "Oriental Pearl") or dict(x=1473, y=1364, z=200, g=10)
    tower = find_label(labels, "上海中心", "Shanghai Tower")
    sw = find_label(labels, "环球金融", "World Financial")
    jm = find_label(labels, "金茂", "Jin Mao")
    wb = find_label(labels, "外白渡", "Waibaidu", "Garden Bridge")
    peace = find_label(labels, "和平饭店", "Peace Hotel")
    bund = find_label(labels, "外滩", "The Bund")

    views.setdefault("overview", view("Overview", [pearl["x"], 30, pearl["y"]], [2100, 520, 200]))
    views.setdefault("pearl", view("Oriental Pearl", [pearl["x"], 80, pearl["y"]],
                                   [pearl["x"] + 420, 220, pearl["y"] + 380]))
    views.setdefault("bund", view("The Bund", [pearl["x"], 60, pearl["y"]], [1650, 55, 480]))
    views.setdefault("skyline", view("Bund skyline", [1100, 80, 1600], [1500, 45, 420]))
    if tower:
        views.setdefault("shanghai_tower", view("Shanghai Tower", [tower["x"], 120, tower["y"]],
                                                [tower["x"] - 350, 280, tower["y"] + 450]))
    def h_of(l, extra=8):
        return int(l.get("g") or 12) + extra

    if wb:
        # slightly up-river on the Huangpu, looking north-west at the span
        # along the Bund promenade looking north toward the span and Broadway Mansions
        # close enough to read the camelback truss, slightly above the 6.9 m promenade
        # 3/4 elevation from the Huangpu, so the two camelback spans and Warren web read in profile
        views["waibaidu"] = view("Waibaidu Bridge", [wb["x"], 18, wb["y"]],
                                 [2045, 24, 455])
    if peace:
        # street along East Nanjing Road is now ~7–8 m (voxel z ≈ 16) after DSM flattening
        views["nanjing"] = view("East Nanjing Road", [1562, 20, 400],
                                [1562, 24, 250])
        # along the Bund roadway / promenade, just above the 6.9 m wall (voxel z ≈ 16)
        views["bund_street"] = view("Bund street", [peace["x"] - 80, 18, peace["y"] + 80],
                                    [peace["x"] - 280, 20, peace["y"] + 90])
    if sw:
        views["swfc_aperture"] = view("SWFC aperture", [sw["x"], 465, sw["y"]],
                                      [sw["x"] - 340, 470, sw["y"]])
    if jm:
        views["jinmao"] = view("Jin Mao Tower", [jm["x"], 200, jm["y"]],
                               [jm["x"] - 180, 160, jm["y"] - 220])
    if bund and "lujiazui" not in views:
        views["lujiazui"] = view("Lujiazui", [bund["x"], 40, bund["y"] + 200], [873, 280, 1164])

    ps = find_label(labels, "人民广场", "People's Square")
    if ps:
        # looking south-west over the square from the Nanjing Road / park side
        views["peoples_square"] = view("People's Square",
                                       [ps["x"], 24, ps["y"]],
                                       [ps["x"] + 220, 70, ps["y"] + 380])
        # from the Bund corridor looking west into the new tiles, no hole at y=0
        views["west_from_bund"] = view("West from the Bund",
                                       [ps["x"], 40, ps["y"]],
                                       [1520, 90, 420])
    # stand just inside the first window on East Nanjing Road, looking west across the seam
    views["nanjing_west"] = view("Nanjing Road west", [1516, 18, -80], [1540, 20, 90])

    ja = find_label(labels, "静安寺", "Jing'an Temple")
    jp = find_label(labels, "静安公园", "Jing'an Park")
    nw = find_label(labels, "南京西路", "West Nanjing Road")
    if ja:
        # from the east (People's Square / Nanjing West side) looking at the temple
        views["jingan_temple"] = view("Jing'an Temple",
                                      [769, 20, -4104],
                                      [700, 36, -4220])
        # along Nanjing Road West, mid-height, looking west to the temple / Kerry / Shanghai Centre
        views["toward_jingan"] = view("Toward Jing'an",
                                      [800, 35, -3900],
                                      [1100, 45, -2400])
    if nw and "nanjing_road_west" not in views:
        views["nanjing_road_west"] = view("West Nanjing Road",
                                          [nw["x"], 18, nw["y"]],
                                          [nw["x"] + 40, 28, nw["y"] + 220])
    if jp and "jingan_park" not in views:
        views["jingan_park"] = view("Jing'an Park",
                                    [jp["x"], 16, jp["y"]],
                                    [jp["x"] + 80, 40, jp["y"] + 200])

    # North Bund already sits inside the first typ≥0 window; do not shift the grid.
    nb = find_label(labels, "北外滩", "North Bund")
    cruise = find_label(labels, "国际客运中心", "International Cruise Terminal", "上海港国际客运中心")
    raffles = find_label(labels, "北外滩来福士", "Raffles City The Bund")
    broadway = find_label(labels, "百老汇大厦", "Broadway Mansions", "上海大厦")
    # district view: from the river looking at the north-bank waterfront
    if cruise:
        views["north_bund"] = view("North Bund",
                                   [cruise["x"], 30, cruise["y"]],
                                   [cruise["x"] - 280, 70, cruise["y"] - 420])
    elif nb:
        views["north_bund"] = view("North Bund",
                                   [nb["x"], 30, nb["y"]],
                                   [nb["x"] - 300, 80, nb["y"] - 400])
    # from south of Waibaidu looking continuously to the north bank / Broadway Mansions
    if wb and broadway:
        views["waibaidu_to_north"] = view("Waibaidu to North Bund",
                                          [2143, 28, 565],
                                          [1920, 42, 400])
    elif wb:
        views["waibaidu_to_north"] = view("Waibaidu to North Bund",
                                          [wb["x"] + 180, 28, wb["y"] + 20],
                                          [1980, 32, 520])
    pk = find_label(labels, "人民公园", "People's Park")
    if pk and "peoples_park_trees" not in views:
        # close enough to read schematic crowns, looking across the park
        views["peoples_park_trees"] = view("People's Park trees",
                                           [pk["x"], 18, pk["y"]],
                                           [pk["x"] + 71, 42, pk["y"] + 136])

    yg = find_label(labels, "豫园", "Yu Garden")
    ch = find_label(labels, "上海城隍庙", "City God Temple", "城隍庙", "上海城隍廟")
    xt = find_label(labels, "新天地", "Xintiandi")
    oc = find_label(labels, "老城厢", "Old City")
    wm = find_label(labels, "上海文庙", "Shanghai Confucian Temple")
    if yg:
        views["yuyuan"] = view("Yu Garden",
                               [yg["x"], 16, yg["y"]],
                               [280, 58, 500])
        views["toward_yuyuan"] = view("Toward Yu Garden",
                                      [yg["x"], 22, yg["y"]],
                                      [1200, 55, 550])
    if ch:
        views["chenghuang"] = view("City God Temple",
                                   [ch["x"], 18, ch["y"]],
                                   [150, 48, 290])
    if xt:
        views["xintiandi"] = view("Xintiandi",
                                  [xt["x"], 16, xt["y"]],
                                  [xt["x"] + 80, 28, xt["y"] + 140])
        views["toward_xintiandi"] = view("Toward Xintiandi",
                                         [xt["x"], 22, xt["y"]],
                                         [992, 70, -835])
    if oc and "old_city" not in views:
        views["old_city"] = view("Old City",
                                 [oc["x"], 20, oc["y"]],
                                 [100, 80, 400])
    if wm and "wenmiao" not in views:
        views["wenmiao"] = view("Shanghai Confucian Temple",
                                [wm["x"], 16, wm["y"]],
                                [wm["x"] + 60, 32, wm["y"] + 100])

    x0, y0, w, d = extent()
    for v in views.values():
        v["pos"][0] = float(np.clip(v["pos"][0], x0 - 800, w + 800))
        v["pos"][2] = float(np.clip(v["pos"][2], y0 - 800, d + 800))
        v["target"][0] = float(np.clip(v["target"][0], x0, w - 1))
        v["target"][2] = float(np.clip(v["target"][2], y0, d - 1))
    return views


def ground_from_meta(meta, dem_path=ROOT / "data" / "dem_grid.npz"):
    allp = ROOT / "data" / "ground_all.npz"
    if allp.exists():
        z = np.load(allp)
        base = int(z["base"]) if "base" in z.files else int(meta.get("base", -9))
        return np.clip(np.round(z["ground"] - base), 1, 250).astype(np.int32)
    dem = np.load(dem_path)
    ground = dem["ground"]
    base = int(meta.get("base", -9))
    return np.clip(np.round(ground - base), 1, 250).astype(np.int32)


def apply_to_meta(meta=None, g=None):
    meta = meta or json.loads(META.read_text())
    g = g if g is not None else ground_from_meta(meta)
    osm = load_osm()
    labels = build(g, osm)
    meta["labels"] = labels
    meta["views"] = camera_presets(labels, meta.get("views"))
    src = meta.setdefault("sources", {})
    src["labels"] = "Wikidata items in the AOI, ranked by zh+en Wikipedia page views (Nov 2025–Oct 2026). far distances follow scripts/labels.py."
    META.parent.mkdir(parents=True, exist_ok=True)
    META.write_text(json.dumps(meta, ensure_ascii=False))
    print(f"labels: {len(labels)}  views: {list(meta['views'])}")
    for l in labels[:20]:
        print(f"  far={l['far']:<5} {l['name']}  ({l.get('en','')})  {l['kind']}  @ {l['x']},{l['y']}")
    return meta


if __name__ == "__main__":
    apply_to_meta()
