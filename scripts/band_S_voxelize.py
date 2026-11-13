"""Worker-S band voxelizer (S, S2, …) on the citywide grid.

Reuses the exact per-tile logic of voxelize_west.py (frozen copy band_S_vw_snapshot.py) with:
- no writes to meta.json / ground_all.npz / foliage.bin / data/debug (band-local files go to work/<band>/)
- palette frozen to the 196 meta entries: unknown colours snap to the nearest existing
  entry of the same material so parallel bands cannot collide on new palette ids
- ground computed once for the whole band (chunked nearest-fill, bbox-limited park flatten),
  voxelized in y-chunks; 8 m clay from a whole-band 8 m volume
- +x apron from --seam-east ground_*.npz (column at x=X1), not from ground_all.npz
Not LiDAR: Copernicus GLO-30 + OSM.
"""
from __future__ import annotations

import gzip
import json
import sys
import time
from pathlib import Path

import numpy as np
from affine import Affine
from scipy import ndimage
from shapely.geometry import box
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import band_S_vw_snapshot as VW  # noqa: E402
import band_S_sculpt_snapshot as SC  # noqa: E402
import tiles as T  # noqa: E402
from osm import OSM  # noqa: E402

BANDS = {
    "S":  dict(x0=-6144,  x1=-2048, y0=-7168, y1=8192, osm="data/raw/S/osm_S.json",   seam_east=None),
    "S2": dict(x0=-10240, x1=-6144, y0=-7168, y1=8192, osm="data/raw/S2/osm_S2.json", seam_east="work/S/ground_S.npz"),
    "S3": dict(x0=-14336, x1=-10240, y0=-7168, y1=8192, osm="data/raw/S3/osm_S3.json", seam_east="work/S2/ground_S2.npz"),
}

BAND = "S"
X0, X1, Y0, Y1 = -6144, -2048, -7168, 8192
TX0, TX1, TY0, TY1 = X0 // 128, X1 // 128, Y0 // 128, Y1 // 128
WORK = ROOT / "work" / "S"
OUT = ROOT / "web" / "public" / "data"
OSM_PATH = ROOT / "data" / "raw" / "S" / "osm_S.json"
SEAM_EAST = None
META = None
BASE = -9
HMAX = 400
N = T.N
note = VW.note


def configure(band="S", x0=None, x1=None, y0=None, y1=None, osm=None, work=None, seam_east=None, meta=None):
    """Set band bounds and paths. Call before phase_ground / main."""
    global BAND, X0, X1, Y0, Y1, TX0, TX1, TY0, TY1, WORK, OSM_PATH, SEAM_EAST, META, BASE
    BAND = band
    spec = dict(BANDS.get(band, {}))
    X0 = int(x0 if x0 is not None else spec.get("x0", X0))
    X1 = int(x1 if x1 is not None else spec.get("x1", X1))
    Y0 = int(y0 if y0 is not None else spec.get("y0", Y0))
    Y1 = int(y1 if y1 is not None else spec.get("y1", Y1))
    TX0, TX1, TY0, TY1 = X0 // 128, X1 // 128, Y0 // 128, Y1 // 128
    WORK = Path(work) if work else ROOT / "work" / BAND
    WORK.mkdir(parents=True, exist_ok=True)
    OSM_PATH = Path(osm) if osm else ROOT / spec.get("osm", f"data/raw/{BAND}/osm_{BAND}.json")
    if not OSM_PATH.is_absolute():
        OSM_PATH = ROOT / OSM_PATH
    se = seam_east if seam_east is not None else spec.get("seam_east")
    SEAM_EAST = Path(se) if se else None
    if SEAM_EAST is not None and not SEAM_EAST.is_absolute():
        SEAM_EAST = ROOT / SEAM_EAST
    meta_path = Path(meta) if meta else (WORK / "meta_snapshot.json")
    if not meta_path.is_absolute():
        meta_path = ROOT / meta_path
    if not meta_path.exists():
        meta_path = ROOT / "work" / "S" / "meta_snapshot.json"
    META = json.loads(meta_path.read_text())
    BASE = int(META.get("base", -9))
    note("configured", BAND, "x", X0, "..", X1, "y", Y0, "..", Y1, "osm", OSM_PATH, "seam-east", SEAM_EAST)


