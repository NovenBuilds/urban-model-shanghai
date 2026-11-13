"""Band W (worker-W) copy of voxelize_west.py: x=-2048..10240, y=-11264..-7168 on the same citywide grid.

Changes vs voxelize_west.py (output logic per cell unchanged):
  * writes ONLY tiles/1, tiles/1f, tiles/8 inside band W; refuses anything else
  * never writes meta.json, data/ground_all.npz, data/debug; caches/notes go to work/W/
  * reads ground_all.npz read-only for the 1-cell +y apron at y=-7168
  * palette is FROZEN to meta.json (unknown colours snap to nearest same-material entry) so parallel
    bands cannot collide on new palette ids
  * windowed / batched rasterize (identical pixels) for speed
  * pre-existing 1f tiles in the band (crown spill from the y>=-7168 band) are union-merged, not lost

Original docstring:
Voxelize a west extension toward People's Square, without rebuilding the first window.

Coverage is the same citywide grid (same theta, Pearl origin, local (0,0) of the
Bund–Lujiazui window). This script only writes tiles with ty < 0 (and the 8 m
row that covers y=-2048..0). Existing tiles/1/0_0.bin … 31_23.bin are left alone.

Ground is Copernicus GLO-30, flattened on OSM streets — not LiDAR.
Palette ids are reused from the first window's meta.json so colours stay consistent.
BASE is taken from meta.json so voxel z lines up at the y=0 seam.
"""
from __future__ import annotations

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
from shapely.geometry import Point, box
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from aoi import CRS, CX, CY, GLOBAL_I0, GLOBAL_J0, I0, J0, THETA_DEG, W as WIN_W, D as WIN_D, RAW, reload, to_world  # noqa: E402
from osm import OSM  # noqa: E402
import tiles as T  # noqa: E402
import sculpt as _sculpt  # noqa: E402

reload()
from aoi import W as WIN_W, D as WIN_D, to_world  # noqa: E402

OUT = ROOT / "web" / "public" / "data"
import os as _os
TILE_OUT = Path(_os.environ["BAND_W_TILE_OUT"]) if _os.environ.get("BAND_W_TILE_OUT") else OUT
DBG = ROOT / "data" / "debug"
META_PATH = OUT / "meta.json"
DEM_TIF = ROOT / RAW / "Copernicus_DSM_COG_10_N31_00_E121_00_DEM.tif"

# West block: same x as the first window, y extending inland (smaller j) by 2048 m
# so 8 m clay tiles stay aligned (1024 m) and Park Hotel / the museum fit with margin.
X0, X1 = -2048, 10240
Y0, Y1 = -11264, -7168
BAND = dict(x0=-2048, x1=10240, y0=-11264, y1=-7168)
# bands owned by worker-W (see PARALLEL.md); --band selects bounds + work dir
BANDS = {
    "W": dict(x0=-2048, x1=10240, y0=-11264, y1=-7168),
    "W2": dict(x0=-2048, x1=10240, y0=-15360, y1=-11264),
}
OPTS = {}
WORK = ROOT / "work" / "W"  # half-open; seam with the first window at y=0
HMAX = 400  # no 600 m towers in this block; Tomorrow Square is ~285 m
N = T.N  # 128
BASE_FALLBACK = -9
OVERRIDES = json.loads((ROOT / "scripts" / "overrides.json").read_text())


def height_override(oid):
    e = OVERRIDES.get(str(oid))
    if isinstance(e, dict) and e.get("height_m") is not None:
        return float(e["height_m"])
    return None



def note(*a):
    print(" ".join(str(x) for x in a), flush=True)


# ---- palette (must reuse first-window ids) ---------------------------------
M_SOLID, M_WALL, M_GLASS, M_LEAF, M_WATER, M_LAMP, M_METAL, M_PKIN, M_GRASS, M_MARK, M_SOIL, M_CAR, M_ROOF, M_CONCRETE, M_CLOCK, M_BRONZE, M_SIGN = range(17)


SNAPPED = []


class Palette:
    def __init__(self, entries=None):
        self.entries = [tuple(e) for e in (entries or [(0, 0, 0, 0)])]
        self.index = {tuple(e): i for i, e in enumerate(self.entries)}

    def __call__(self, rgb, mat=M_SOLID, q=4):
        if isinstance(rgb, str):
            rgb = hex_rgb(rgb)
        r, g, b = (min(255, int(round(v / q)) * q) for v in rgb)
        key = (r, g, b, mat)
        if key not in self.index:
            # frozen: snap to nearest existing entry with the same material (parallel-band safe)
            best, bd = None, 1e18
            for i, e in enumerate(self.entries):
                if i == 0 or e[3] != mat:
                    continue
                d = (e[0] - r) ** 2 + (e[1] - g) ** 2 + (e[2] - b) ** 2
                if d < bd:
                    best, bd = i, d
            if best is None:
                for i, e in enumerate(self.entries[1:], 1):
                    d = (e[0] - r) ** 2 + (e[1] - g) ** 2 + (e[2] - b) ** 2
                    if d < bd:
                        best, bd = i, d
            self.index[key] = best
            SNAPPED.append((key, self.entries[best], bd))
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
        import webcolors, colorsys
        rgb = hex_rgb(s) if re.fullmatch(r"#?[0-9a-f]{6}|#?[0-9a-f]{3}", s) else hex_rgb(webcolors.name_to_hex(s))
    except ValueError:
        return None
    import colorsys
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


