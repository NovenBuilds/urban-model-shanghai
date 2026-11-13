"""Build 1 m (and 8 m) voxel tiles for the Bund–Lujiazui AOI from OSM + a public DEM.

No LiDAR / government ortho is used. Building heights come from OSM height / levels /
building:parts (plus a documented, simplified Oriental Pearl profile from published
dimensions). Ground is a bare-earth estimate of Copernicus GLO-30.

Outputs under web/public/data/: tiles/{1,1f,8}/*.bin, meta.json, zones.bin, zones.json,
terrain.bin. Tile format matches the Warsaw viewer (scripts/tiles.py).
"""
from __future__ import annotations

import colorsys
import gzip
import hashlib
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
from affine import Affine
from PIL import Image
from rasterio.features import rasterize
from scipy import ndimage
from shapely.affinity import scale as shp_scale
from shapely.geometry import LineString, Point, box
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from aoi import (  # noqa: E402
    AOI_FILE, CRS, CX, CY, D, GLOBAL_I0, GLOBAL_J0, I0, J0, THETA_DEG, W, reload,
)
from osm import OSM  # noqa: E402
import tiles as T  # noqa: E402

reload()
from aoi import CX, CY, D, GLOBAL_I0, GLOBAL_J0, I0, J0, THETA_DEG, W  # noqa: E402

OUT = ROOT / "web" / "public" / "data"
DBG = ROOT / "data" / "debug"
OUT.mkdir(parents=True, exist_ok=True)
DBG.mkdir(parents=True, exist_ok=True)

HMAX = 720  # enough for Shanghai Tower (632 m) plus terrain
LEVEL_H = 3.2
report = {"notes": []}