class SnapPalette(VW.Palette):
    def __init__(self, entries):
        super().__init__(entries)
        self.n0 = len(self.entries)
        self.arr = np.array(self.entries, np.int32)
        self.snapped = {}

    def __call__(self, rgb, mat=VW.M_SOLID, q=4):
        if isinstance(rgb, str):
            rgb = VW.hex_rgb(rgb)
        r, g, b = (min(255, int(round(v / q)) * q) for v in rgb)
        key = (r, g, b, mat)
        if key in self.index:
            return self.index[key]
        if key in self.snapped:
            return self.snapped[key]
        a = self.arr
        same = np.nonzero(a[:, 3] == mat)[0]
        same = same[same > 0]
        cand = same if len(same) else np.arange(1, len(a))
        d = ((a[cand, :3] - np.array([r, g, b])) ** 2).sum(1)
        i = int(cand[int(np.argmin(d))])
        self.snapped[key] = i
        return i


def fill_nan_chunked(a, chunk=2048, pad=768):
    out = a.copy()
    H = a.shape[0]
    for r0 in range(0, H, chunk):
        r1 = min(H, r0 + chunk)
        p0, p1 = max(0, r0 - pad), min(H, r1 + pad)
        sub = a[p0:p1]
        mask = ~np.isfinite(sub)
        if not mask.any():
            continue
        if mask.all():
            out[r0:r1] = 0.0
            continue
        ir, ic = ndimage.distance_transform_edt(mask, return_distances=False, return_indices=True)
        filled = sub[ir, ic]
        out[r0:r1] = filled[r0 - p0:r1 - p0]
        del ir, ic, filled
    out[~np.isfinite(out)] = 0.0
    return out


def flatten_parks_fast(ground_m, osm, water_mask, origin, building_mask):
    """Same rule as sculpt.flatten_parks, but each park is evaluated in its own bbox."""
    from rasterio.features import rasterize
    from scipy.ndimage import uniform_filter
    H, W = ground_m.shape
    ox, oy = origin
    park_geoms = []
    for oid, typ, t, g in osm.areas:
        if g is None or g.is_empty:
            continue
        lu = t.get("leisure") or t.get("landuse") or t.get("place")
        if lu in ("park", "garden", "village_green", "playground", "pitch", "recreation_ground", "square", "grass"):
            park_geoms.append(g)
        elif t.get("highway") == "pedestrian" and g.area and g.area > 400:
            park_geoms.append(g)
    if not park_geoms:
        return ground_m, 0
    lab = rasterize([(g, i + 1) for i, g in enumerate(park_geoms)], out_shape=(H, W),
                    transform=Affine.translation(ox, oy), fill=0, dtype=np.uint16)
    out = ground_m.copy()
    bld = building_mask.astype(bool)
    objs = ndimage.find_objects(lab)
    for i, sl in enumerate(objs, start=1):
        if sl is None:
            continue
        m = (lab[sl] == i) & (water_mask[sl] == 0) & (~bld[sl])
        if int(m.sum()) < 20:
            continue
        o = out[sl]
        vals = o[m]
        plausible = vals[(vals >= 1.5) & (vals <= 16.0)]
        target = float(np.percentile(plausible if plausible.size >= 10 else vals, 20))
        high = m & (o > target + 0.6)
        o[high] = np.minimum(o[high], target + 0.3)
    union = (lab > 0) & (water_mask == 0) & (~bld)
    del lab
    if union.any():
        sm = uniform_filter(out.astype(np.float32), size=7)
        lower = union & (sm + 0.15 < out)
        out[lower] = sm[lower]
        del sm, lower
    n = int(((ground_m - out) > 0.2).sum())
    return out, n