def burn(shapes, out_shape, transform=Affine.identity(), dtype=np.uint16, all_touched=False, fill=0):
    shapes = [(s, int(v)) for s, v in shapes if s is not None and not getattr(s, "is_empty", False)]
    if not shapes:
        return np.zeros(out_shape, dtype)
    return rasterize(shapes, out_shape=out_shape, transform=transform, fill=fill,
                     all_touched=all_touched, dtype=dtype)


def burn_batch(shapes, out_shape, x0, y0, all_touched=True):
    """Rasterize (geom, value) in order (later wins) into a full band array, but only over the union bbox
    of geometries that touch the band. Pixel-identical to burn() on the full array."""
    H, Wd = out_shape
    out = np.zeros(out_shape, np.uint16)
    keep = []
    r0 = c0 = 10 ** 9
    r1 = c1 = -10 ** 9
    for g, v in shapes:
        if g is None or g.is_empty:
            continue
        a, b, c, d = g.bounds
        ca, cb = max(0, int(math.floor(a)) - x0 - 2), min(Wd, int(math.ceil(c)) - x0 + 3)
        ra, rb = max(0, int(math.floor(b)) - y0 - 2), min(H, int(math.ceil(d)) - y0 + 3)
        if ca >= cb or ra >= rb:
            continue
        keep.append((g, int(v)))
    if not keep:
        return out
    # chunk the band in 1024-row strips to bound memory of rasterize
    for ra in range(0, H, 1024):
        rb = min(H, ra + 1024)
        strip = box(x0 - 2, y0 + ra - 2, x0 + Wd + 2, y0 + rb + 2)
        sh = [(g, v) for g, v in keep if g.intersects(strip)]
        if sh:
            out[ra:rb] = rasterize(sh, out_shape=(rb - ra, Wd), transform=Affine.translation(x0, y0 + ra),
                                   fill=0, all_touched=all_touched, dtype=np.uint16)
    return out


def tunnelish(t):
    return t.get("tunnel") in ("yes", "building_passage", "culvert") \
        or (t.get("layer", "0").lstrip("-").isdigit() and int(t.get("layer", "0")) < 0) \
        or t.get("covered") == "yes" or t.get("location") == "underground"


LEVEL_H = 3.2
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
WALL_DEFAULTS = ["#d8cfc0", "#cbbfa8", "#e2d6bf", "#c4bbad", "#d9c7a7",
                 "#cdc6ba", "#e6ddcc", "#bdb09b", "#d3c3b0", "#c9c9c2"]
GLASS_DEFAULTS = ["#9fb5c2", "#8aa0b0", "#b0c4cc", "#7e96a4"]


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


def window_transform(c0, r0):
    return Affine.translation(c0, r0)


def ids_for(tree, items, env):
    if tree is None:
        return []
    hits = tree.query(env)
    return [int(h) for h in np.atleast_1d(hits)]


def fill_nan(a):
    mask = ~np.isfinite(a)
    if not mask.any():
        return a
    _, (ir, ic) = ndimage.distance_transform_edt(mask, return_indices=True)
    return a[ir, ic]


def sample_dem(path: Path, x0, y0, width, depth, band=256) -> np.ndarray:
    from pyproj import Transformer
    from scipy.ndimage import map_coordinates
    t = Transformer.from_crs(32651, 4326, always_xy=True)
    out = np.empty((depth, width), np.float32)
    import rasterio
    with rasterio.open(path) as src:
        arr = src.read(1).astype(np.float32)
        a, b, c, d, e, f = src.transform[:6]
        for j0 in range(0, depth, band):
            j1 = min(j0 + band, depth)
            jj, ii = np.mgrid[j0:j1, 0:width].astype(np.float64)
            x, y = to_world(x0 + ii + 0.5, y0 + jj + 0.5)
            lon, lat = t.transform(x, y)
            col = (lon - c) / a - 0.5
            row = (lat - f) / e - 0.5
            out[j0:j1] = map_coordinates(arr, [row, col], order=1, mode="nearest")
            note(f"  dem rows {y0 + j0}..{y0 + j1}")
    return out


def fill_ground_fast(vol, gwin, swin):
    rows, cols = gwin.shape
    zz = np.arange(vol.shape[0], dtype=np.int32)[:, None, None]
    g3 = gwin[None, :, :]
    soil_m = (zz < g3 - 1) & (g3 > 1)
    water_top = (swin == C["water"])
    vol[soil_m] = C["soil"]
    if water_top.any():
        vol[soil_m & water_top[None, :, :]] = C["water2"]
    rs, cs = np.indices((rows, cols))
    tops = np.clip(gwin - 1, 0, vol.shape[0] - 1)
    vol[tops, rs, cs] = swin