def note(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    report["notes"].append(s)


# ---------------------------------------------------------------- materials / palette (same ids as the Warsaw viewer)
M_SOLID, M_WALL, M_GLASS, M_LEAF, M_WATER, M_LAMP, M_METAL, M_PKIN, M_GRASS, M_MARK, M_SOIL, M_CAR, M_ROOF, M_CONCRETE, M_CLOCK, M_BRONZE, M_SIGN = range(17)


class Palette:
    def __init__(self):
        self.entries = [(0, 0, 0, 0)]
        self.index = {}

    def __call__(self, rgb, mat=M_SOLID, q=4):
        if isinstance(rgb, str):
            rgb = hex_rgb(rgb)
        r, g, b = (min(255, int(round(v / q)) * q) for v in rgb)
        key = (r, g, b, mat)
        if key not in self.index:
            self.index[key] = len(self.entries)
            self.entries.append(key)
        return self.index[key]


def hex_rgb(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


COLOUR_ALIASES = {
    "grey": "gray", "lightgrey": "lightgray", "darkgrey": "darkgray", "cream": "#f1e9d2",
    "sand": "#d8c79f", "beige": "#ddd2b8", "brick": "#a5553f", "terracotta": "#c0623f",
    "glass": "#9fb5c2", "concrete": "#b8b5ae", "gold": "#cfae5c", "bronze": "#8c6c47",
    "copper": "#5f9c87", "ochre": "#cc9a4a", "salmon": "#e19a85", "pink": "#e6b3b3",
    "white": "#efede6", "black": "#3a3a3d", "red": "#b5513f", "brown": "#7e5a43",
    "yellow": "#e3c86a", "green": "#6e9a5e", "blue": "#6c8cad", "orange": "#d98b4e",
    "silver": "#c3c6c9", "gray": "#9a9a9a", "darkgray": "#6e6f72", "lightgray": "#cfcfcc",
}


def parse_colour(s):
    if not s:
        return None
    s = s.split(";")[0].strip().lower().replace(" ", "")
    s = COLOUR_ALIASES.get(s, s)
    s = COLOUR_ALIASES.get(s, s)
    try:
        import webcolors
        rgb = hex_rgb(s) if re.fullmatch(r"#?[0-9a-f]{6}|#?[0-9a-f]{3}", s) else hex_rgb(webcolors.name_to_hex(s))
    except ValueError:
        return None
    h_, l_, s_ = colorsys.rgb_to_hls(*(v / 255 for v in rgb))
    if s_ > 0.62 and 0.12 < l_ < 0.9:
        rgb = tuple(round(v * 255) for v in colorsys.hls_to_rgb(h_, l_, 0.62))
    return rgb


def parse_len(s):
    if s is None:
        return None
    m = re.match(r"\s*(-?[0-9]+(?:[.,][0-9]+)?)", str(s))
    return float(m.group(1).replace(",", ".")) if m else None


def soften(rgb, amt=0.12):
    return tuple(v * (1 - amt) + w * amt for v, w in zip(rgb, (226, 220, 208)))


def stable_hash(*a):
    return int(hashlib.md5("/".join(map(str, a)).encode()).hexdigest()[:8], 16)


pal = Palette()
C = {
    "paving": pal("#c6c2b8"), "paving2": pal("#b5b4af"), "asphalt": pal("#5f636b"),
    "asphalt_l": pal("#6e7178"), "sett": pal("#8f8981"), "road_paving": pal("#a49f97"),
    "concrete": pal("#bdbab2"), "cycle": pal("#8a5a52"), "gravel": pal("#d8ccb5"),
    "grass": pal("#86b45a", M_GRASS), "grass2": pal("#79a852", M_GRASS), "garden": pal("#73a34e", M_GRASS),
    "dirt": pal("#a58a66"), "water": pal("#5a6e7a", M_WATER), "water2": pal("#4e6270", M_WATER),
    "mark": pal("#f1efe8", M_MARK), "bare": pal("#a8977c", M_SOIL), "soil": pal("#7a5c45", M_SOIL),
    "trunk": pal("#6b4f3a"), "lamp": pal("#ffe2a8", M_LAMP), "pole": pal("#3a3e42", M_METAL),
    "kerb": pal("#cfc7b8"), "deck": pal("#a7a49d", M_CONCRETE), "rail": pal("#55575d", M_METAL),
    "iron": pal("#5a534c", M_METAL), "quay": pal("#9a958c", M_CONCRETE), "promenade": pal("#cfc8bc"),
    "ballast": pal("#8a8178"), "platform": pal("#b7b1a6"), "sand": pal("#d8c79f"),
}
LEAVES = [pal(c, M_LEAF) for c in
          ["#a3c65c", "#8db552", "#76a24a", "#618f43", "#4f7c3c", "#93bb6a", "#7da95f"]]
WALL_DEFAULTS = ["#d8cfc0", "#cbbfa8", "#e2d6bf", "#c4bbad", "#d9c7a7",
                 "#cdc6ba", "#e6ddcc", "#bdb09b", "#d3c3b0", "#c9c9c2"]
GLASS_DEFAULTS = ["#9fb5c2", "#8aa0b0", "#b0c4cc", "#7e96a4"]

DEFAULT_H = {
    "house": 8, "detached": 8, "semidetached_house": 8, "villa": 9, "bungalow": 6,
    "apartments": 21, "residential": 15, "dormitory": 16,
    "office": 36, "commercial": 18, "retail": 12, "hotel": 40, "public": 16,
    "industrial": 12, "warehouse": 10, "factory": 12,
    "garage": 3, "garages": 3, "carport": 3, "shed": 3, "kiosk": 3, "hut": 3, "service": 4,
    "church": 18, "temple": 12, "cathedral": 24, "chapel": 8,
    "train_station": 16, "transportation": 12, "hangar": 14,
    "tower": 48, "skyscraper": 120, "yes": 12,
}

ROAD_W = {
    "motorway": 22, "motorway_link": 10, "trunk": 18, "trunk_link": 8,
    "primary": 16, "primary_link": 7, "secondary": 12, "secondary_link": 6,
    "tertiary": 10, "tertiary_link": 6, "unclassified": 8, "residential": 8,
    "living_street": 7, "service": 5, "pedestrian": 8, "footway": 2.2, "path": 2,
    "cycleway": 3, "steps": 2.5, "track": 3.5, "corridor": 3,
}

ROAD_PAL = {
    "footway": "paving", "path": "paving", "pedestrian": "road_paving", "steps": "paving",
    "cycleway": "cycle", "track": "dirt", "living_street": "road_paving",
}


def building_height(tags, default=12):
    h = parse_len(tags.get("height"))
    roof = parse_len(tags.get("roof:height")) or 0
    if h is None:
        levels = parse_len(tags.get("building:levels") or tags.get("levels"))
        if levels is not None:
            h = levels * LEVEL_H + roof
    if h is None:
        typ = tags.get("building") or tags.get("building:part") or "yes"
        h = DEFAULT_H.get(typ, default)
    return float(max(2.0, min(h, 700.0)))


def min_height(tags):
    m = parse_len(tags.get("min_height"))
    if m is None:
        ml = parse_len(tags.get("building:min_level") or tags.get("min_level"))
        if ml is not None:
            m = ml * LEVEL_H
    return float(m or 0.0)


def wall_roof_ids(tags, oid):
    typ = tags.get("building") or tags.get("building:part") or "yes"
    col = parse_colour(tags.get("building:colour") or tags.get("colour"))
    mat_s = (tags.get("building:material") or tags.get("material") or "").lower()
    if col is None:
        if "glass" in mat_s or typ in ("skyscraper", "office", "commercial"):
            col = hex_rgb(GLASS_DEFAULTS[stable_hash(oid, "w") % len(GLASS_DEFAULTS)])
            wmat = M_GLASS
        else:
            col = hex_rgb(WALL_DEFAULTS[stable_hash(oid, "w") % len(WALL_DEFAULTS)])
            wmat = M_WALL
    else:
        wmat = M_GLASS if "glass" in mat_s or typ in ("skyscraper",) else M_WALL
        if typ in ("office", "commercial", "hotel") and "brick" not in mat_s:
            # many Lujiazui towers are glass even without a material tag
            if typ == "office" and tags.get("building:colour") is None:
                wmat = M_GLASS
    roof_c = parse_colour(tags.get("roof:colour") or tags.get("roof:color"))
    if roof_c is None:
        roof_c = tuple(max(0, int(v * 0.88)) for v in col)
        rmat = M_ROOF
    else:
        rmat = M_ROOF
    if "glass" in (tags.get("roof:material") or "") or wmat == M_GLASS:
        rmat = M_GLASS
    return pal(soften(col), wmat), pal(soften(roof_c), rmat)


def tunnelish(t):
    return t.get("tunnel") in ("yes", "building_passage", "culvert") \
        or (t.get("layer", "0").lstrip("-").isdigit() and int(t.get("layer", "0")) < 0) \
        or t.get("covered") == "yes" or t.get("location") == "underground"


def burn(shapes, out_shape, transform=Affine.identity(), dtype=np.uint16, all_touched=False, fill=0):
    shapes = [(s, int(v)) for s, v in shapes if s is not None and not getattr(s, "is_empty", False)]
    if not shapes:
        return np.zeros(out_shape, dtype)
    return rasterize(shapes, out_shape=out_shape, transform=transform, fill=fill,
                     all_touched=all_touched, dtype=dtype)


# ---------------------------------------------------------------- load
note("loading OSM…")
osm = OSM()
note("OSM", osm.timestamp, "areas", len(osm.areas), "lines", len(osm.lines), "nodes", len(osm.nodes))

z = np.load(ROOT / "data" / "dem_grid.npz")
ground_m = z["ground"].astype(np.float32)
water_mask = z["water"].astype(np.uint8)
water_z = float(z["water_z"]) if "water_z" in z.files else 1.0
note("DEM ground m", float(ground_m.min()), "…", float(ground_m.max()), "water_z", water_z)

import sculpt as _sculpt
buildings_m = z["buildings"] if "buildings" in z.files else None
ground_m, promenade_mask, wall_mask, _sc = _sculpt.apply(ground_m, water_mask, osm, buildings_m)
note("sculpt", "road cells lowered", _sc["road_cells"], "waterfront cols", _sc["waterfront_cols"])
np.savez_compressed(ROOT / "data" / "ground_fixed.npz",
                    ground=ground_m, water=water_mask, promenade=promenade_mask.astype(np.uint8),
                    wall=wall_mask.astype(np.uint8),
                    note="OSM road flatten + cited Bund flood wall; not LiDAR")

SLAB = 3
BASE = int(math.floor(float(ground_m.min()))) - SLAB
g = np.clip(np.round(ground_m - BASE), 1, 250).astype(np.int32)
note("BASE", BASE, "g", int(g.min()), "…", int(g.max()))

# ---------------------------------------------------------------- ground colours
surf = np.full((D, W), C["bare"], np.uint16)
# landuse / leisure
for oid, typ, t, geom in osm.areas:
    key = None
    lu = t.get("landuse") or t.get("leisure") or t.get("natural") or t.get("amenity")
    if lu in ("grass", "park", "garden", "village_green", "meadow", "recreation_ground",
              "cemetery", "pitch", "golf_course", "allotments"):
        key = "grass" if stable_hash(oid) % 2 == 0 else "grass2"
        if lu == "garden":
            key = "garden"
    elif lu in ("forest", "wood", "scrub"):
        key = "garden"
    elif lu in ("residential",):
        key = "paving2"
    elif lu in ("commercial", "retail", "industrial", "construction"):
        key = "concrete"
    elif lu in ("plaza",) or t.get("place") == "square" or t.get("highway") == "pedestrian":
        key = "paving"
    elif lu in ("sand", "beach"):
        key = "sand"
    if key:
        m = burn([(geom, 1)], (D, W), dtype=np.uint8, all_touched=True)
        surf[m == 1] = C[key]

# water overwrites
surf[water_mask == 1] = C["water"]
# slightly lower the river so banks read as banks
g = np.where(water_mask == 1, np.maximum(g - 1, 1), g)

# roads
road_shapes = []
bridge_shapes = []
for oid, t, line in osm.lines:
    hwy = t.get("highway")
    if not hwy or tunnelish(t):
        continue
    width = parse_len(t.get("width") or t.get("est_width"))
    if width is None:
        lanes = parse_len(t.get("lanes"))
        width = ROAD_W.get(hwy, 6)
        if lanes:
            width = max(width, lanes * 3.5)
    pal_name = ROAD_PAL.get(hwy, "asphalt")
    if t.get("surface") in ("paving_stones", "sett", "cobblestone"):
        pal_name = "sett"
    elif t.get("surface") in ("concrete",):
        pal_name = "concrete"
    elif t.get("surface") in ("dirt", "earth", "grass", "ground"):
        pal_name = "dirt"
    pid = C[pal_name]
    poly = line.buffer(max(1.0, width / 2.0), cap_style=2, join_style=2)
    if t.get("bridge") not in (None, "no"):
        if oid == _sculpt.WAIBAIDU_WAY or t.get("name") in ("外白渡桥", "Waibaidu Bridge"):
            continue  # schematic camelback truss in fill_waibaidu
        layer = parse_len(t.get("layer")) or 1
        deck = 6 + max(0, layer) * 4
        bridge_shapes.append((poly, pid, deck))
    else:
        road_shapes.append((poly, pid))

if road_shapes:
    # burn low roads first, later (narrower) last — just sequential
    for poly, pid in road_shapes:
        m = burn([(poly, 1)], (D, W), dtype=np.uint8, all_touched=True)
        # don't paint asphalt over the river (underwater tunnels already skipped; ferry approaches ok)
        surf[(m == 1) & (water_mask == 0)] = pid

# cited Bund promenade / quay face (overrides smeared DEM colours)
surf[promenade_mask] = C["promenade"]
surf[wall_mask] = C["quay"]

# railways
for oid, t, line in osm.lines:
    if t.get("railway") not in ("rail", "light_rail", "subway", "tram") or tunnelish(t):
        continue
    if t.get("railway") == "subway" and t.get("bridge") in (None, "no"):
        continue
    poly = line.buffer(2.2 if t.get("railway") == "tram" else 3.0)
    m = burn([(poly, 1)], (D, W), dtype=np.uint8, all_touched=True)
    surf[(m == 1) & (water_mask == 0)] = C["ballast"]

note("ground colours ready")

# ---------------------------------------------------------------- buildings
parents = []
parts = []
for oid, typ, t, geom in osm.areas:
    if t.get("location") == "underground":
        continue
    if "building:part" in t:
        parts.append((oid, t, geom, building_height(t), min_height(t), *wall_roof_ids(t, oid)))
    elif t.get("building") not in (None, "no"):
        parents.append((oid, t, geom, building_height(t), 0.0, *wall_roof_ids(t, oid)))
note("buildings", len(parents), "parts", len(parts))

# raster of "this cell has a part" and parent ids for zones
parent_ids = []
parent_geoms = []
for i, (oid, t, geom, *rest) in enumerate(parents):
    parent_ids.append(i + 1)
    parent_geoms.append(geom)
bld = burn(zip(parent_geoms, parent_ids), (D, W), dtype=np.uint16)
note("building columns", int((bld > 0).sum()))

# ---------------------------------------------------------------- trees & lamps
trees = [(p.x, p.y, t) for oid, t, p in osm.nodes if t.get("natural") == "tree"]
# some trees are mapped as areas — plant one at the centroid
for oid, typ, t, geom in osm.areas:
    if t.get("natural") == "tree":
        c = geom.centroid
        trees.append((c.x, c.y, t))
    elif t.get("natural") in ("wood", "forest") or t.get("landuse") == "forest":
        # scatter a few
        minx, miny, maxx, maxy = geom.bounds
        rng = np.random.default_rng(oid % (2**32))
        n = max(1, int(geom.area / 80))
        for _ in range(min(n, 40)):
            x = rng.uniform(minx, maxx)
            y = rng.uniform(miny, maxy)
            if geom.contains(Point(x, y)):
                trees.append((x, y, t))
lamps = []
for oid, t, p in osm.nodes:
    if t.get("highway") == "street_lamp" or t.get("amenity") == "street_lamp":
        i, j = int(round(p.x)), int(round(p.y))
        if 0 <= i < W and 0 <= j < D:
            lamps.append((i, j, int(g[j, i] + 5)))
note("trees", len(trees), "lamps", len(lamps))

# ---------------------------------------------------------------- helpers for per-tile fill
part_index = STRtree([p[2] for p in parts]) if parts else None
parent_index = STRtree([p[2] for p in parents]) if parents else None
bridge_index = STRtree([p[0] for p in bridge_shapes]) if bridge_shapes else None


def window_transform(c0, r0):
    return Affine.translation(c0, r0)


def ids_for(tree, items, env):
    if tree is None:
        return []
    hits = tree.query(env)
    out = []
    for h in np.atleast_1d(hits):
        out.append(int(h))
    return out


def pearl_center():
    # Prefer an OSM feature named 东方明珠; else the AOI anchor.
    for oid, typ, t, geom in osm.areas:
        n = " ".join(str(t.get(k, "")) for k in ("name", "name:zh", "name:en"))
        if "东方明珠" in n or "Oriental Pearl" in n:
            c = geom.centroid
            return float(c.x), float(c.y)
    for oid, t, p in osm.nodes:
        n = " ".join(str(t.get(k, "")) for k in ("name", "name:zh", "name:en"))
        if "东方明珠" in n or "Oriental Pearl" in n:
            return float(p.x), float(p.y)
    return float(I0), float(J0)


PEARL_I, PEARL_J = pearl_center()
note("Pearl grid", round(PEARL_I, 1), round(PEARL_J, 1))

# --- Jin Mao pagoda terraces (overrides.json; OSM shaft parts are too coarse)
OVERRIDES = json.loads((ROOT / "scripts" / "overrides.json").read_text())
JINMAO_SKIP_PARTS = {188377486, 376016310}  # replaced by terraces; keep podium + needle
JINMAO = None  # dict set below if the shaft part is on the map
_ov = OVERRIDES.get("376075961") or {}
for oid, typ, tgs, geom in osm.areas:
    if oid == _ov.get("shaft_part"):
        roof = float(_ov.get("roof_m", 385.5))
        tip = float(_ov.get("tip_m", 420.5))
        floors = int(_ov.get("floors", 88))
        segs = list(_ov.get("segments_floors") or [16, 14, 12, 10, 8, 7, 6, 5, 4, 3, 2, 1])
        # plan scale: office tiers shrink gently, hotel tiers (from floor 53) a bit faster
        # down to ~0.35 of the shaft, matching the published stepping-back pagoda
        # plan scales cited in overrides.json (base 16 floors at full shaft width, then 1/8 shrinks)
        scales = [1.00, 0.92, 0.84, 0.76, 0.68, 0.61, 0.55, 0.49, 0.44, 0.40, 0.36, 0.32]
        terraces = []
        f0 = 0
        for i, nf in enumerate(segs):
            f1 = f0 + nf
            z0 = 0.0 if f0 == 0 else roof * f0 / floors
            z1 = roof * f1 / floors
            s = scales[i] if i < len(scales) else scales[-1]
            terraces.append((z0, z1, s))
            f0 = f1
        wall = pal(soften((196, 140, 64)), M_BRONZE)
        roof_id = pal(soften((168, 112, 48)), M_ROOF)
        needle = pal(soften((180, 150, 90)), M_METAL)
        JINMAO = dict(shaft=geom, terraces=terraces, tip=tip, roof=roof,
                      wall=wall, roof_id=roof_id, needle=needle, centroid=geom.centroid)
        note("Jin Mao terraces", len(terraces), "roof", roof, "tip", tip, "sources", len(_ov.get("sources") or []))
        break


# Simplified published profile of the Oriental Pearl (Wikipedia / official height 468 m).
# Radii in metres, heights above ground. This is a schematic voxel sculpture, not LiDAR.
PEARL_H = 468
PEARL_SILVER = pal(soften((200, 208, 216)), M_METAL)
PEARL_GLASS = pal(soften((196, 168, 180)), M_GLASS)  # the spheres' familiar pink-silver
PEARL_SHAFT = pal(soften((220, 216, 208)), M_CONCRETE)
PEARL_BAND = pal(soften((180, 56, 56)), M_WALL)
PEARL_SPIRE = pal(soften((196, 196, 200)), M_METAL)


def pearl_radius(z_agl: float) -> tuple[float, int] | None:
    z = z_agl
    if z < 0 or z > PEARL_H:
        return None
    def sphere(zc, rmax, z):
        d = abs(z - zc)
        if d >= rmax:
            return 0.0
        return math.sqrt(rmax * rmax - d * d)
    if z < 8:
        return 36.0, PEARL_SHAFT
    if z < 68:
        return (16.0 if not (28 <= z <= 32 or 48 <= z <= 52) else 18.0,
                PEARL_BAND if (28 <= z <= 32 or 48 <= z <= 52) else PEARL_SHAFT)
    if z < 118:  # lower sphere, diameter 50 m, centre ~93 m
        r = sphere(93, 25, z)
        return (r, PEARL_GLASS) if r > 0.5 else (8.0, PEARL_SHAFT)
    if z < 230:
        return 8.0, PEARL_SHAFT
    if z < 276:  # upper sphere, diameter 45 m, centre ~252 m
        r = sphere(252, 22.5, z)
        return (r, PEARL_GLASS) if r > 0.5 else (6.0, PEARL_SHAFT)
    if z < 348:
        return 5.5, PEARL_SHAFT
    if z < 366:  # space capsule
        r = sphere(357, 8, z)
        return (r, PEARL_SILVER) if r > 0.5 else (3.0, PEARL_SPIRE)
    # spire to 468 m
    r = 3.0 + (1.0 - 3.0) * (z - 366) / (PEARL_H - 366)
    return r, PEARL_SPIRE


def fill_pearl(vol, gwin, c0, r0, rows, cols):
    """Sculpt the Pearl into this tile window (including apron)."""
    # only if the tile is near the Pearl
    if (c0 + cols < PEARL_I - 50) or (c0 > PEARL_I + 50) or (r0 + rows < PEARL_J - 50) or (r0 > PEARL_J + 50):
        return
    for rr in range(rows):
        gj = r0 + rr
        for cc in range(cols):
            gi = c0 + cc
            di, dj = gi + 0.5 - PEARL_I, gj + 0.5 - PEARL_J
            dist = math.hypot(di, dj)
            if dist > 42:
                continue
            gz = int(gwin[rr, cc]) if 0 <= rr < gwin.shape[0] and 0 <= cc < gwin.shape[1] else 10
            # clear any extruded box in this column up to the spire
            top = min(vol.shape[0], gz + PEARL_H + 1)
            if dist <= 38:
                vol[gz:top, rr, cc] = 0
            z = gz
            while z < top:
                info = pearl_radius(z - gz)
                if info is None:
                    break
                rad, pid = info
                if dist <= rad + 0.15:
                    vol[z, rr, cc] = pid
                z += 1


def fill_jinmao(vol, gwin, c0, r0, rows, cols):
    """Replace the coarse OSM shaft with published pagoda terraces (see overrides.json)."""
    if not JINMAO:
        return
    shaft, c = JINMAO["shaft"], JINMAO["centroid"]
    env = box(c0 - 1, r0 - 1, c0 + cols + 1, r0 + rows + 1)
    if not shaft.buffer(8).intersects(env):
        return
    scaled = []
    for z0, z1, s in JINMAO["terraces"]:
        pg = shp_scale(shaft, xfact=s, yfact=s, origin=c)
        scaled.append((z0, z1, pg))
    # raster each terrace into this window
    tr = window_transform(c0, r0)
    # cells that belong to the (unscaled) shaft: clear above the podium, then fill terraces
    base_m = burn([(shaft, 1)], (rows, cols), tr, dtype=np.uint8, all_touched=True) == 1
    if not base_m.any():
        return
    wid, rid, nid = JINMAO["wall"], JINMAO["roof_id"], JINMAO["needle"]
    tip, roof = JINMAO["tip"], JINMAO["roof"]
    rs, cs = np.nonzero(base_m)
    # precompute terrace masks
    masks = [burn([(pg, 1)], (rows, cols), tr, dtype=np.uint8, all_touched=True) == 1 for *_, pg in scaled]
    for rr, cc in zip(rs, cs):
        gz = int(gwin[rr, cc])
        # leave the 8–10 m OSM podium bits; clear the old shaft above ~12 m
        zclear0 = gz + 12
        zclear1 = min(vol.shape[0], gz + int(tip) + 2)
        vol[zclear0:zclear1, rr, cc] = 0
        last_top = zclear0
        for (z0, z1, _pg), m in zip(scaled, masks):
            if not m[rr, cc]:
                continue
            a = gz + int(round(z0))
            b = gz + int(round(z1))
            a = max(a, zclear0)
            if b <= a:
                continue
            vol[a:b, rr, cc] = wid
            vol[b - 1, rr, cc] = rid
            last_top = b
        # needle / spire at the centroid cluster (inside the smallest terrace)
        if masks[-1][rr, cc]:
            # thinner needle: only the inner 30% of the last terrace
            last_pg = scaled[-1][2]
            # approximate: distance to centroid
            gi, gj = c0 + cc + 0.5, r0 + rr + 0.5
            if (gi - c.x) ** 2 + (gj - c.y) ** 2 <= (6.0 ** 2):
                a = gz + int(round(roof))
                b = min(vol.shape[0] - 1, gz + int(round(tip)))
                if b > a:
                    vol[a:b, rr, cc] = nid
                    vol[b - 1, rr, cc] = nid


def fill_trees(vol, gwin, c0, r0, rows, cols, fol_only=False):

    # trees whose (x,y) fall in this window (with 6 m margin)
    for x, y, t in trees:
        if not (c0 - 6 <= x < c0 + cols + 6 and r0 - 6 <= y < r0 + rows + 6):
            continue
        h = parse_len(t.get("height")) or (6 + stable_hash(int(x), int(y)) % 6)
        h = int(max(4, min(h, 22)))
        cr = max(1, int(round((parse_len(t.get("diameter_crown")) or h * 0.45) / 2)))
        cr = min(cr, 5)
        ci, rj = int(round(x - c0)), int(round(y - r0))
        if not (0 <= ci < cols and 0 <= rj < rows):
            # crown may still overlap
            pass
        gz = int(gwin[rj, ci]) if 0 <= rj < rows and 0 <= ci < cols else None
        if gz is None:
            continue
        leaf = LEAVES[stable_hash(int(x), int(y), "l") % len(LEAVES)]
        # trunk
        if 0 <= ci < cols and 0 <= rj < rows and not fol_only:
            vol[gz:gz + max(1, h - cr), rj, ci] = C["trunk"]
        # crown
        cz = gz + h - cr
        for dj in range(-cr, cr + 1):
            for di in range(-cr, cr + 1):
                if di * di + dj * dj > cr * cr + 1:
                    continue
                cc, rr = ci + di, rj + dj
                if 0 <= cc < cols and 0 <= rr < rows:
                    z0 = cz + (0 if di * di + dj * dj < (cr - 1) ** 2 else 1)
                    z1 = cz + cr
                    vol[z0:z1, rr, cc] = leaf


def fill_buildings(vol, gwin, c0, r0, rows, cols):
    env = box(c0 - 1, r0 - 1, c0 + cols + 1, r0 + rows + 1)
    tr = window_transform(c0, r0)
    # parents
    has_part = np.zeros((rows, cols), bool)
    if parts:
        pids = ids_for(part_index, parts, env)
        if pids:
            has_part = burn([(parts[i][2], 1) for i in pids], (rows, cols), tr, dtype=np.uint8, all_touched=True) == 1
    if parents:
        bids = ids_for(parent_index, parents, env)
        # draw large first so small courtyards overwrite
        bids.sort(key=lambda i: -parents[i][2].area)
        for i in bids:
            oid, t, geom, h, z0, wid, rid = parents[i]
            m = burn([(geom, 1)], (rows, cols), tr, dtype=np.uint8, all_touched=True) == 1
            m &= ~has_part
            if not m.any():
                continue
            rs, cs = np.nonzero(m)
            for rr, cc in zip(rs, cs):
                gz = int(gwin[rr, cc]) + int(round(z0))
                top = min(int(gwin[rr, cc] + h), vol.shape[0] - 1)
                if top <= gz:
                    continue
                vol[gz:top, rr, cc] = wid
                vol[top - 1, rr, cc] = rid
    if parts:
        pids = ids_for(part_index, parts, env)
        pids.sort(key=lambda i: (parts[i][4], parts[i][3]))  # min_h, then height
        for i in pids:
            oid, t, geom, h, z0, wid, rid = parts[i]
            if oid in JINMAO_SKIP_PARTS:
                continue
            m = burn([(geom, 1)], (rows, cols), tr, dtype=np.uint8, all_touched=True) == 1
            if not m.any():
                continue
            rs, cs = np.nonzero(m)
            for rr, cc in zip(rs, cs):
                gz = int(gwin[rr, cc]) + int(round(z0))
                top = min(int(gwin[rr, cc] + h), vol.shape[0] - 1)
                if top <= gz:
                    continue
                vol[gz:top, rr, cc] = wid
                vol[top - 1, rr, cc] = rid
    # bridges (decks above ground)
    if bridge_shapes:
        bids = ids_for(bridge_index, bridge_shapes, env)
        for i in bids:
            poly, pid, deck = bridge_shapes[i]
            m = burn([(poly, 1)], (rows, cols), tr, dtype=np.uint8, all_touched=True) == 1
            rs, cs = np.nonzero(m)
            for rr, cc in zip(rs, cs):
                z0 = int(gwin[rr, cc] + deck)
                if 1 <= z0 < vol.shape[0] - 1:
                    vol[z0:z0 + 1, rr, cc] = pid
                    # thin kerb edges skipped


def fill_ground(vol, gwin, swin, rows, cols):
    # soil up to g-1, surface at g-1 (visible top of the ground run)
    for rr in range(rows):
        gz = gwin[rr]
        su = swin[rr]
        for cc in range(cols):
            z = int(gz[cc])
            sid = int(su[cc])
            if z <= 1:
                vol[0, rr, cc] = sid
            else:
                vol[0:z - 1, rr, cc] = C["soil"] if sid != C["water"] else C["water2"]
                vol[z - 1, rr, cc] = sid


# Vectorised ground fill
def fill_ground_fast(vol, gwin, swin):
    rows, cols = gwin.shape
    # column-wise: use a broadcast z index
    zz = np.arange(vol.shape[0], dtype=np.int32)[:, None, None]
    g3 = gwin[None, :, :]
    soil_m = (zz < g3 - 1) & (g3 > 1)
    top_m = (zz == g3 - 1) | ((g3 <= 1) & (zz == 0))
    water_top = (swin == C["water"])
    vol[soil_m] = C["soil"]
    # water body fill
    if water_top.any():
        wsoil = soil_m & water_top[None, :, :]
        vol[wsoil] = C["water2"]
    # place surface colours (including water)
    # loop only surface row — still D*W per tile = 130^2, fine; use put
    rs, cs = np.indices((rows, cols))
    tops = np.clip(gwin - 1, 0, vol.shape[0] - 1)
    vol[tops, rs, cs] = swin


# ---------------------------------------------------------------- tile export
N = T.N
nx, ny = -(-W // N), -(-D // N)
H8 = -(-HMAX // 8)
W8, D8 = -(-W // 8), -(-D // 8)
vol8 = np.zeros((H8, D8, W8), np.uint16)
g8 = np.clip(g[::8, ::8], 0, 255).astype(np.uint8)
if g8.shape[0] < D8 or g8.shape[1] < W8:
    tmp = np.zeros((D8, W8), np.uint8)
    tmp[:g8.shape[0], :g8.shape[1]] = g8
    g8 = tmp

fol_ids = set(LEAVES) | {C["trunk"]}
grass_ids = {C["grass"], C["grass2"], C["garden"]}
bare_id = C["bare"]

(OUT / "tiles" / "1").mkdir(parents=True, exist_ok=True)
(OUT / "tiles" / "1f").mkdir(parents=True, exist_ok=True)
(OUT / "tiles" / "8").mkdir(parents=True, exist_ok=True)

nruns = 0
bytes1 = 0
bytesf = 0
for ty in range(ny):
    for tx in range(nx):
        r0, c0 = ty * N - 1, tx * N - 1  # apron origin
        rows = cols = N + 2
        # clip source
        gwin = np.zeros((rows, cols), np.int32)
        swin = np.full((rows, cols), C["bare"], np.uint16)
        rr0, rr1 = max(r0, 0), min(r0 + rows, D)
        cc0, cc1 = max(c0, 0), min(c0 + cols, W)
        gwin[rr0 - r0:rr1 - r0, cc0 - c0:cc1 - c0] = g[rr0:rr1, cc0:cc1]
        swin[rr0 - r0:rr1 - r0, cc0 - c0:cc1 - c0] = surf[rr0:rr1, cc0:cc1]
        vol = np.zeros((HMAX, rows, cols), np.uint16)
        fill_ground_fast(vol, gwin, swin)
        fill_buildings(vol, gwin, c0, r0, rows, cols)
        fill_jinmao(vol, gwin, c0, r0, rows, cols)
        fill_pearl(vol, gwin, c0, r0, rows, cols)
        _sculpt.fill_waibaidu(vol, gwin, c0, r0, rows, cols, BASE,
                              C["iron"], C["deck"], C["quay"], osm)
        # copy core into 8 m volume before foliage split? Include trees in 8 m as clay.
        fill_trees(vol, gwin, c0, r0, rows, cols)
        # downsample the core (no apron) into vol8
        core = vol[:, 1:1 + N, 1:1 + N]
        # last tiles may hang past W/D: already zero-padded
        # pad height to multiple of 8
        Hs = HMAX
        ds = T.downsample(core, 8)
        r8, c8 = ty * (N // 8), tx * (N // 8)
        h8, d8, w8 = ds.shape
        vol8[:h8, r8:r8 + d8, c8:c8 + w8] = ds
        # split foliage
        is_fol = np.zeros(65536, bool)
        is_fol[list(fol_ids)] = True
        is_fol[list(grass_ids)] = True
        is_grass = np.zeros(65536, bool)
        is_grass[list(grass_ids)] = True
        top = int(np.nonzero(vol.reshape(Hs, -1).any(1))[0].max()) + 1 if vol.any() else 1
        win = vol[:top]
        f = is_fol[win]
        fol = np.where(f, win, 0).astype(np.uint16)
        win = np.where(f, np.where(is_grass[win], bare_id, 0), win).astype(np.uint16)
        gw = np.clip(gwin, 0, 255).astype(np.uint8)
        fc, fr = T.column_runs(fol)
        with gzip.GzipFile(OUT / "tiles" / "1f" / f"{tx}_{ty}.bin", "wb", compresslevel=9, mtime=0) as fh:
            fh.write(fc.tobytes() + gw.tobytes() + fr.tobytes())
        bytesf += (OUT / "tiles" / "1f" / f"{tx}_{ty}.bin").stat().st_size
        counts, runs = T.column_runs(win)
        with gzip.GzipFile(OUT / "tiles" / "1" / f"{tx}_{ty}.bin", "wb", compresslevel=9, mtime=0) as fh:
            fh.write(counts.tobytes() + gw.tobytes() + runs.tobytes())
        bytes1 += (OUT / "tiles" / "1" / f"{tx}_{ty}.bin").stat().st_size
        nruns += len(runs)
        del vol, win, fol, core, ds
    note(f"  1 m row {ty + 1}/{ny}")

note(f"tiles level 1: {nx}x{ny} nodes, {nruns} runs, {bytes1 / 2**20:.1f} MB gz, foliage {bytesf / 2**20:.1f} MB")

# level 8
# trim empty height
top8 = int(np.nonzero(vol8.reshape(H8, -1).any(1))[0].max()) + 1 if vol8.any() else 1
vol8 = vol8[:top8]
nx8, ny8 = -(-W8 // N), -(-D8 // N)
n8 = 0
bytes8 = 0
for ty in range(ny8):
    for tx in range(nx8):
        r0, c0 = ty * N - 1, tx * N - 1
        rows = cols = N + 2
        win = np.zeros((vol8.shape[0], rows, cols), np.uint16)
        gw = np.zeros((rows, cols), np.uint8)
        rr0, rr1 = max(r0, 0), min(r0 + rows, D8)
        cc0, cc1 = max(c0, 0), min(c0 + cols, W8)
        win[:, rr0 - r0:rr1 - r0, cc0 - c0:cc1 - c0] = vol8[:, rr0:rr1, cc0:cc1]
        gw[rr0 - r0:rr1 - r0, cc0 - c0:cc1 - c0] = g8[rr0:rr1, cc0:cc1]
        top = int(np.nonzero(win.reshape(win.shape[0], -1).any(1))[0].max()) + 1 if win.any() else 1
        counts, runs = T.column_runs(win[:top])
        with gzip.GzipFile(OUT / "tiles" / "8" / f"{tx}_{ty}.bin", "wb", compresslevel=9, mtime=0) as fh:
            fh.write(counts.tobytes() + gw.tobytes() + runs.tobytes())
        bytes8 += (OUT / "tiles" / "8" / f"{tx}_{ty}.bin").stat().st_size
        n8 += len(runs)
note(f"tiles level 8: {nx8}x{ny8} nodes, {n8} runs, {bytes8 / 2**20:.1f} MB gz")
del vol8

# ---------------------------------------------------------------- zones
note("zones…")
# separators: major roads + water
ROADS = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
         "living_street", "motorway_link", "trunk_link", "primary_link", "secondary_link",
         "tertiary_link", "pedestrian"}
sep_shapes = []
for oid, t, line in osm.lines:
    if (t.get("highway") in ROADS or t.get("railway") in ("rail", "tram", "light_rail")) and not tunnelish(t) and t.get("bridge") is None:
        sep_shapes.append(line.buffer(1.5))
sep_shapes.extend([g for oid, typ, t, g in osm.areas if t.get("natural") == "water" or t.get("waterway") == "riverbank"])
sep = burn([(s, 1) for s in sep_shapes], (D, W), dtype=np.uint8, all_touched=True) > 0
sep &= bld == 0
lab, nblk = ndimage.label(~sep)
_, (ir, ic) = ndimage.distance_transform_edt(lab == 0, return_indices=True)
blk = lab[ir, ic].astype(np.int64)
area = np.bincount(blk.ravel(), minlength=nblk + 1)
rr, cc = np.mgrid[0:D, 0:W]
big = area[blk] > 120_000
blk = np.where(big, (nblk + 1) + (rr // 256) * (W // 256 + 1) + (cc // 256) + blk * 10_000, blk)
_, blk = np.unique(blk, return_inverse=True)
blk = blk.reshape(D, W) + 1
area = np.bincount(blk.ravel())
small = area[blk] < 400
if small.any():
    _, (ir, ic) = ndimage.distance_transform_edt(small, return_indices=True)
    blk = blk[ir, ic]
# a building lies whole in one block
idx = np.flatnonzero(bld.ravel())
if idx.size:
    b, k = bld.ravel()[idx], blk.ravel()[idx]
    pairs, counts = np.unique(np.stack([b, k], 1), axis=0, return_counts=True)
    best = {}
    for (bi, ki), cnt in zip(pairs, counts):
        if cnt > best.get(bi, (0, 0))[1]:
            best[int(bi)] = (int(ki), int(cnt))
    lut = np.zeros(int(bld.max()) + 1, np.int64)
    for bi, (ki, _) in best.items():
        lut[bi] = ki
    blk = np.where(bld > 0, lut[bld], blk)
_, blk = np.unique(blk, return_inverse=True)
blk = (blk.reshape(D, W) + 1).astype(np.uint16)
nb = int(blk.max())
note("blocks", nb)


def stats(lab, n):
    idx = np.arange(1, n + 1)
    cnt = ndimage.sum_labels(np.ones_like(lab, np.float32), lab, idx)
    cy = ndimage.sum_labels(rr.astype(np.float32), lab, idx) / np.maximum(cnt, 1)
    cx = ndimage.sum_labels(cc.astype(np.float32), lab, idx) / np.maximum(cnt, 1)
    sl = ndimage.find_objects(lab, n)
    out = []
    for i in range(n):
        s = sl[i]
        n_col = int(cnt[i])
        # triangle stand-in: ~8 tris per occupied column (viewer only uses this for the budget)
        tris = max(200, n_col * 8)
        box = [s[1].start, s[0].start, s[1].stop, s[0].stop] if s else [0, 0, 0, 0]
        out.append([round(float(cx[i]), 1), round(float(cy[i]), 1), n_col, *box, tris])
    return out


zones = {"blocks": stats(blk, nb), "buildings": stats(bld.astype(np.int32), int(bld.max()))}
with gzip.GzipFile(OUT / "zones.bin", "wb", compresslevel=9, mtime=0) as fh:
    fh.write(blk.tobytes() + bld.astype(np.uint16).tobytes())
json.dump(zones, open(OUT / "zones.json", "w"))
note("zones.bin", (OUT / "zones.bin").stat().st_size // 1024, "KB, buildings", len(zones["buildings"]))

# ---------------------------------------------------------------- terrain.bin
STEP = 4
hf = ndimage.uniform_filter(g.astype(np.float32), STEP)
h = np.round(hf[::STEP, ::STEP] * 10).astype(np.uint16)  # decimetres above BASE? viewer uses metres*10 of model z
# ground z in the model is `g` (already minus BASE). Yes.
# colours every 2 m, centre of each 2 m cell
# grass as bare earth (like Warsaw)
rgb_lut = np.array([e[:3] for e in pal.entries] + [(0, 0, 0)] * (65536 - len(pal.entries)), np.uint8)
surf_vis = surf.copy()
for gid in grass_ids:
    surf_vis[surf == gid] = bare_id
rgb = rgb_lut[surf_vis]
c = np.ascontiguousarray(rgb[1::2, 1::2])
with gzip.GzipFile(OUT / "terrain.bin", "wb", compresslevel=9, mtime=0) as fh:
    fh.write(h.tobytes())
    fh.write(c.tobytes())
note("terrain", h.shape, c.shape, (OUT / "terrain.bin").stat().st_size // 1024, "KB")

# ---------------------------------------------------------------- labels & views
def name_of(t):
    return t.get("name:zh") or t.get("name") or t.get("name:zh-Hans") or t.get("name:en") or ""


def en_of(t):
    return t.get("name:en") or ""


CURATED = [
    # substrings in name/name:zh/name:en, then (en, kind, height, year, site, far)
    ("东方明珠", "Oriental Pearl Tower", "tower", 468, 1994, "https://www.opg.cn/", 9000),
    ("上海中心大厦", "Shanghai Tower", "tower", 632, 2015, "https://www.shanghaitower.com/", 9000),
    ("上海中心", "Shanghai Tower", "tower", 632, 2015, "https://www.shanghaitower.com/", 9000),
    ("金茂大厦", "Jin Mao Tower", "tower", 421, 1999, None, 8000),
    ("金茂", "Jin Mao Tower", "tower", 421, 1999, None, 8000),
    ("环球金融中心", "Shanghai World Financial Center", "tower", 492, 2008, None, 8000),
    ("上海国际会议中心", "Shanghai International Convention Center", "building", 87, 1999, None, 4000),
    ("上海海关", "Customs House", "building", 90, 1927, None, 4000),
    ("和平饭店", "Peace Hotel", "hotel", 77, 1929, None, 3500),
    ("汇丰银行", "HSBC Building", "building", 47, 1923, None, 3000),
    ("外白渡桥", "Waibaidu Bridge", "bridge", None, 1907, None, 3000),
    ("上海美术馆", "China Art Museum / former?", "museum", None, None, None, 2000),
    ("陈毅", "Chen Yi statue", "monument", None, 1993, None, 1500),
    ("陆家嘴中心绿地", "Lujiazui Central Green Space", "park", None, 1997, None, 2500),
    ("滨江道", "Binjiang Avenue", "promenade", None, None, None, 2000),
]


def match_curated(t):
    n = " ".join(str(t.get(k, "")) for k in ("name", "name:zh", "name:en", "name:zh-Hans", "alt_name"))
    for key, en, kind, height, year, site, far in CURATED:
        if key in n:
            return dict(en=en, kind=kind, height=height, year=year, site=site, far=far)
    return None


labels = []
seen_keys = set()
# areas first (buildings), then nodes
for oid, typ, t, geom in list(osm.areas) + []:
    info = match_curated(t)
    if not info:
        continue
    c = geom.centroid
    x, y = int(round(c.x)), int(round(c.y))
    if not (0 <= x < W and 0 <= y < D):
        continue
    key = info["en"]
    if key in seen_keys:
        continue
    seen_keys.add(key)
    ztop = int(g[y, x] + (info["height"] or 20))
    labels.append(dict(
        name=name_of(t) or info["en"], en=info["en"], kind=info["kind"],
        height=info["height"] or None, year=info["year"] or None,
        x=x, y=y, z=ztop, g=int(g[y, x]),
        site=info["site"] or None, far=info["far"],
        alt=f"{info['en']} {name_of(t)}",
    ))
for oid, t, p in osm.nodes:
    info = match_curated(t)
    if not info:
        continue
    x, y = int(round(p.x)), int(round(p.y))
    if not (0 <= x < W and 0 <= y < D):
        continue
    key = info["en"]
    if key in seen_keys:
        continue
    seen_keys.add(key)
    labels.append(dict(
        name=name_of(t) or info["en"], en=info["en"], kind=info["kind"],
        height=info["height"] or None, year=info["year"] or None,
        x=x, y=y, z=int(g[y, x] + (info["height"] or 8)), g=int(g[y, x]),
        site=info["site"] or None, far=info["far"],
        alt=f"{info['en']} {name_of(t)}",
    ))
# always have a Pearl label at the sculpted centre
if "Oriental Pearl Tower" not in seen_keys:
    x, y = int(round(PEARL_I)), int(round(PEARL_J))
    labels.append(dict(name="东方明珠", en="Oriental Pearl Tower", kind="tower", height=468, year=1994,
                       x=x, y=y, z=int(g[y, x] + 468), g=int(g[y, x]),
                       site="https://www.opg.cn/", far=9000, alt="Oriental Pearl Tower 东方明珠"))
note("labels", len(labels), [l["name"] for l in labels])


def find_label(*keys):
    for l in labels:
        blob = (l.get("name") or "") + (l.get("en") or "")
        if any(k in blob for k in keys):
            return l
    return None


pearl = find_label("东方明珠", "Oriental Pearl") or dict(x=int(PEARL_I), y=int(PEARL_J), z=int(g[int(PEARL_J), int(PEARL_I)] + 200), g=10)
tower = find_label("上海中心")
jinmao = find_label("金茂")
swfc = find_label("环球")
customs = find_label("海关", "Customs")

# Camera: three.js is (x, z-up, y-south=r)
def view(label, target, pos):
    return dict(label=label, target=list(target), pos=list(pos))


# Overview: from Puxi / above the Bund, looking toward Lujiazui (Pearl + towers)
# +j is Pudong inland, so smaller j is Puxi. Camera at small j, looking at Pearl.
px, py = pearl["x"], pearl["y"]
views = {
    "overview": view("Overview", [px, 30, py], [px + 627, 520, 200]),
    "pearl": view("Oriental Pearl", [px, 80, py], [px + 420, 220, py + 380]),
    "lujiazui": view("Lujiazui", [px + 200, 40, py + 200], [px - 600, 280, py - 200]),
    "bund": view("The Bund", [px, 60, py], [1650, 55, 480]),
    "skyline": view("Bund skyline", [1100, 80, 1600], [1500, 45, 420]),
    "river": view("Huangpu River", [px - 200, 10, py], [px - 100, 220, py - 900]),
}
if tower:
    views["shanghai_tower"] = view("Shanghai Tower", [tower["x"], 120, tower["y"]],
                                   [tower["x"] - 350, 280, tower["y"] + 450])
# Waibaidu, East Nanjing Road, SWFC aperture, Jin Mao terraces (labels.py may refine)
wb = find_label("外白渡桥", "Waibaidu")
if wb:
    views["waibaidu"] = view("Waibaidu Bridge", [wb["x"], 16, wb["y"]],
                             [wb["x"] - 80, 28, max(40, wb["y"] - 200)])
peace = find_label("和平饭店", "Peace Hotel")
if peace:
    views["nanjing"] = view("East Nanjing Road", [peace["x"] - 10, 16, peace["y"] + 30],
                            [peace["x"] - 10, 18, max(40, peace["y"] - 120)])
    views["bund_street"] = view("Bund street", [peace["x"] - 80, 14, peace["y"] + 80],
                                [peace["x"] - 280, 16, peace["y"] + 90])
sw = find_label("环球金融", "World Financial")
if sw:
    views["swfc_aperture"] = view("SWFC aperture", [sw["x"], 465, sw["y"]],
                                  [sw["x"] - 340, 470, sw["y"]])
jm = find_label("金茂", "Jin Mao")
if jm:
    views["jinmao"] = view("Jin Mao Tower", [jm["x"], 200, jm["y"]],
                           [jm["x"] - 160, 180, jm["y"] - 200])
# Clamp cameras loosely into a padded box (the viewer also keeps the focus inside)
for v in views.values():
    v["pos"][0] = float(np.clip(v["pos"][0], -800, W + 800))
    v["pos"][2] = float(np.clip(v["pos"][2], -800, D + 800))
    v["target"][0] = float(np.clip(v["target"][0], 0, W - 1))
    v["target"][2] = float(np.clip(v["target"][2], 0, D - 1))

Hm = int(g.max() + 640)
meta = dict(
    W=W, D=D, H=Hm, base=BASE,
    tiles=dict(N=N, levels=[dict(s=1, nx=nx, ny=ny), dict(s=8, nx=nx8, ny=ny8)],
               bytes=bytes1 + bytes8 + bytesf),
    center=[CX, CY], theta_deg=THETA_DEG, crs=CRS,
    global_i0=GLOBAL_I0 - I0, global_j0=GLOBAL_J0 - J0,
    global_i_pearl=GLOBAL_I0, global_j_pearl=GLOBAL_J0,
    trams=[], jets=[],
    palette=[list(e) for e in pal.entries],
    lamps=lamps,
    floods=[],
    labels=labels,
    views=views,
    sources=dict(
        osm=f"OpenStreetMap, extract timestamp {osm.timestamp}; © OpenStreetMap contributors, ODbL",
        dem="Copernicus GLO-30 (30 m DSM), resampled and building-masked to approximate bare earth. Not LiDAR, not a 0.5 m DTM.",
        waterfront="Bund flood wall / promenade at 6.9 m Wusong (Shanghai Water Authority 2024; 1989–1993 Bund rebuild). Street ~3.2 m from the 1997 tide report. Not LiDAR.",
        waibaidu="Schematic camelback truss from published span/width/height (zh/en Wikipedia); OSM centreline for placement.",
        note="Shanghai 2025 government aerial / 0.1 m ortho / 0.5 m DSM is not publicly downloadable and was not used.",
    ),
)
json.dump(meta, open(OUT / "meta.json", "w"))
note("meta", "palette", len(pal.entries), "H", Hm, "lamps", len(lamps))

# top-down preview (debug + a simple RGB for README)
rgb_img = rgb_lut[surf_vis]
# paint roofs: top of buildings from bld + g + height is heavy; just darken building footprints
roof_col = np.array([160, 150, 140], np.uint8)
rgb_img = np.where((bld > 0)[:, :, None], (rgb_img * 0.4 + roof_col * 0.6).astype(np.uint8), rgb_img)
# mark pearl
pi, pj = int(round(PEARL_I)), int(round(PEARL_J))
rgb_img[max(0, pj - 3):pj + 4, max(0, pi - 3):pi + 4] = (220, 80, 120)
Image.fromarray(rgb_img[::2, ::2]).save(DBG / "topdown.jpg", quality=85)
note("topdown", DBG / "topdown.jpg")
json.dump(report, open(DBG / "report.json", "w"), ensure_ascii=False, indent=2)

# refresh labels + camera presets from Wikidata / pageviews when notable.json is present
try:
    if (ROOT / "data" / "raw" / "notable.json").exists():
        import labels as _labels
        _labels.apply_to_meta(meta, g)
        note("labels refreshed from Wikidata")
except Exception as exc:
    note("labels.py skipped", type(exc).__name__, exc)

note("done")