def phase_ground(osm):
    RW, RD = X1 - X0, Y1 - Y0
    gp = WORK / f"ground_{BAND}.npz"
    if gp.exists():
        z = np.load(gp)
        note("loaded", gp)
        return z["ground"].astype(np.float32), z["water"].astype(np.uint8), z["buildings"].astype(np.uint8)
    dp = WORK / f"dem_{BAND}.npy"
    if dp.exists():
        dem = np.load(dp)
    else:
        dem = VW.sample_dem(VW.DEM_TIF, X0, Y0, RW, RD)
        np.save(dp, dem)
    origin = Affine.translation(X0, Y0)
    water_geoms, bld_geoms = [], []
    for oid, typ, t, geom in osm.areas:
        if t.get("natural") == "water" or t.get("waterway") in ("riverbank", "dock") or t.get("landuse") == "basin":
            water_geoms.append(geom)
        if t.get("building") not in (None, "no") or "building:part" in t:
            bld_geoms.append(geom)
    wmask = VW.burn([(g, 1) for g in water_geoms], (RD, RW), origin, dtype=np.uint8, all_touched=True)
    bmask = VW.burn([(g, 1) for g in bld_geoms], (RD, RW), origin, dtype=np.uint8, all_touched=True)
    note("masks water", int(wmask.sum()), "buildings", int(bmask.sum()))
    g = dem.copy()
    del dem
    g[bmask == 1] = np.nan
    g[(wmask == 1) & (np.nan_to_num(g, nan=0) <= 0.2)] = np.nan
    g = fill_nan_chunked(g)
    g = np.where(wmask == 1, np.float32(0.0), g).astype(np.float32)
    sm = ndimage.gaussian_filter(g, 3)
    g = np.where(wmask == 1, g, sm).astype(np.float32)
    del sm
    note("ground raw", float(g.min()), float(g.max()))
    g, n_flat = SC.flatten_highways(g, osm, wmask, origin=(X0, Y0))
    note("flatten road cells lowered", n_flat)
    g, n_park = flatten_parks_fast(g, osm, wmask, (X0, Y0), bmask)
    note("flatten park cells lowered", n_park)
    np.savez_compressed(gp, ground=g, water=wmask, buildings=bmask, x0=X0, y0=Y0, x1=X1, y1=Y1, base=BASE,
                        note=f"band {BAND} ground: GLO-30, building-masked, OSM road/park flatten; row=y-y0, col=x-x0; not LiDAR")
    note("wrote", gp)
    return g, wmask, bmask