def fill_trees(vol, gwin, c0, r0, rows, cols):
    for x, y, t in trees:
        if not (c0 - 6 <= x < c0 + cols + 6 and r0 - 6 <= y < r0 + rows + 6):
            continue
        h = parse_len(t.get("height")) or (6 + stable_hash(int(x), int(y)) % 6)
        h = int(max(4, min(h, 22)))
        cr = max(1, int(round((parse_len(t.get("diameter_crown")) or h * 0.45) / 2)))
        cr = min(cr, 5)
        ci, rj = int(round(x - c0)), int(round(y - r0))
        gz = int(gwin[rj, ci]) if 0 <= rj < rows and 0 <= ci < cols else None
        if gz is None:
            continue
        leaf = LEAVES[stable_hash(int(x), int(y), "l") % len(LEAVES)]
        if 0 <= ci < cols and 0 <= rj < rows:
            vol[gz:gz + max(1, h - cr), rj, ci] = C["trunk"]
        cz = gz + h - cr
        for dj in range(-cr, cr + 1):
            for di in range(-cr, cr + 1):
                if di * di + dj * dj > cr * cr + 1:
                    continue
                cc, rr = ci + di, rj + dj
                if 0 <= cc < cols and 0 <= rr < rows:
                    z0 = cz + (0 if di * di + dj * dj < (cr - 1) ** 2 else 1)
                    vol[z0:cz + cr, rr, cc] = leaf


def fill_buildings(vol, gwin, c0, r0, rows, cols):
    env = box(c0 - 1, r0 - 1, c0 + cols + 1, r0 + rows + 1)
    tr = window_transform(c0, r0)
    has_part = np.zeros((rows, cols), bool)
    if parts:
        pids = ids_for(part_index, parts, env)
        if pids:
            has_part = burn([(parts[i][2], 1) for i in pids], (rows, cols), tr, dtype=np.uint8, all_touched=True) == 1
    if parents:
        bids = ids_for(parent_index, parents, env)
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
        pids.sort(key=lambda i: (parts[i][4], parts[i][3]))
        for i in pids:
            oid, t, geom, h, z0, wid, rid = parts[i]
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


def flatten_parks_fast(ground_m, osm, water_mask, origin=(0, 0), building_mask=None):
    """sculpt.flatten_parks with per-label bounding-box slices (same result, no full-array pass per park)."""
    from scipy.ndimage import uniform_filter, find_objects
    H, W = ground_m.shape
    ox, oy = origin
    park_geoms = []
    for oid, typ, t, g in osm.areas:
        if g is None or g.is_empty:
            continue
        lu = t.get("leisure") or t.get("landuse") or t.get("place")
        if lu in ("park", "garden", "village_green", "playground", "pitch",
                  "recreation_ground", "square", "grass"):
            park_geoms.append(g)
        elif t.get("highway") == "pedestrian" and g.area and g.area > 400:
            park_geoms.append(g)
    if not park_geoms:
        return ground_m, 0
    assert len(park_geoms) < 65535
    shapes = [(g, i + 1) for i, g in enumerate(park_geoms)]
    lab = rasterize(shapes, out_shape=(H, W), transform=Affine.translation(ox, oy), fill=0, dtype=np.uint16)
    out = ground_m.copy()
    bld = building_mask.astype(bool) if building_mask is not None else np.zeros((H, W), bool)
    objs = find_objects(lab)
    for i, sl in enumerate(objs, 1):
        if sl is None:
            continue
        m = (lab[sl] == i) & (water_mask[sl] == 0) & (~bld[sl])
        if int(m.sum()) < 20:
            continue
        v = out[sl]
        vals = v[m]
        plausible = vals[(vals >= 1.5) & (vals <= 16.0)]
        target = float(np.percentile(plausible if plausible.size >= 10 else vals, 20))
        high = m & (v > target + 0.6)
        v[high] = np.minimum(v[high], target + 0.3)
    union = (lab > 0) & (water_mask == 0) & (~bld)
    del lab
    n = 0
    if union.any():
        sm = uniform_filter(out.astype(np.float32), size=7)
        lower = union & (sm + 0.15 < out)
        out[lower] = sm[lower]
        n = int(((ground_m - out) > 0.2).sum())
    return out, n


