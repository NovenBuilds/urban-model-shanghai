"""Voxelize a west extension toward People's Square, without rebuilding the first window.

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
DBG = ROOT / "data" / "debug"
META_PATH = OUT / "meta.json"
DEM_TIF = ROOT / RAW / "Copernicus_DSM_COG_10_N31_00_E121_00_DEM.tif"

# West block: same x as the first window, y extending inland (smaller j) by 2048 m
# so 8 m clay tiles stay aligned (1024 m) and Park Hotel / the museum fit with margin.
X0, X1 = 0, WIN_W
Y0, Y1 = -2048, 0  # half-open; seam with the first window at y=0
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

    west_npz = ROOT / "data" / f"dem_{name}.npz"
    # keep the first west band's original cache name
    if name == "west" and not west_npz.exists() and (ROOT / "data" / "dem_west.npz").exists():
        west_npz = ROOT / "data" / "dem_west.npz"
    if west_npz.exists() and tuple(int(x) for x in np.load(west_npz)["dem"].shape) == (RD, RW):
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
    ground_m, n_flat = _sculpt.flatten_highways(ground_m, osm, water_mask, origin=(X0, Y0))
    note("flatten road cells lowered", n_flat)
    ground_m, n_park = _sculpt.flatten_parks(ground_m, osm, water_mask, origin=(X0, Y0), building_mask=bmask)
    note("flatten park cells lowered", n_park)
    g_out = ROOT / "data" / f"ground_{name}.npz"
    if name == "west":
        g_out = ROOT / "data" / "ground_west.npz"
    # previous flattened ground, for restamp skip
    prev_flat = None
    if restamp and g_out.exists():
        prev_flat = np.load(g_out)["ground"].astype(np.float32)
    np.savez_compressed(g_out,
                        ground=ground_m, water=water_mask, y0=Y0, x0=X0, x1=X1, y1=Y1,
                        note="OSM road/park flatten of GLO-30; not LiDAR")

    # stitch into ground_all (row = y - y0_all, col = x - x0_all). Keep already-built bands.
    first = np.load(ROOT / "data" / "ground_fixed.npz")["ground"].astype(np.float32)
    allp = ROOT / "data" / "ground_all.npz"
    y0_all, y1_all = Y0, WIN_D
    x0_all, x1_all = X0, WIN_W
    if allp.exists():
        oldg = np.load(allp)
        oy0 = int(oldg["y0"]); oy1 = int(oldg["y1"]) if "y1" in oldg.files else WIN_D
        ox0 = int(oldg["x0"]) if "x0" in oldg.files else 0
        ox1 = int(oldg["x1"]) if "x1" in oldg.files else ox0 + oldg["ground"].shape[1]
        y0_all, y1_all = min(Y0, oy0), max(WIN_D, Y1, oy1)
        x0_all, x1_all = min(X0, ox0), max(WIN_W, X1, ox1)
        G_ALL = np.zeros((y1_all - y0_all, x1_all - x0_all), np.float32)
        og = oldg["ground"].astype(np.float32)
        G_ALL[oy0 - y0_all:oy0 - y0_all + og.shape[0], ox0 - x0_all:ox0 - x0_all + og.shape[1]] = og
    else:
        y0_all, y1_all = min(Y0, 0), WIN_D
        x0_all, x1_all = min(X0, 0), WIN_W
        G_ALL = np.zeros((y1_all - y0_all, x1_all - x0_all), np.float32)
        G_ALL[0 - y0_all:WIN_D - y0_all, 0 - x0_all:WIN_W - x0_all] = first
    # new band (must not overwrite accepted x>=0 / previously built cells except its own box)
    G_ALL[Y0 - y0_all:Y1 - y0_all, X0 - x0_all:X1 - x0_all] = ground_m
    # first window is authoritative for its own box x=0..W, y=0..D
    fa, fb = max(0, 0 - y0_all), min(G_ALL.shape[0], WIN_D - y0_all)
    ca, cb = max(0, 0 - x0_all), min(G_ALL.shape[1], WIN_W - x0_all)
    if fa < fb and ca < cb:
        fs0, fs1 = fa - (0 - y0_all), fb - (0 - y0_all)
        G_ALL[fa:fb, ca:cb] = first[fs0:fs1, :cb - ca]
    np.savez_compressed(allp,
                        ground=G_ALL, y0=y0_all, x0=x0_all, y1=y1_all, x1=x1_all, base=BASE,
                        note="stitched coverage; row=y-y0, col=x-x0; first window y>=0,x>=0 from ground_fixed")
    note("ground_all", G_ALL.shape, "x", x0_all, "..", x1_all, "y", y0_all, "..", y1_all)

    g = np.clip(np.round(ground_m - BASE), 1, 250).astype(np.int32)
    # seam apron at y=Y1 (already-built band to the +y side) and at x=X1 (accepted x>=0 column)
    seam_row = Y1 - y0_all
    if 0 <= seam_row < G_ALL.shape[0]:
        g_seam = np.clip(np.round(G_ALL[seam_row, X0 - x0_all:X1 - x0_all] - BASE), 1, 250).astype(np.int32)
    else:
        g_seam = np.zeros((X1 - X0,), np.int32)
    g_first0 = g_seam  # 1-D along x of this band at y=Y1
    # already-built band on the -y side when expanding in +y (apron cell y=Y0-1)
    seam_lo = (Y0 - 1) - y0_all
    if 0 <= seam_lo < G_ALL.shape[0]:
        g_south = np.clip(np.round(G_ALL[seam_lo, X0 - x0_all:X1 - x0_all] - BASE), 1, 250).astype(np.int32)
    else:
        g_south = None
    # already-built column on the -x side when expanding in +x (apron cell x=X0-1)
    seam_w = (X0 - 1) - x0_all
    if 0 <= seam_w < G_ALL.shape[1] and X0 > x0_all:
        g_west = np.clip(np.round(G_ALL[Y0 - y0_all:Y1 - y0_all, seam_w] - BASE), 1, 250).astype(np.int32)
    else:
        g_west = None
    seam_col = X1 - x0_all
    if 0 <= seam_col < G_ALL.shape[1]:
        g_east = np.clip(np.round(G_ALL[Y0 - y0_all:Y1 - y0_all, seam_col] - BASE), 1, 250).astype(np.int32)
    else:
        g_east = np.zeros((Y1 - Y0,), np.int32)
    # restamp: tiles whose core ground moved by more than 0.4 m
    changed = None
    if restamp:
        ref = prev_flat if prev_flat is not None else g_before
        changed = np.abs(ground_m - ref) > 0.4
        note("restamp cells changed", int(changed.sum()))

    origin = Affine.translation(X0, Y0)
    surf = np.full((RD, RW), C["bare"], np.uint16)
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
            m = burn([(geom, 1)], (RD, RW), origin, dtype=np.uint8, all_touched=True)
            surf[m == 1] = C[key]
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
    for poly, pid in road_shapes:
        m = burn([(poly, 1)], (RD, RW), origin, dtype=np.uint8, all_touched=True)
        surf[(m == 1) & (water_mask == 0)] = pid
    for oid, t, line in osm.lines:
        if t.get("railway") not in ("rail", "light_rail", "subway", "tram") or tunnelish(t):
            continue
        if t.get("railway") == "subway" and t.get("bridge") in (None, "no"):
            continue
        poly = line.buffer(2.2 if t.get("railway") == "tram" else 3.0)
        m = burn([(poly, 1)], (RD, RW), origin, dtype=np.uint8, all_touched=True)
        surf[(m == 1) & (water_mask == 0)] = C["ballast"]
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
        (OUT / "tiles" / d).mkdir(parents=True, exist_ok=True)

    # refuse to overwrite already-accepted tiles
    def assert_west(tx, ty, s=1):
        span = N * s
        # never write accepted columns (typ>=0 and west/Jing'an tx>=0)
        if X1 <= 0 and tx >= 0:
            raise RuntimeError(f"refusing to write accepted-column {s} m tile {tx}_{ty} (tx >= 0)")
        if tx >= X1 // span and X1 <= 0:
            raise RuntimeError(f"refusing to write accepted-column {s} m tile {tx}_{ty} (tx >= {X1 // span})")
        # when extending only in y, never write tiles at or past Y1
        if X0 >= 0 and ty >= Y1 // span:
            raise RuntimeError(f"refusing to write existing-band {s} m tile {tx}_{ty} (ty >= {Y1 // span})")
        if not (X0 // span <= tx < (X1 + span - 1) // span):
            raise RuntimeError(f"tile {tx}_{ty} outside this band x {X0}..{X1}")

    nruns = bytes1 = bytesf = 0
    nskip = 0
    for ty in range(ty0, ty1):
        for tx in range(tx0, tx1):
            assert_west(tx, ty, 1)
            # never overwrite already-accepted 1 m tiles (hole-fill / new bands only)
            if not restamp and (OUT / "tiles" / "1" / f"{tx}_{ty}.bin").exists():
                nskip += 1
                continue
            r0, c0 = ty * N - 1, tx * N - 1
            rows = cols = N + 2
            gwin = np.zeros((rows, cols), np.int32)
            swin = np.full((rows, cols), C["bare"], np.uint16)
            for rr in range(rows):
                y = r0 + rr
                for cc in range(cols):
                    x = c0 + cc
                    if Y0 <= y < Y1 and X0 <= x < X1:
                        gwin[rr, cc] = g[y - Y0, x - X0]
                        swin[rr, cc] = surf[y - Y0, x - X0]
                    elif y == Y1 and X0 <= x < X1:
                        # 1-cell apron into the already-built band at y=Y1
                        gwin[rr, cc] = g_first0[x - X0]
                    elif y == Y0 - 1 and X0 <= x < X1 and g_south is not None:
                        # 1-cell apron into the already-built band at y=Y0-1 (+y expansion)
                        gwin[rr, cc] = g_south[x - X0]
                    elif x == X1 and Y0 <= y < Y1:
                        # 1-cell apron into the accepted x=X1 column
                        gwin[rr, cc] = g_east[y - Y0]
                    elif x == X0 - 1 and Y0 <= y < Y1 and g_west is not None:
                        # 1-cell apron into the already-built column at x=X0-1 (+x expansion)
                        gwin[rr, cc] = g_west[y - Y0]
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
            with gzip.GzipFile(OUT / "tiles" / "1f" / f"{tx}_{ty}.bin", "wb", compresslevel=9, mtime=0) as fh:
                fh.write(fc.tobytes() + gw.tobytes() + fr.tobytes())
            bytesf += (OUT / "tiles" / "1f" / f"{tx}_{ty}.bin").stat().st_size
            counts, runs = T.column_runs(win)
            with gzip.GzipFile(OUT / "tiles" / "1" / f"{tx}_{ty}.bin", "wb", compresslevel=9, mtime=0) as fh:
                fh.write(counts.tobytes() + gw.tobytes() + runs.tobytes())
            bytes1 += (OUT / "tiles" / "1" / f"{tx}_{ty}.bin").stat().st_size
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
    if restamp:
        note("restamp: leaving existing 8 m clay in this band (1 m tiles were restamped)")
        typ0_8, typ1_8 = typ0_8, typ0_8  # empty range
    for ty in range(typ0_8, typ1_8):
        for tx in range(tx0_8, tx1_8):
            assert_west(tx, ty, 8)
            if not restamp and (OUT / "tiles" / "8" / f"{tx}_{ty}.bin").exists():
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
            with gzip.GzipFile(OUT / "tiles" / "8" / f"{tx}_{ty}.bin", "wb", compresslevel=9, mtime=0) as fh:
                fh.write(counts.tobytes() + gw.tobytes() + runs.tobytes())
            bytes8 += (OUT / "tiles" / "8" / f"{tx}_{ty}.bin").stat().st_size
            n8 += len(runs)
    note(f"tiles level 8 west: {tx1_8-tx0_8}x{typ1_8-typ0_8} nodes, {n8} runs, {bytes8/2**20:.1f} MB gz")
    del vol8

    # top-down of this block (full coverage stitch is done separately when x0<0)
    rgb_lut = np.array([e[:3] for e in pal.entries] + [(0, 0, 0)] * (65536 - len(pal.entries)), np.uint8)
    rgb = rgb_lut[surf]
    Image.fromarray(rgb[::2, ::2]).save(DBG / f"topdown_{name}.jpg", quality=85)
    # stitch with existing first-window topdown if present (y-only expansions, x0=0)
    first_td = DBG / "topdown.jpg"
    west_img = Image.fromarray(rgb)
    if X0 < 0:
        note("skipping first-window topdown stitch (x0<0); write preview coverage later")
        first_td = Path("/__skip_topdown__")
    # first window rgb approximation: load existing jpg (half res) and scale
    if first_td.exists():
        old = Image.open(first_td)
        # old is W/2 x D/2 = 2048 x 1536
        first_full = old.resize((WIN_W, WIN_D), Image.Resampling.NEAREST)
        canvas = Image.new("RGB", (WIN_W, WIN_D - Y0), (40, 40, 40))
        # row 0 of canvas = y=Y0; first window starts at row RD
        canvas.paste(Image.fromarray(rgb), (0, 0))
        canvas.paste(first_full, (0, RD))
        canvas.resize((WIN_W // 2, (WIN_D - Y0) // 2), Image.Resampling.NEAREST).save(
            DBG / "topdown_expanded.jpg", quality=85)
        note("topdown expanded", DBG / "topdown_expanded.jpg")

    # update meta.json: add x0/y0 and tile origin, do not change W/D or first-window identity
    levels = []
    for L in meta["tiles"]["levels"]:
        s = int(L["s"])
        span = N * s
        # coverage now x=X0..max(X1,W) = 0..4096, y=Y0..D = -2048..3072
        y_min, y_max = Y0, int(meta["D"])
        x_min, x_max = X0, int(meta["W"])
        txi0, tyi0 = x_min // span, y_min // span
        nx = (x_max + span - 1) // span - txi0
        ny = (y_max + span - 1) // span - tyi0
        levels.append({"s": s, "nx": nx, "ny": ny, "tx0": txi0, "ty0": tyi0})
    meta["x0"] = min(int(meta.get("x0", X0)), X0)
    meta["y0"] = min(int(meta.get("y0", Y0)), Y0)
    # grow exclusive max when a band extends past the previous window (same origin)
    meta["W"] = max(int(meta["W"]), X1)
    meta["D"] = max(int(meta["D"]), Y1)
    # levels already computed from this run's Y0 and meta D; recompute from the union
    levels = []
    for L in meta["tiles"]["levels"]:
        s = int(L["s"])
        span = N * s
        y_min, y_max = int(meta["y0"]), int(meta["D"])
        x_min, x_max = int(meta["x0"]), int(meta["W"])
        txi0, tyi0 = x_min // span, y_min // span
        nx = (x_max + span - 1) // span - txi0
        ny = (y_max + span - 1) // span - tyi0
        levels.append({"s": s, "nx": nx, "ny": ny, "tx0": txi0, "ty0": tyi0})
    meta["tiles"]["levels"] = levels
    if not restamp:
        meta["tiles"]["bytes"] = int(bytes1 + bytesf + bytes8) + int(meta["tiles"].get("bytes") or 0)
    sources = meta.setdefault("sources", {})
    sources[name] = {
        "bbox_grid": [X0, Y0, X1, Y1],
        "tiles_1m": {"tx": [tx0, tx1 - 1], "ty": [ty0, ty1 - 1]},
        "tiles_8m": {"tx": [tx0_8, tx1_8 - 1], "ty": [typ0_8, typ1_8 - 1]},
        "dem": "Copernicus GLO-30",
        "osm": "OpenStreetMap (ODbL)",
        "note": f"streamed {name} block on the same citywide grid; earlier tiles not rebuilt or renumbered",
    }
    # keep palette if we didn't grow it; if we did, write the union
    if len(pal.entries) != len(meta["palette"]):
        meta["palette"] = [list(e) for e in pal.entries]
    META_PATH.write_text(json.dumps(meta))
    note("meta updated", "y0", Y0, "levels", levels)
    note("done west")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--y0", type=int, default=None)
    ap.add_argument("--y1", type=int, default=None)
    ap.add_argument("--x0", type=int, default=None)
    ap.add_argument("--x1", type=int, default=None)
    ap.add_argument("--name", default="")
    ap.add_argument("--restamp", action="store_true")
    ap.add_argument("--osm", default=None)
    args = ap.parse_args()
    main(y0=args.y0, y1=args.y1, name=args.name, restamp=args.restamp, osm_path=args.osm,
         x0=args.x0, x1=args.x1)