def main(chunk_rows=1024, only=None):
    t0 = time.time()
    pal = SnapPalette(META["palette"])
    VW.pal = pal
    C = {
        "paving": pal("#c6c2b8"), "paving2": pal("#b5b4af"), "asphalt": pal("#5f636b"),
        "asphalt_l": pal("#6e7178"), "sett": pal("#8f8981"), "road_paving": pal("#a49f97"),
        "concrete": pal("#bdbab2"), "cycle": pal("#8a5a52"), "gravel": pal("#d8ccb5"),
        "grass": pal("#86b45a", VW.M_GRASS), "grass2": pal("#79a852", VW.M_GRASS), "garden": pal("#73a34e", VW.M_GRASS),
        "dirt": pal("#a58a66"), "water": pal("#5a6e7a", VW.M_WATER), "water2": pal("#4e6270", VW.M_WATER),
        "mark": pal("#f1efe8", VW.M_MARK), "bare": pal("#a8977c", VW.M_SOIL), "soil": pal("#7a5c45", VW.M_SOIL),
        "trunk": pal("#6b4f3a"), "lamp": pal("#ffe2a8", VW.M_LAMP), "pole": pal("#3a3e42", VW.M_METAL),
        "kerb": pal("#cfc7b8"), "deck": pal("#a7a49d", VW.M_CONCRETE), "rail": pal("#55575d", VW.M_METAL),
        "iron": pal("#5a534c", VW.M_METAL), "quay": pal("#9a958c", VW.M_CONCRETE), "promenade": pal("#cfc8bc"),
        "ballast": pal("#8a8178"), "platform": pal("#b7b1a6"), "sand": pal("#d8c79f"),
    }
    LEAVES = [pal(c, VW.M_LEAF) for c in ["#a3c65c", "#8db552", "#76a24a", "#618f43", "#4f7c3c", "#93bb6a", "#7da95f"]]
    VW.C, VW.LEAVES = C, LEAVES
    note("snapped base colours", len(pal.snapped))

    osm = OSM(str(OSM_PATH))
    note("OSM", osm.timestamp, "areas", len(osm.areas), "lines", len(osm.lines), "nodes", len(osm.nodes))
    ground_m, water_mask, bmask = phase_ground(osm)
    RW, RD = X1 - X0, Y1 - Y0
    g = np.clip(np.round(ground_m - BASE), 1, 250).astype(np.int32)
    g = np.where(water_mask == 1, np.maximum(g - 1, 1), g)
    del ground_m

    # +x apron from the neighbouring band's ground at x=X1 (never from ground_all.npz)
    g_east = np.zeros(RD, np.int32)
    if SEAM_EAST is not None and SEAM_EAST.exists():
        ga = np.load(SEAM_EAST)
        gax0, gay0 = int(ga["x0"]), int(ga["y0"])
        col_i = X1 - gax0
        assert 0 <= col_i < ga["ground"].shape[1], (SEAM_EAST, gax0, X1, ga["ground"].shape)
        col = ga["ground"][:, col_i].astype(np.float32)
        ys = np.arange(Y0, Y1)
        ok = (ys - gay0 >= 0) & (ys - gay0 < col.shape[0])
        g_east[ok] = np.clip(np.round(col[ys[ok] - gay0] - BASE), 1, 250).astype(np.int32)
        del ga, col
        note("east apron from", SEAM_EAST, "col x=", X1, "cells", int(ok.sum()))
        d = g[:, -1] - g_east
        note(f"seam x={X1-1} vs x={X1} (m): mean", float(d.mean()), "abs>2", int((np.abs(d) > 2).sum()), "max", int(np.abs(d).max()))
        np.save(WORK / f"seam_x{X1-1}_glevel.npy", g[:, -1].astype(np.uint8))
    else:
        note("WARNING: no --seam-east; +x apron ground stays 0")

    # --- static OSM layers (indexed) -----------------------------------------
    surf_areas = []
    for oid, typ, t, geom in osm.areas:
        key = None
        lu = t.get("landuse") or t.get("leisure") or t.get("natural") or t.get("amenity")
        if lu in ("grass", "park", "garden", "village_green", "meadow", "recreation_ground",
                  "cemetery", "pitch", "golf_course", "allotments"):
            key = "grass" if VW.stable_hash(oid) % 2 == 0 else "grass2"
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
            surf_areas.append((geom, C[key]))
    road_shapes, bridge_shapes = [], []
    for oid, t, line in osm.lines:
        hwy = t.get("highway")
        if not hwy or VW.tunnelish(t):
            continue
        width = VW.parse_len(t.get("width") or t.get("est_width"))
        if width is None:
            lanes = VW.parse_len(t.get("lanes"))
            width = VW.ROAD_W.get(hwy, 6)
            if lanes:
                width = max(width, lanes * 3.5)
        pal_name = VW.ROAD_PAL.get(hwy, "asphalt")
        if t.get("surface") in ("paving_stones", "sett", "cobblestone"):
            pal_name = "sett"
        elif t.get("surface") in ("concrete",):
            pal_name = "concrete"
        elif t.get("surface") in ("dirt", "earth", "grass", "ground"):
            pal_name = "dirt"
        pid = C[pal_name]
        poly = line.buffer(max(1.0, width / 2.0), cap_style=2, join_style=2)
        if t.get("bridge") not in (None, "no"):
            layer = VW.parse_len(t.get("layer")) or 1
            deck = 6 + max(0, layer) * 4
            bridge_shapes.append((poly, pid, deck))
        else:
            road_shapes.append((poly, pid))
    rail_shapes = []
    for oid, t, line in osm.lines:
        if t.get("railway") not in ("rail", "light_rail", "subway", "tram") or VW.tunnelish(t):
            continue
        if t.get("railway") == "subway" and t.get("bridge") in (None, "no"):
            continue
        rail_shapes.append(line.buffer(2.2 if t.get("railway") == "tram" else 3.0))
    sa_tree = STRtree([s[0] for s in surf_areas]) if surf_areas else None
    rd_tree = STRtree([s[0] for s in road_shapes]) if road_shapes else None
    rl_tree = STRtree(rail_shapes) if rail_shapes else None

    parents, parts = [], []
    for oid, typ, t, geom in osm.areas:
        if t.get("location") == "underground":
            continue
        if "building:part" in t:
            parts.append((oid, t, geom, VW.height_override(oid) or VW.building_height(t), VW.min_height(t), *VW.wall_roof_ids(t, oid)))
        elif t.get("building") not in (None, "no"):
            parents.append((oid, t, geom, VW.height_override(oid) or VW.building_height(t), 0.0, *VW.wall_roof_ids(t, oid)))
    note("buildings", len(parents), "parts", len(parts), "snapped colours so far", len(pal.snapped))
    trees_all = [(p.x, p.y, t) for oid, t, p in osm.nodes if t.get("natural") == "tree"]
    from shapely.geometry import Point
    for oid, typ, t, geom in osm.areas:
        if t.get("natural") == "tree":
            c = geom.centroid
            trees_all.append((c.x, c.y, t))
        elif t.get("natural") in ("wood", "forest") or t.get("landuse") == "forest":
            minx, miny, maxx, maxy = geom.bounds
            rng = np.random.default_rng(oid % (2**32))
            n = max(1, int(geom.area / 80))
            for _ in range(min(n, 40)):
                x = rng.uniform(minx, maxx)
                y = rng.uniform(miny, maxy)
                if geom.contains(Point(x, y)):
                    trees_all.append((x, y, t))
    note("trees", len(trees_all))
    VW.parents, VW.parts = parents, parts
    VW.part_index = STRtree([p[2] for p in parts]) if parts else None
    VW.parent_index = STRtree([p[2] for p in parents]) if parents else None
    VW.bridge_shapes = bridge_shapes
    VW.bridge_index = STRtree([p[0] for p in bridge_shapes]) if bridge_shapes else None
    tree_xy = np.array([(x, y) for x, y, _ in trees_all]) if trees_all else np.zeros((0, 2))

    H8 = -(-HMAX // 8)
    W8, D8 = RW // 8, RD // 8
    vol8 = np.zeros((H8, D8, W8), np.uint16)
    if only:
        old8 = np.load(WORK / f"vol8_{BAND}.npy")
        vol8[:old8.shape[0]] = old8
        del old8
    g8 = np.clip(g[::8, ::8], 0, 255).astype(np.uint8)
    preview8 = np.zeros((D8, W8), np.uint16)

    fol_ids = set(LEAVES) | {C["trunk"]}
    grass_ids = {C["grass"], C["grass2"], C["garden"]}
    bare_id = C["bare"]
    is_fol = np.zeros(65536, bool)
    is_fol[list(fol_ids)] = True
    is_fol[list(grass_ids)] = True
    is_grass = np.zeros(65536, bool)
    is_grass[list(grass_ids)] = True

    def check(tx, ty, s):
        span = N * s
        # never write typ ty < -56 (WX and other west-south bands) or outside this band box
        if ty < Y0 // span or (ty + 1) * span > Y1 or tx < X0 // span or (tx + 1) * span > X1:
            raise RuntimeError(f"refusing {s} m tile {tx}_{ty}: outside band {BAND} x={X0}..{X1} y={Y0}..{Y1}")

    nruns = bytes1 = bytesf = nskip = nwritten = 0
    for cy0 in range(Y0, Y1, chunk_rows):
        cy1 = min(Y1, cy0 + chunk_rows)
        if only and not any(cy0 // N <= ty < cy1 // N for _tx, ty in only):
            continue
        sy0, sy1 = max(Y0, cy0 - 1), min(Y1, cy1 + 1)
        sh = (sy1 - sy0, RW)
        org = Affine.translation(X0, sy0)
        env = box(X0 - 2, sy0 - 2, X1 + 2, sy1 + 2)
        surf = np.full(sh, C["bare"], np.uint16)
        for i in sorted(int(h) for h in np.atleast_1d(sa_tree.query(env))):
            geom, pid = surf_areas[i]
            m = VW.burn([(geom, 1)], sh, org, dtype=np.uint8, all_touched=True)
            surf[m == 1] = pid
        wm = water_mask[sy0 - Y0:sy1 - Y0]
        surf[wm == 1] = C["water"]
        # 8 m colour preview of this chunk (row 0 = y=Y0, same orientation as other band topdowns)
        for r8 in range((cy0 - Y0) // 8, (cy1 - Y0) // 8):
            y = Y0 + r8 * 8
            if sy0 <= y < sy1:
                preview8[r8] = surf[y - sy0, ::8]
        for i in sorted(int(h) for h in np.atleast_1d(rd_tree.query(env))):
            poly, pid = road_shapes[i]
            m = VW.burn([(poly, 1)], sh, org, dtype=np.uint8, all_touched=True)
            surf[(m == 1) & (wm == 0)] = pid
        if rl_tree is not None:
            for i in sorted(int(h) for h in np.atleast_1d(rl_tree.query(env))):
                m = VW.burn([(rail_shapes[i], 1)], sh, org, dtype=np.uint8, all_touched=True)
                surf[(m == 1) & (wm == 0)] = C["ballast"]
        sel = (tree_xy[:, 1] >= cy0 - 8) & (tree_xy[:, 1] < cy1 + 8) if len(tree_xy) else np.zeros(0, bool)
        VW.trees = [trees_all[i] for i in np.nonzero(sel)[0]]
        for ty in range(cy0 // N, cy1 // N):
            for tx in range(TX0, TX1):
                check(tx, ty, 1)
                p1 = OUT / "tiles" / "1" / f"{tx}_{ty}.bin"
                pf = OUT / "tiles" / "1f" / f"{tx}_{ty}.bin"
                if only and (tx, ty) not in only:
                    continue
                if p1.exists():
                    nskip += 1
                    continue
                orphan = None
                if pf.exists():
                    # pre-existing crown-spill 1f tile from an earlier foliage pass (no 1 m tile): back up and merge
                    bk = WORK / "orphan_1f_backup"
                    bk.mkdir(exist_ok=True)
                    (bk / pf.name).write_bytes(pf.read_bytes())
                    oc, og, orr = T.load_node(pf)
                    orphan = T.vol_from_runs(oc, orr)
                    note("merging pre-existing orphan 1f", pf.name)
                r0, c0 = ty * N - 1, tx * N - 1
                rows = cols = N + 2
                gwin = np.zeros((rows, cols), np.int32)
                swin = np.full((rows, cols), C["bare"], np.uint16)
                ya, yb = max(r0, Y0), min(r0 + rows, Y1)
                xa, xb = max(c0, X0), min(c0 + cols, X1)
                gwin[ya - r0:yb - r0, xa - c0:xb - c0] = g[ya - Y0:yb - Y0, xa - X0:xb - X0]
                swin[ya - r0:yb - r0, xa - c0:xb - c0] = surf[ya - sy0:yb - sy0, xa - X0:xb - X0]
                if c0 + cols - 1 == X1:  # apron column x=X1 into accepted coverage
                    gwin[ya - r0:yb - r0, cols - 1] = g_east[ya - Y0:yb - Y0]
                vol = np.zeros((HMAX, rows, cols), np.uint16)
                VW.fill_ground_fast(vol, gwin, swin)
                VW.fill_buildings(vol, gwin, c0, r0, rows, cols)
                VW.fill_trees(vol, gwin, c0, r0, rows, cols)
                core = vol[:, 1:1 + N, 1:1 + N]
                ds = T.downsample(core, 8)
                r8 = (ty * N - Y0) // 8
                c8 = (tx * N - X0) // 8
                h8, d8, w8 = ds.shape
                vol8[:h8, r8:r8 + d8, c8:c8 + w8] = ds[:, :min(d8, D8 - r8), :min(w8, W8 - c8)]
                top = int(np.nonzero(vol.reshape(HMAX, -1).any(1))[0].max()) + 1 if vol.any() else 1
                win = vol[:top]
                f = is_fol[win]
                fol = np.where(f, win, 0).astype(np.uint16)
                if orphan is not None:
                    hh = max(fol.shape[0], orphan.shape[0])
                    mf = np.zeros((hh, rows, cols), np.uint16)
                    mf[:fol.shape[0]] = fol
                    oo = np.zeros_like(mf)
                    oo[:orphan.shape[0]] = orphan
                    fol = np.where(mf != 0, mf, oo)
                    del mf, oo
                win = np.where(f, np.where(is_grass[win], bare_id, 0), win).astype(np.uint16)
                gw = np.clip(gwin, 0, 255).astype(np.uint8)
                fc, fr = T.column_runs(fol)
                with gzip.GzipFile(pf, "wb", compresslevel=9, mtime=0) as fh:
                    fh.write(fc.tobytes() + gw.tobytes() + fr.tobytes())
                bytesf += pf.stat().st_size
                counts, runs = T.column_runs(win)
                with gzip.GzipFile(p1, "wb", compresslevel=9, mtime=0) as fh:
                    fh.write(counts.tobytes() + gw.tobytes() + runs.tobytes())
                bytes1 += p1.stat().st_size
                nruns += len(runs)
                nwritten += 1
                del vol, win, fol, core, ds
            note(f"  1 m row ty={ty} written {nwritten} skipped {nskip} t={time.time() - t0:.0f}s")
        del surf
    note(f"1 m: written {nwritten}, skipped {nskip}, runs {nruns}, {bytes1/2**20:.1f} MB, 1f {bytesf/2**20:.1f} MB")

    # 8 m clay: tx -6..-3, ty -7..7 (all fully inside the band)
    top8 = int(np.nonzero(vol8.reshape(H8, -1).any(1))[0].max()) + 1 if vol8.any() else 1
    vol8 = vol8[:top8]
    n8 = bytes8 = w8n = 0
    for ty in range(Y0 // 1024, Y1 // 1024):
        for tx in range(X0 // 1024, X1 // 1024):
            check(tx, ty, 8)
            p8 = OUT / "tiles" / "8" / f"{tx}_{ty}.bin"
            if only:
                hit = any(tx * 8 <= otx < (tx + 1) * 8 and ty * 8 <= oty < (ty + 1) * 8 for otx, oty in only)
                if not hit:
                    continue
                # rewrite our own 8 m tile (written by this worker earlier in round)
            elif p8.exists():
                note("skip existing 8 m", tx, ty)
                continue
            r0 = ty * N - (Y0 // 8) - 1
            c0 = tx * N - (X0 // 8) - 1
            rows = cols = N + 2
            win = np.zeros((vol8.shape[0], rows, cols), np.uint16)
            gw = np.zeros((rows, cols), np.uint8)
            rr0, rr1 = max(r0, 0), min(r0 + rows, D8)
            cc0, cc1 = max(c0, 0), min(c0 + cols, W8)
            win[:, rr0 - r0:rr1 - r0, cc0 - c0:cc1 - c0] = vol8[:, rr0:rr1, cc0:cc1]
            gw[rr0 - r0:rr1 - r0, cc0 - c0:cc1 - c0] = g8[rr0:rr1, cc0:cc1]
            top = int(np.nonzero(win.reshape(win.shape[0], -1).any(1))[0].max()) + 1 if win.any() else 1
            counts, runs = T.column_runs(win[:top])
            with gzip.GzipFile(p8, "wb", compresslevel=9, mtime=0) as fh:
                fh.write(counts.tobytes() + gw.tobytes() + runs.tobytes())
            bytes8 += p8.stat().st_size
            n8 += len(runs)
            w8n += 1
    note(f"8 m: written {w8n}, runs {n8}, {bytes8/2**20:.2f} MB")
    np.save(WORK / f"vol8_{BAND}.npy", vol8)

    rgb_lut = np.array([e[:3] for e in pal.entries] + [(0, 0, 0)] * (65536 - len(pal.entries)), np.uint8)
    if only:
        note("patched", nwritten, "tiles", sorted(only))
        return
    from PIL import Image
    Image.fromarray(rgb_lut[preview8]).save(WORK / f"topdown_{BAND}.jpg", quality=85)
    # water preview: +y up (same convention as work/S/water_ground_preview.png)
    w8 = water_mask[::8, ::8]
    wp = rgb_lut[preview8].copy()
    wp[w8 == 1] = (40, 90, 200)
    Image.fromarray(wp[::-1]).save(WORK / f"water_ground_preview_{BAND}.png")
    note("wrote", WORK / f"topdown_{BAND}.jpg", "and water preview")
    json.dump({
        "band": BAND, "bbox_grid": [X0, Y0, X1, Y1],
        "tiles_1m": {"tx": [TX0, TX1 - 1], "ty": [TY0, TY1 - 1]},
        "tiles_8m": {"tx": [X0 // 1024, X1 // 1024 - 1], "ty": [Y0 // 1024, Y1 // 1024 - 1]},
        "osm_timestamp": osm.timestamp, "osm_file": str(OSM_PATH.relative_to(ROOT)),
        "dem": "Copernicus GLO-30 (Copernicus_DSM_COG_10_N31_00_E121_00_DEM.tif)",
        "palette_len_used": len(META["palette"]), "palette_grew": len(pal.entries) != len(META["palette"]),
        "snapped_colours": len(pal.snapped), "bytes_1m": bytes1, "bytes_1f": bytesf, "bytes_8m": bytes8,
        "written_1m": nwritten, "skipped_1m": nskip, "elapsed_s": round(time.time() - t0),
        "seam_east": str(SEAM_EAST) if SEAM_EAST else None,
    }, open(WORK / f"voxelize_{BAND}_summary.json", "w"), indent=2)
    note("done band", BAND, round(time.time() - t0), "s")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--band", default="S", choices=sorted(BANDS))
    ap.add_argument("--x0", type=int, default=None)
    ap.add_argument("--x1", type=int, default=None)
    ap.add_argument("--y0", type=int, default=None)
    ap.add_argument("--y1", type=int, default=None)
    ap.add_argument("--osm", default=None)
    ap.add_argument("--work", default=None)
    ap.add_argument("--seam-east", default=None, help="neighbour ground_*.npz whose column at x=X1 becomes the +x apron")
    ap.add_argument("--meta", default=None)
    ap.add_argument("--ground-only", action="store_true")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    configure(band=a.band, x0=a.x0, x1=a.x1, y0=a.y0, y1=a.y1, osm=a.osm, work=a.work,
              seam_east=a.seam_east, meta=a.meta)
    if a.ground_only:
        pal = SnapPalette(META["palette"]); VW.pal = pal
        osm = OSM(str(OSM_PATH))
        phase_ground(osm)
    elif a.only:
        main(only={tuple(int(v) for v in t.split("_")) for t in a.only.split(",")})
    else:
        main()