def main(y0=None, y1=None, name="", restamp=False, osm_path=None, x0=None, x1=None):
    global pal, C, LEAVES, trees, parents, parts, part_index, parent_index, bridge_index, bridge_shapes
    global Y0, Y1, X0, X1
    if y0 is not None:
        Y0 = int(y0)
    if y1 is not None:
        Y1 = int(y1)
    if x0 is not None:
        X0 = int(x0)
    if x1 is not None:
        X1 = int(x1)
    name = name or {
        (0, WIN_W, -2048, 0): "west",
        (0, WIN_W, -4096, -2048): "jingan",
        (0, WIN_W, -5120, -4096): "jingan_west",
        (-2048, 0, -2048, 1024): "south",
        (-2048, 0, -5120, 3072): "south_full",
    }.get((X0, X1, Y0, Y1), f"x{X0}_{X1}_y{Y0}_{Y1}")


    meta = json.loads(META_PATH.read_text())
    BASE = int(meta.get("base", BASE_FALLBACK))
    pal = Palette(meta["palette"])
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
    if pal.entries != [tuple(e) for e in meta["palette"]]:
        note("WARNING: palette grew from", len(meta["palette"]), "to", len(pal.entries),
             "— will write the extended palette back; existing tiles keep old ids")

    RW, RD = X1 - X0, Y1 - Y0  # 4096, 2048
    note("band", name, "window x", X0, "..", X1, "y", Y0, "..", Y1, "BASE", BASE)

    if osm_path:
        osm_path = Path(osm_path)
    else:
        osm_path = ROOT / RAW / "osm_expanded.json"
        if not osm_path.exists():
            osm_path = ROOT / RAW / "osm.json"
    note("loading OSM", osm_path.name, "band", name, "restamp", restamp)
    osm = OSM(str(osm_path))
    note("OSM", osm.timestamp, "areas", len(osm.areas), "lines", len(osm.lines))

    west_npz = WORK / f"dem_{name}.npz"
    if OPTS.get("ground_npz"):
        # reuse an already flattened ground (e.g. ground_W.npz) for a re-seam of owned tiles
        zg = np.load(OPTS["ground_npz"])
        gy0, gx0 = int(zg["y0"]), int(zg["x0"])
        ra, ca = Y0 - gy0, X0 - gx0
        ground_m = zg["ground"][ra:ra + RD, ca:ca + RW].astype(np.float32)
        water_mask = zg["water"][ra:ra + RD, ca:ca + RW].astype(np.uint8)
        assert ground_m.shape == (RD, RW), ground_m.shape
        bmask = np.zeros_like(water_mask)
        note("ground from", OPTS["ground_npz"], "rows", ra, "..", ra + RD)
        west_npz = Path("/__no_dem_cache__")
    # keep the first west band's original cache name
    if name == "west" and not west_npz.exists() and (ROOT / "data" / "dem_west.npz").exists():
        west_npz = ROOT / "data" / "dem_west.npz"
    if OPTS.get("ground_npz"):
        pass
    elif west_npz.exists() and tuple(int(x) for x in np.load(west_npz)["dem"].shape) == (RD, RW):
        z = np.load(west_npz)
        dem = z["dem"].astype(np.float32)
        ground_m = z["ground"].astype(np.float32)
        water_mask = z["water"].astype(np.uint8)
        bmask = z["buildings"].astype(np.uint8) if "buildings" in z.files else np.zeros_like(water_mask)
        note("loaded cached", west_npz, dem.shape)
    else:
        note("sampling Copernicus GLO-30 onto west grid")
        if not DEM_TIF.exists():
            raise SystemExit(f"missing DEM {DEM_TIF}")
        dem = sample_dem(DEM_TIF, X0, Y0, RW, RD)
        origin = Affine.translation(X0, Y0)
        wmask = np.zeros((RD, RW), np.uint8)
        bmask = np.zeros((RD, RW), np.uint8)
        water_geoms, bld_geoms = [], []
        for oid, typ, t, geom in osm.areas:
            if t.get("natural") == "water" or t.get("waterway") in ("riverbank", "dock") or t.get("landuse") == "basin":
                water_geoms.append(geom)
            if t.get("building") not in (None, "no") or "building:part" in t:
                bld_geoms.append(geom)
        if water_geoms:
            wmask = burn([(g, 1) for g in water_geoms], (RD, RW), origin, dtype=np.uint8, all_touched=True)
        if bld_geoms:
            bmask = burn([(g, 1) for g in bld_geoms], (RD, RW), origin, dtype=np.uint8, all_touched=True)
        g = dem.copy()
        g[bmask == 1] = np.nan
        g[(wmask == 1) & (np.nan_to_num(g, nan=0) <= 0.2)] = np.nan
        g = fill_nan(g)
        # keep the first window's water level (0.0 m) so the seam is consistent
        water_z = 0.0
        g = np.where(wmask == 1, water_z, g)
        sm = ndimage.gaussian_filter(g, 3)
        g = np.where(wmask == 1, g, sm)
        ground_m, water_mask = g.astype(np.float32), wmask
        np.savez_compressed(west_npz, dem=dem, ground=ground_m, water=water_mask, buildings=bmask,
                            y0=Y0, x0=X0, note="Copernicus GLO-30, west window, not LiDAR")
        note("wrote", west_npz, "ground", float(ground_m.min()), "…", float(ground_m.max()))

    g_before = ground_m.copy()
    if not OPTS.get("ground_npz"):
        ground_m, n_flat = _sculpt.flatten_highways(ground_m, osm, water_mask, origin=(X0, Y0))
        note("flatten road cells lowered", n_flat)
        ground_m, n_park = flatten_parks_fast(ground_m, osm, water_mask, origin=(X0, Y0), building_mask=bmask)
        note("flatten park cells lowered", n_park)
    g_out = WORK / f"ground_{name}.npz"
    if name == "west":
        g_out = ROOT / "data" / "ground_west.npz"
    # previous flattened ground, for restamp skip
    prev_flat = None
    if restamp and g_out.exists():
        prev_flat = np.load(g_out)["ground"].astype(np.float32)
    if not OPTS.get("ground_npz"):
        np.savez_compressed(g_out,
                            ground=ground_m, water=water_mask, y0=Y0, x0=X0, x1=X1, y1=Y1,
                            note="OSM road/park flatten of GLO-30; not LiDAR")

    # Band W: ground_all.npz is READ-ONLY here (integrator owns it). Only the y=Y1 row is needed for the
    # 1-cell +y apron into the accepted band; -y / -x / +x neighbours are other bands (apron left 0 like
    # every earlier outer edge; integrator re-seams).
    seam_cache = WORK / f"seam_row_y{Y1}.npz"
    g_row = None

    def own_row(path, y):
        """row y (ints, tile ground incl. the water -1 rule) from one of worker-W's own ground_*.npz"""
        z = np.load(path)
        oy0, ox0 = int(z["y0"]), int(z["x0"])
        r = y - oy0
        if not (0 <= r < z["ground"].shape[0]):
            return None
        out = np.zeros((X1 - X0,), np.int32)
        ca, cb = max(X0, ox0), min(X1, ox0 + z["ground"].shape[1])
        gg = np.clip(np.round(z["ground"][r, ca - ox0:cb - ox0] - BASE), 1, 250).astype(np.int32)
        ww = z["water"][r, ca - ox0:cb - ox0]
        out[ca - X0:cb - X0] = np.where(ww == 1, np.maximum(gg - 1, 1), gg)
        return out

    g_first_own = own_row(OPTS["seam_north"], Y1) if OPTS.get("seam_north") else None
    g_south_own = own_row(OPTS["seam_south"], Y0 - 1) if OPTS.get("seam_south") else None
    if g_first_own is not None:
        note("north seam (y=Y1) from", OPTS["seam_north"])
    for attempt in range(10 if g_first_own is None else 0):
        try:
            oldg = np.load(ROOT / "data" / "ground_all.npz")
            oy0, ox0 = int(oldg["y0"]), int(oldg["x0"])
            og = oldg["ground"]
            r = Y1 - oy0
            if 0 <= r < og.shape[0]:
                row = np.zeros((X1 - X0,), np.float32)
                ca, cb = max(X0, ox0), min(X1, ox0 + og.shape[1])
                row[ca - X0:cb - X0] = og[r, ca - ox0:cb - ox0]
                valid = np.zeros((X1 - X0,), bool)
                valid[ca - X0:cb - X0] = True
                g_row = (row, valid)
            del og, oldg
            break
        except Exception as ex:  # integrator may be rewriting it
            note("ground_all read retry", attempt, ex)
            import time
            time.sleep(20)
    g = np.clip(np.round(ground_m - BASE), 1, 250).astype(np.int32)
    if g_first_own is not None:
        g_first0 = g_first_own
        np.savez_compressed(seam_cache, row_int=g_first0, y=Y1, x0=X0, x1=X1, src=str(OPTS["seam_north"]))
    else:
        if g_row is None:
            raise SystemExit("could not read y=Y1 seam row from ground_all.npz")
        np.savez_compressed(seam_cache, row=g_row[0], valid=g_row[1], y=Y1, x0=X0, x1=X1)
        g_first0 = np.where(g_row[1], np.clip(np.round(g_row[0] - BASE), 1, 250), 0).astype(np.int32)
    g_south = g_south_own
    if g_south is not None:
        note("south seam (y=Y0-1) from", OPTS["seam_south"])
    g_west = None
    g_east = np.zeros((Y1 - Y0,), np.int32)
    # restamp: tiles whose core ground moved by more than 0.4 m
    changed = None
    if restamp:
        ref = prev_flat if prev_flat is not None else g_before
        changed = np.abs(ground_m - ref) > 0.4
        note("restamp cells changed", int(changed.sum()))

    origin = Affine.translation(X0, Y0)
    surf = np.full((RD, RW), C["bare"], np.uint16)
    surf_shapes = []
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
            surf_shapes.append((geom, C[key]))
    m = burn_batch(surf_shapes, (RD, RW), X0, Y0)
    surf[m > 0] = m[m > 0]
    del m, surf_shapes
    surf[water_mask == 1] = C["water"]
    g = np.where(water_mask == 1, np.maximum(g - 1, 1), g)

    road_shapes, bridge_shapes = [], []
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
            layer = parse_len(t.get("layer")) or 1
            deck = 6 + max(0, layer) * 4
            bridge_shapes.append((poly, pid, deck))
        else:
            road_shapes.append((poly, pid))
    m = burn_batch(road_shapes, (RD, RW), X0, Y0)
    sel = (m > 0) & (water_mask == 0)
    surf[sel] = m[sel]
    del m, sel
    rail_shapes = []
    for oid, t, line in osm.lines:
        if t.get("railway") not in ("rail", "light_rail", "subway", "tram") or tunnelish(t):
            continue
        if t.get("railway") == "subway" and t.get("bridge") in (None, "no"):
            continue
        poly = line.buffer(2.2 if t.get("railway") == "tram" else 3.0)
        rail_shapes.append((poly, 1))
    m = burn_batch(rail_shapes, (RD, RW), X0, Y0)
    surf[(m > 0) & (water_mask == 0)] = C["ballast"]
    del m
    note("ground colours ready")

    parents, parts = [], []
    for oid, typ, t, geom in osm.areas:
        if t.get("location") == "underground":
            continue
        if "building:part" in t:
            parts.append((oid, t, geom, height_override(oid) or building_height(t), min_height(t), *wall_roof_ids(t, oid)))
        elif t.get("building") not in (None, "no"):
            parents.append((oid, t, geom, height_override(oid) or building_height(t), 0.0, *wall_roof_ids(t, oid)))
    note("buildings", len(parents), "parts", len(parts))

    trees = [(p.x, p.y, t) for oid, t, p in osm.nodes if t.get("natural") == "tree"]
    for oid, typ, t, geom in osm.areas:
        if t.get("natural") == "tree":
            c = geom.centroid
            trees.append((c.x, c.y, t))
        elif t.get("natural") in ("wood", "forest") or t.get("landuse") == "forest":
            minx, miny, maxx, maxy = geom.bounds
            rng = np.random.default_rng(oid % (2**32))
            n = max(1, int(geom.area / 80))
            for _ in range(min(n, 40)):
                x = rng.uniform(minx, maxx)
                y = rng.uniform(miny, maxy)
                if geom.contains(Point(x, y)):
                    trees.append((x, y, t))
    note("trees", len(trees))

    part_index = STRtree([p[2] for p in parts]) if parts else None
    parent_index = STRtree([p[2] for p in parents]) if parents else None
    bridge_index = STRtree([p[0] for p in bridge_shapes]) if bridge_shapes else None

    tx0, tx1 = X0 // N, X1 // N          # 0, 32
    ty0, ty1 = Y0 // N, Y1 // N          # -16, 0  (ty in [ty0, ty1))
    note("1 m tiles tx", tx0, "..", tx1 - 1, "ty", ty0, "..", ty1 - 1)

    # 8 m volume for this block only, array row 0 = y=Y0
    H8 = -(-HMAX // 8)
    W8, D8 = RW // 8, RD // 8
    vol8 = np.zeros((H8, D8, W8), np.uint16)
    g8 = np.clip(g[::8, ::8], 0, 255).astype(np.uint8)
    if g8.shape != (D8, W8):
        tmp = np.zeros((D8, W8), np.uint8)
        tmp[:g8.shape[0], :g8.shape[1]] = g8
        g8 = tmp

    fol_ids = set(LEAVES) | {C["trunk"]}
    grass_ids = {C["grass"], C["grass2"], C["garden"]}
    bare_id = C["bare"]
    is_fol = np.zeros(65536, bool)
    is_fol[list(fol_ids)] = True
    is_fol[list(grass_ids)] = True
    is_grass = np.zeros(65536, bool)
    is_grass[list(grass_ids)] = True

    for d in ("1", "1f", "8"):
        (TILE_OUT / "tiles" / d).mkdir(parents=True, exist_ok=True)

    # refuse to overwrite already-accepted tiles
    def assert_west(tx, ty, s=1):
        span = N * s
        bx0, bx1 = BAND["x0"] // span, BAND["x1"] // span
        by0, by1 = BAND["y0"] // span, BAND["y1"] // span
        if not (bx0 <= tx < bx1 and by0 <= ty < by1):
            raise RuntimeError(f"refusing {s} m tile {tx}_{ty}: outside band W tx {bx0}..{bx1-1} ty {by0}..{by1-1}")
        if s == 8 and not (tx * span >= BAND["x0"] and (tx + 1) * span <= BAND["x1"]
                           and ty * span >= BAND["y0"] and (ty + 1) * span <= BAND["y1"]):
            raise RuntimeError(f"8 m tile {tx}_{ty} not fully inside band W")

    import shutil
    bk = WORK / "backup_1f_preexisting"
    bk.mkdir(parents=True, exist_ok=True)

    def merge_old_1f(path, counts, gw, runs):
        """Union with a pre-existing 1f tile (crown spill from the accepted band): old voxels win where set."""
        if not path.exists():
            return counts, runs
        dst = bk / path.name
        if not dst.exists():
            shutil.copy2(path, dst)
        oc, og, orr = T.load_node(dst)
        newv = T.vol_from_runs(counts, runs.reshape(-1, 3) if len(runs) else np.zeros((0, 3), np.uint16))
        oldv = T.vol_from_runs(oc, orr)
        h = max(newv.shape[0], oldv.shape[0])
        v = np.zeros((h, N + 2, N + 2), np.uint16)
        v[:newv.shape[0]] = newv
        v[:oldv.shape[0]] = np.where(oldv > 0, oldv, v[:oldv.shape[0]])
        note("  merged pre-existing 1f", path.name)
        return T.column_runs(v)

    def apron_only_1f(path, fol_new):
        """force/re-seam: keep the existing 1f core (crowns), replace only the 1-cell apron ring."""
        bdir = WORK / "backup_1f_reseam"
        bdir.mkdir(parents=True, exist_ok=True)
        dst = bdir / path.name
        if not dst.exists():
            shutil.copy2(path, dst)
        oc, og, orr = T.load_node(dst)
        oldv = T.vol_from_runs(oc, orr)
        h = max(oldv.shape[0], fol_new.shape[0])
        v = np.zeros((h, N + 2, N + 2), np.uint16)
        v[:oldv.shape[0]] = oldv
        ring = np.ones((N + 2, N + 2), bool)
        ring[1:-1, 1:-1] = False
        v[:, ring] = 0
        nv = np.zeros_like(v)
        nv[:fol_new.shape[0]] = fol_new
        v[:, ring] = nv[:, ring]
        return T.column_runs(v)

    nruns = bytes1 = bytesf = 0
    nskip = 0
    # padded band (row 0 = y=Y0-1, col 0 = x=X0-1) with the same apron rules as the per-cell loop
    GP = np.zeros((RD + 2, RW + 2), np.int32)
    SP = np.full((RD + 2, RW + 2), C["bare"], np.uint16)
    GP[1:1 + RD, 1:1 + RW] = g
    SP[1:1 + RD, 1:1 + RW] = surf
    GP[RD + 1, 1:1 + RW] = g_first0          # y == Y1
    if g_south is not None:
        GP[0, 1:1 + RW] = g_south
    GP[1:1 + RD, RW + 1] = g_east            # x == X1
    if g_west is not None:
        GP[1:1 + RD, 0] = g_west
    del_apron_ok = True
    for ty in range(ty0, ty1):
        for tx in range(tx0, tx1):
            assert_west(tx, ty, 1)
            if OPTS.get("write_ty") is not None and ty not in OPTS["write_ty"]:
                continue
            # never overwrite already-accepted 1 m tiles (hole-fill / new bands only)
            if not restamp and not OPTS.get("force") and (TILE_OUT / "tiles" / "1" / f"{tx}_{ty}.bin").exists():
                nskip += 1
                continue
            r0, c0 = ty * N - 1, tx * N - 1
            rows = cols = N + 2
            pr, pc = r0 - (Y0 - 1), c0 - (X0 - 1)
            gwin = GP[pr:pr + rows, pc:pc + cols].copy()
            swin = SP[pr:pr + rows, pc:pc + cols].copy()
            assert gwin.shape == (rows, cols)
            if restamp and changed is not None:
                # core only (skip the 1-cell apron)
                y_a, y_b = ty * N, ty * N + N
                x_a, x_b = tx * N, tx * N + N
                ra, rb = max(0, y_a - Y0), min(RD, y_b - Y0)
                ca, cb = max(0, x_a - X0), min(RW, x_b - X0)
                if ra < rb and ca < cb and not changed[ra:rb, ca:cb].any():
                    continue
            vol = np.zeros((HMAX, rows, cols), np.uint16)
            fill_ground_fast(vol, gwin, swin)
            fill_buildings(vol, gwin, c0, r0, rows, cols)
            fill_trees(vol, gwin, c0, r0, rows, cols)
            core = vol[:, 1:1 + N, 1:1 + N]
            ds = T.downsample(core, 8)
            # vol8 row 0 = y=Y0; 8 m cell row = (ty*N - Y0)//8 + local
            r8 = (ty * N - Y0) // 8
            c8 = (tx * N - X0) // 8
            h8, d8, w8 = ds.shape
            if 0 <= r8 < D8 and 0 <= c8 < W8:
                vol8[:h8, r8:r8 + d8, c8:c8 + w8] = ds[:, :min(d8, D8 - r8), :min(w8, W8 - c8)]
            top = int(np.nonzero(vol.reshape(HMAX, -1).any(1))[0].max()) + 1 if vol.any() else 1
            win = vol[:top]
            f = is_fol[win]
            fol = np.where(f, win, 0).astype(np.uint16)
            win = np.where(f, np.where(is_grass[win], bare_id, 0), win).astype(np.uint16)
            gw = np.clip(gwin, 0, 255).astype(np.uint8)
            fc, fr = T.column_runs(fol)
            if OPTS.get("force"):
                fc, fr = apron_only_1f(TILE_OUT / "tiles" / "1f" / f"{tx}_{ty}.bin", fol)
            else:
                fc, fr = merge_old_1f(TILE_OUT / "tiles" / "1f" / f"{tx}_{ty}.bin", fc, gw, fr)
            with gzip.GzipFile(TILE_OUT / "tiles" / "1f" / f"{tx}_{ty}.bin", "wb", compresslevel=9, mtime=0) as fh:
                fh.write(fc.tobytes() + gw.tobytes() + fr.tobytes())
            bytesf += (TILE_OUT / "tiles" / "1f" / f"{tx}_{ty}.bin").stat().st_size
            counts, runs = T.column_runs(win)
            with gzip.GzipFile(TILE_OUT / "tiles" / "1" / f"{tx}_{ty}.bin", "wb", compresslevel=9, mtime=0) as fh:
                fh.write(counts.tobytes() + gw.tobytes() + runs.tobytes())
            bytes1 += (TILE_OUT / "tiles" / "1" / f"{tx}_{ty}.bin").stat().st_size
            nruns += len(runs)
            del vol, win, fol, core, ds
        note(f"  1 m row ty={ty} ({ty - ty0 + 1}/{ty1 - ty0})")

    note(f"tiles level 1 west: {tx1-tx0}x{ty1-ty0} nodes, {nruns} runs, {bytes1/2**20:.1f} MB gz, foliage {bytesf/2**20:.1f} MB, skipped existing {nskip}")

    top8 = int(np.nonzero(vol8.reshape(H8, -1).any(1))[0].max()) + 1 if vol8.any() else 1
    vol8 = vol8[:top8]
    # 8 m tiles: ty=-2 (y=-2048..-1024), ty=-1 (y=-1024..0); tx=0..3
    typ0_8, typ1_8 = Y0 // (N * 8), Y1 // (N * 8)  # e.g. -2, 0 or -4, -2
    tx0_8, tx1_8 = X0 // (N * 8), X1 // (N * 8)
    n8 = bytes8 = 0
    if OPTS.get("force"):
        note("force/re-seam: 8 m clay untouched")
        typ0_8, typ1_8 = typ0_8, typ0_8
    if restamp:
        note("restamp: leaving existing 8 m clay in this band (1 m tiles were restamped)")
        typ0_8, typ1_8 = typ0_8, typ0_8  # empty range
    for ty in range(typ0_8, typ1_8):
        for tx in range(tx0_8, tx1_8):
            assert_west(tx, ty, 8)
            if not restamp and (TILE_OUT / "tiles" / "8" / f"{tx}_{ty}.bin").exists():
                note(f"skip existing 8 m {tx}_{ty}")
                continue
            # 8 m array coords: cell (0,0) is y=Y0, x=X0. Tile ty covers 8 m rows
            # starting at (ty*N) in world-8m index, i.e. array row (ty*N - Y0//8)
            r0 = ty * N - (Y0 // 8) - 1  # array row of apron
            c0 = tx * N - (X0 // 8) - 1
            rows = cols = N + 2
            win = np.zeros((vol8.shape[0], rows, cols), np.uint16)
            gw = np.zeros((rows, cols), np.uint8)
            rr0, rr1 = max(r0, 0), min(r0 + rows, D8)
            cc0, cc1 = max(c0, 0), min(c0 + cols, W8)
            if rr0 < rr1 and cc0 < cc1:
                win[:, rr0 - r0:rr1 - r0, cc0 - c0:cc1 - c0] = vol8[:, rr0:rr1, cc0:cc1]
                gw[rr0 - r0:rr1 - r0, cc0 - c0:cc1 - c0] = g8[rr0:rr1, cc0:cc1]
            top = int(np.nonzero(win.reshape(win.shape[0], -1).any(1))[0].max()) + 1 if win.any() else 1
            counts, runs = T.column_runs(win[:top])
            with gzip.GzipFile(TILE_OUT / "tiles" / "8" / f"{tx}_{ty}.bin", "wb", compresslevel=9, mtime=0) as fh:
                fh.write(counts.tobytes() + gw.tobytes() + runs.tobytes())
            bytes8 += (TILE_OUT / "tiles" / "8" / f"{tx}_{ty}.bin").stat().st_size
            n8 += len(runs)
    note(f"tiles level 8 west: {tx1_8-tx0_8}x{typ1_8-typ0_8} nodes, {n8} runs, {bytes8/2**20:.1f} MB gz")
    del vol8

    rgb_lut = np.array([e[:3] for e in pal.entries] + [(0, 0, 0)] * (65536 - len(pal.entries)), np.uint8)
    Image.fromarray(rgb_lut[surf][::4, ::4]).save(WORK / f"topdown_{name}.jpg", quality=85)
    frag = {
        "band": WORK.name, "bbox_grid": [X0, Y0, X1, Y1],
        "tiles_1m": {"tx": [tx0, tx1 - 1], "ty": [ty0, ty1 - 1]},
        "tiles_8m": {"tx": [tx0_8, tx1_8 - 1], "ty": [typ0_8, typ1_8 - 1]},
        "bytes": int(bytes1 + bytesf + bytes8), "osm": str(osm_path), "osm_timestamp": osm.timestamp,
        "dem": "Copernicus GLO-30", "base": BASE, "palette_len": len(pal.entries),
        "palette_snapped": [[list(k), list(v), d] for k, v, d in SNAPPED[:500]], "n_snapped": len(SNAPPED),
        "note": "band W; meta.json NOT modified (integrator merges); palette frozen to meta.json",
    }
    (WORK / f"meta_fragment_{name}.json").write_text(json.dumps(frag, indent=1))
    note("palette snapped colours", len(SNAPPED))
    note("done band W", name)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--y0", type=int, default=None)
    ap.add_argument("--y1", type=int, default=None)
    ap.add_argument("--x0", type=int, default=None)
    ap.add_argument("--x1", type=int, default=None)
    ap.add_argument("--name", default="W")
    ap.add_argument("--restamp", action="store_true")
    ap.add_argument("--osm", default=None)
    ap.add_argument("--band", default="W", choices=sorted(BANDS))
    ap.add_argument("--seam-north", default=None, help="worker-W ground_*.npz for the y=Y1 apron (else ground_all)")
    ap.add_argument("--seam-south", default=None, help="worker-W ground_*.npz for the y=Y0-1 apron")
    ap.add_argument("--ground-npz", default=None, help="reuse a flattened ground npz (re-seam of owned tiles)")
    ap.add_argument("--write-ty", type=int, action="append", default=None, help="only write these 1 m ty rows")
    ap.add_argument("--force", action="store_true", help="re-seam: rewrite owned in-band 1 m tiles, 1f apron ring only, no 8 m")
    args = ap.parse_args()
    BAND = BANDS[args.band]
    WORK = ROOT / "work" / args.band
    WORK.mkdir(parents=True, exist_ok=True)
    X0, X1, Y0, Y1 = BAND["x0"], BAND["x1"], BAND["y0"], BAND["y1"]
    OPTS.update(seam_north=args.seam_north, seam_south=args.seam_south, ground_npz=args.ground_npz, force=args.force,
                write_ty=set(args.write_ty) if args.write_ty else None)
    main(y0=args.y0, y1=args.y1, name=args.name, restamp=args.restamp, osm_path=args.osm,
         x0=args.x0, x1=args.x1)
