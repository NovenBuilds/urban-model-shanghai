"""Band N3 (north 3, x=18432..22528) voxelizer, parametrized copy of band_N2_voxelize.py.
Adds the Yangtze estuary as flat water from OSM natural=coastline (band_N2_sea.py).
Band N for the parallel build.

Copy-in-spirit of voxelize_west.py (same grid, BASE, palette ids, height/colour rules,
same ground recipe: Copernicus GLO-30 masked by OSM buildings, nearest-filled, water at
0 m, gaussian sigma 3, OSM road/park flatten). Differences, all for safe parallel work:
  * writes ONLY tiles/1, tiles/1f, tiles/8 inside x=18432..22528, y=-7168..8192, never
    overwrites an existing file; does NOT touch meta.json, ground_all.npz, foliage.bin;
  * palette is frozen (unknown colours map to the nearest existing entry of the same
    material) so parallel bands cannot hand out colliding palette ids;
  * ground is prepared on a 128 m padded window and cropped (less edge bias at seams),
    and blended over 24 m toward the accepted x=10239 ground column;
  * the -x apron column of every 1 m / 8 m node is copied voxel-for-voxel from the
    accepted neighbour node (band N2 tx=143 / 8 m tx=17), so culling at x=18432 is exact;
    other outer aprons keep the earlier convention (ground 0) for the integrator;
  * per-category single-pass rasterization and vectorized column fills (same output
    rules, much less time/memory).
Not LiDAR. No geometry without OSM footprints.
"""
from __future__ import annotations

import gzip
import json
import sys
import time
from pathlib import Path

import numpy as np
from affine import Affine
from rasterio.features import rasterize
from scipy import ndimage
from shapely.geometry import Point, box
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import voxelize_west as V  # noqa: E402  (read-only reuse of helpers)
import tiles as T  # noqa: E402
import sculpt as _sculpt  # noqa: E402
from osm import OSM  # noqa: E402

OUT = ROOT / "web" / "public" / "data"
WORK = ROOT / "work" / "N3"
WORK.mkdir(parents=True, exist_ok=True)
DEM_TIF = V.DEM_TIF
X0, X1, Y0, Y1 = 18432, 22528, -7168, 8192
PAD = 128
HMAX = 400
N = T.N
BLEND = 24
OSM_PATH = ROOT / "data" / "raw" / "N3" / "osm_N3.json"
t_start = time.time()


def note(*a):
    print(f"[{time.time() - t_start:7.0f}s]", " ".join(str(x) for x in a), flush=True)


class FrozenPalette(V.Palette):
    remapped = {}

    def __call__(self, rgb, mat=V.M_SOLID, q=4):
        if isinstance(rgb, str):
            rgb = V.hex_rgb(rgb)
        r, g, b = (min(255, int(round(v / q)) * q) for v in rgb)
        key = (r, g, b, mat)
        if key in self.index:
            return self.index[key]
        if key in self.remapped:
            return self.remapped[key]
        best, bd = 0, 1e18
        for i, e in enumerate(self.entries[1:], 1):
            d = (e[0] - r) ** 2 + (e[1] - g) ** 2 + (e[2] - b) ** 2 + (0 if e[3] == mat else 3 * 255 ** 2)
            if d < bd:
                best, bd = i, d
        self.remapped[key] = best
        return best


def in_band(tx, ty, s):
    span = N * s
    return X0 // span <= tx < X1 // span and Y0 // span <= ty < Y1 // span


def vol_from_runs_fast(counts, runs, h):
    m = N + 2
    vol = np.zeros((h, m * m), np.uint16)
    if len(runs):
        col = np.repeat(np.arange(m * m), counts.astype(np.int64))
        for (z0, ln, p), c in zip(runs.tolist(), col.tolist()):
            z1 = min(h, z0 + ln)
            if z1 > z0:
                vol[z0:z1, c] = p
    return vol.reshape(h, m, m)


def fill_cols(vol, rs, cs, gz, top, wid, rid):
    ok = top > gz
    if not ok.any():
        return
    rs, cs, gz, top = rs[ok], cs[ok], gz[ok], top[ok]
    zz = np.arange(vol.shape[0])[:, None]
    m = (zz >= gz[None, :]) & (zz < top[None, :])
    cur = vol[:, rs, cs]
    vol[:, rs, cs] = np.where(m, wid, cur)
    vol[top - 1, rs, cs] = rid


def main():
    meta = json.loads((OUT / "meta.json").read_text())
    BASE = int(meta.get("base", -9))
    pal = FrozenPalette(meta["palette"])
    V.pal = pal  # wall_roof_ids uses module-global pal
    P = pal
    C = {
        "paving": P("#c6c2b8"), "paving2": P("#b5b4af"), "asphalt": P("#5f636b"),
        "asphalt_l": P("#6e7178"), "sett": P("#8f8981"), "road_paving": P("#a49f97"),
        "concrete": P("#bdbab2"), "cycle": P("#8a5a52"), "gravel": P("#d8ccb5"),
        "grass": P("#86b45a", V.M_GRASS), "grass2": P("#79a852", V.M_GRASS), "garden": P("#73a34e", V.M_GRASS),
        "dirt": P("#a58a66"), "water": P("#5a6e7a", V.M_WATER), "water2": P("#4e6270", V.M_WATER),
        "mark": P("#f1efe8", V.M_MARK), "bare": P("#a8977c", V.M_SOIL), "soil": P("#7a5c45", V.M_SOIL),
        "trunk": P("#6b4f3a"), "lamp": P("#ffe2a8", V.M_LAMP), "pole": P("#3a3e42", V.M_METAL),
        "kerb": P("#cfc7b8"), "deck": P("#a7a49d", V.M_CONCRETE), "rail": P("#55575d", V.M_METAL),
        "iron": P("#5a534c", V.M_METAL), "quay": P("#9a958c", V.M_CONCRETE), "promenade": P("#cfc8bc"),
        "ballast": P("#8a8178"), "platform": P("#b7b1a6"), "sand": P("#d8c79f"),
    }
    LEAVES = [P(c, V.M_LEAF) for c in ["#a3c65c", "#8db552", "#76a24a", "#618f43", "#4f7c3c", "#93bb6a", "#7da95f"]]
    if FrozenPalette.remapped:
        note("WARNING base colours remapped", FrozenPalette.remapped)

    RW, RD = X1 - X0, Y1 - Y0
    PX0, PY0 = X0 - PAD, Y0 - PAD
    PW, PD = RW + 2 * PAD, RD + 2 * PAD
    note("band N3 x", X0, X1, "y", Y0, Y1, "BASE", BASE, "pad", PAD)

    osm = OSM(str(OSM_PATH))
    note("OSM", OSM_PATH.name, osm.timestamp, "areas", len(osm.areas), "lines", len(osm.lines), "nodes", len(osm.nodes))

    # ---------------- ground ----------------
    gnpz = WORK / "ground_N3.npz"
    porigin = Affine.translation(PX0, PY0)
    water_geoms = [g for oid, typ, t, g in osm.areas
                   if t.get("natural") == "water" or t.get("waterway") in ("riverbank", "dock") or t.get("landuse") == "basin"]
    import band_N2_sea
    sea = band_N2_sea.sea_polygon(osm, PX0, PY0, PX0 + PW, PY0 + PD)
    if not sea.is_empty:
        water_geoms.append(sea)
        (WORK / "sea_N3.wkt").write_text(sea.wkt)
    note("coastline sea polygon m2", int(sea.area))
    bld_geoms = [g for oid, typ, t, g in osm.areas if t.get("building") not in (None, "no") or "building:part" in t]
    if gnpz.exists():
        z = np.load(gnpz)
        ground_m = z["ground"].astype(np.float32)
        water_mask = z["water"].astype(np.uint8)
        bmask = z["buildings"].astype(np.uint8)
        note("loaded cached", gnpz)
    else:
        dem = V.sample_dem(DEM_TIF, PX0, PY0, PW, PD, band=1024)
        note("dem sampled", dem.shape, float(dem.min()), float(dem.max()))
        wmask = V.burn([(g, 1) for g in water_geoms], (PD, PW), porigin, dtype=np.uint8, all_touched=True)
        pbmask = V.burn([(g, 1) for g in bld_geoms], (PD, PW), porigin, dtype=np.uint8, all_touched=True)
        g = dem
        g[pbmask == 1] = np.nan
        g[(wmask == 1) & (np.nan_to_num(g, nan=0) <= 0.2)] = np.nan
        mask = ~np.isfinite(g)
        if mask.any():
            ir, ic = ndimage.distance_transform_edt(mask, return_distances=False, return_indices=True)
            g = g[ir, ic]
            del ir, ic
        del mask
        g = np.where(wmask == 1, np.float32(0.0), g).astype(np.float32)
        sm = ndimage.gaussian_filter(g, 3)
        g = np.where(wmask == 1, g, sm).astype(np.float32)
        del sm
        note("ground raw", float(g.min()), float(g.max()))
        g, n_flat = _sculpt.flatten_highways(g, osm, wmask, origin=(PX0, PY0))
        note("flatten road cells lowered", n_flat)
        g, n_park = _sculpt.flatten_parks(g, osm, wmask, origin=(PX0, PY0), building_mask=pbmask)
        note("flatten park cells lowered", n_park)
        ground_m = g[PAD:PAD + RD, PAD:PAD + RW].copy()
        water_mask = wmask[PAD:PAD + RD, PAD:PAD + RW].copy()
        bmask = pbmask[PAD:PAD + RD, PAD:PAD + RW].copy()
        np.savez_compressed(WORK / "ground_N3_padded_edges.npz",
                            west=g[:, :PAD + 2], east=g[:, -PAD - 2:], south=g[:PAD + 2], north=g[-PAD - 2:],
                            px0=PX0, py0=PY0, note="padded ground edges (m) for seam work, not LiDAR")
        del g, dem, wmask, pbmask

        # blend toward the accepted x=10239 column (read from tiles/1/79_ty.bin, ground byte at col 128)
        west = np.full(RD, np.nan, np.float32)
        for ty in range(Y0 // N, Y1 // N):
            p = OUT / "tiles" / "1" / f"{X0 // N - 1}_{ty}.bin"
            if not p.exists():
                continue
            _, gw, _ = T.load_node(p)
            west[ty * N - Y0: ty * N - Y0 + N] = gw[1:1 + N, 128].astype(np.float32) + BASE
        okw = np.isfinite(west)
        note("west seam column found", int(okw.sum()), "of", RD)
        for i in range(BLEND):
            w = (i + 1) / (BLEND + 1)
            col = ground_m[:, i]
            nb = np.where(okw, west, col)
            blended = w * col + (1 - w) * nb
            ground_m[:, i] = np.where(water_mask[:, i] == 1, col, blended)
        np.savez_compressed(gnpz, ground=ground_m, water=water_mask, buildings=bmask,
                            x0=X0, y0=Y0, x1=X1, y1=Y1, base=BASE,
                            note="band N3 ground (m): GLO-30 masked+filled+smoothed, OSM road/park flatten, 24 m blend to x=18431; not LiDAR")
        note("wrote", gnpz, float(ground_m.min()), float(ground_m.max()))

    g = np.clip(np.round(ground_m - BASE), 1, 250).astype(np.int32)
    g = np.where(water_mask == 1, np.maximum(g - 1, 1), g)
    del ground_m

    # ---------------- surface colours ----------------
    origin = Affine.translation(X0, Y0)
    surf = np.full((RD, RW), C["bare"], np.uint16)
    lu_shapes = []
    for oid, typ, t, geom in osm.areas:
        key = None
        lu = t.get("landuse") or t.get("leisure") or t.get("natural") or t.get("amenity")
        if lu in ("grass", "park", "garden", "village_green", "meadow", "recreation_ground",
                  "cemetery", "pitch", "golf_course", "allotments"):
            key = "grass" if V.stable_hash(oid) % 2 == 0 else "grass2"
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
            lu_shapes.append((geom, C[key]))
    m = V.burn(lu_shapes, (RD, RW), origin, dtype=np.uint16, all_touched=True)
    surf = np.where(m > 0, m, surf).astype(np.uint16)
    surf[water_mask == 1] = C["water"]
    del m

    road_shapes, bridge_shapes = [], []
    for oid, t, line in osm.lines:
        hwy = t.get("highway")
        if not hwy or V.tunnelish(t):
            continue
        width = V.parse_len(t.get("width") or t.get("est_width"))
        if width is None:
            lanes = V.parse_len(t.get("lanes"))
            width = V.ROAD_W.get(hwy, 6)
            if lanes:
                width = max(width, lanes * 3.5)
        pal_name = V.ROAD_PAL.get(hwy, "asphalt")
        if t.get("surface") in ("paving_stones", "sett", "cobblestone"):
            pal_name = "sett"
        elif t.get("surface") in ("concrete",):
            pal_name = "concrete"
        elif t.get("surface") in ("dirt", "earth", "grass", "ground"):
            pal_name = "dirt"
        pid = C[pal_name]
        poly = line.buffer(max(1.0, width / 2.0), cap_style=2, join_style=2)
        if t.get("bridge") not in (None, "no"):
            layer = V.parse_len(t.get("layer")) or 1
            deck = 6 + max(0, layer) * 4
            bridge_shapes.append((poly, pid, deck))
        else:
            road_shapes.append((poly, pid))
    m = V.burn(road_shapes, (RD, RW), origin, dtype=np.uint16, all_touched=True)
    surf = np.where((m > 0) & (water_mask == 0), m, surf).astype(np.uint16)
    rail = []
    for oid, t, line in osm.lines:
        if t.get("railway") not in ("rail", "light_rail", "subway", "tram") or V.tunnelish(t):
            continue
        if t.get("railway") == "subway" and t.get("bridge") in (None, "no"):
            continue
        rail.append((line.buffer(2.2 if t.get("railway") == "tram" else 3.0), 1))
    m = V.burn(rail, (RD, RW), origin, dtype=np.uint8, all_touched=True)
    surf[(m == 1) & (water_mask == 0)] = C["ballast"]
    del m
    note("ground colours ready; roads", len(road_shapes), "bridges", len(bridge_shapes), "rail", len(rail))

    parents, parts = [], []
    for oid, typ, t, geom in osm.areas:
        if t.get("location") == "underground":
            continue
        if "building:part" in t:
            parts.append((oid, t, geom, V.height_override(oid) or V.building_height(t), V.min_height(t), *V.wall_roof_ids(t, oid)))
        elif t.get("building") not in (None, "no"):
            parents.append((oid, t, geom, V.height_override(oid) or V.building_height(t), 0.0, *V.wall_roof_ids(t, oid)))
    note("buildings", len(parents), "parts", len(parts), "palette remaps", len(FrozenPalette.remapped))

    trees = [(p.x, p.y, t) for oid, t, p in osm.nodes if t.get("natural") == "tree"]
    for oid, typ, t, geom in osm.areas:
        if t.get("natural") == "tree":
            c = geom.centroid
            trees.append((c.x, c.y, t))
        elif t.get("natural") in ("wood", "forest") or t.get("landuse") == "forest":
            minx, miny, maxx, maxy = geom.bounds
            rng = np.random.default_rng(oid % (2 ** 32))
            n = max(1, int(geom.area / 80))
            for _ in range(min(n, 40)):
                x = rng.uniform(minx, maxx)
                y = rng.uniform(miny, maxy)
                if geom.contains(Point(x, y)):
                    trees.append((x, y, t))
    note("trees", len(trees))
    tree_xy = np.array([(x, y) for x, y, _ in trees]) if trees else np.zeros((0, 2))

    part_index = STRtree([p[2] for p in parts]) if parts else None
    parent_index = STRtree([p[2] for p in parents]) if parents else None
    bridge_index = STRtree([p[0] for p in bridge_shapes]) if bridge_shapes else None

    def q(tree, env):
        return [] if tree is None else [int(h) for h in np.atleast_1d(tree.query(env))]

    def fill_buildings(vol, gwin, c0, r0):
        rows = cols = N + 2
        env = box(c0 - 1, r0 - 1, c0 + cols + 1, r0 + rows + 1)
        tr = Affine.translation(c0, r0)
        Hm = vol.shape[0] - 1
        pids = q(part_index, env)
        has_part = np.zeros((rows, cols), bool)
        if pids:
            has_part = V.burn([(parts[i][2], 1) for i in pids], (rows, cols), tr, dtype=np.uint8, all_touched=True) == 1
        bids = q(parent_index, env)
        bids.sort(key=lambda i: -parents[i][2].area)
        for i in bids:
            oid, t, geom, h, z0, wid, rid = parents[i]
            mm = (V.burn([(geom, 1)], (rows, cols), tr, dtype=np.uint8, all_touched=True) == 1) & ~has_part
            if not mm.any():
                continue
            rs, cs = np.nonzero(mm)
            gg = gwin[rs, cs]
            fill_cols(vol, rs, cs, gg + int(round(z0)), np.minimum((gg + h).astype(np.int64), Hm), wid, rid)
        pids.sort(key=lambda i: (parts[i][4], parts[i][3]))
        for i in pids:
            oid, t, geom, h, z0, wid, rid = parts[i]
            mm = V.burn([(geom, 1)], (rows, cols), tr, dtype=np.uint8, all_touched=True) == 1
            if not mm.any():
                continue
            rs, cs = np.nonzero(mm)
            gg = gwin[rs, cs]
            fill_cols(vol, rs, cs, gg + int(round(z0)), np.minimum((gg + h).astype(np.int64), Hm), wid, rid)
        for i in q(bridge_index, env):
            poly, pid, deck = bridge_shapes[i]
            mm = V.burn([(poly, 1)], (rows, cols), tr, dtype=np.uint8, all_touched=True) == 1
            rs, cs = np.nonzero(mm)
            z0 = (gwin[rs, cs] + deck).astype(np.int64)
            ok = (z0 >= 1) & (z0 < vol.shape[0] - 1)
            vol[z0[ok], rs[ok], cs[ok]] = pid

    def fill_trees(vol, gwin, c0, r0):
        rows = cols = N + 2
        if not len(tree_xy):
            return
        sel = np.nonzero((tree_xy[:, 0] >= c0 - 6) & (tree_xy[:, 0] < c0 + cols + 6) &
                         (tree_xy[:, 1] >= r0 - 6) & (tree_xy[:, 1] < r0 + rows + 6))[0]
        for k in sel:
            x, y, t = trees[k]
            h = V.parse_len(t.get("height")) or (6 + V.stable_hash(int(x), int(y)) % 6)
            h = int(max(4, min(h, 22)))
            cr = max(1, int(round((V.parse_len(t.get("diameter_crown")) or h * 0.45) / 2)))
            cr = min(cr, 5)
            ci, rj = int(round(x - c0)), int(round(y - r0))
            if not (0 <= rj < rows and 0 <= ci < cols):
                continue
            gz = int(gwin[rj, ci])
            leaf = LEAVES[V.stable_hash(int(x), int(y), "l") % len(LEAVES)]
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

    fol_ids = set(LEAVES) | {C["trunk"]}
    grass_ids = {C["grass"], C["grass2"], C["garden"]}
    # also treat every leaf / grass material entry as foliage (matches foliage.py palette_ids)
    for i, e in enumerate(pal.entries):
        if int(e[3]) == V.M_LEAF:
            fol_ids.add(i)
    bare_id = C["bare"]
    is_fol = np.zeros(65536, bool); is_fol[list(fol_ids)] = True; is_fol[list(grass_ids)] = True
    is_grass = np.zeros(65536, bool); is_grass[list(grass_ids)] = True

    H8 = -(-HMAX // 8)
    W8, D8 = RW // 8, RD // 8
    vol8 = np.zeros((H8, D8, W8), np.uint16)
    g8 = np.clip(g[::8, ::8], 0, 255).astype(np.uint8)

    tx0, tx1, ty0, ty1 = X0 // N, X1 // N, Y0 // N, Y1 // N
    log = {"written_1": 0, "skipped_existing_1": 0, "neighbor_apron_cols": 0}
    for ty in range(ty0, ty1):
        # neighbour node for exact -x apron
        nb_vol = None
        for d in ("1", "1f"):
            p = OUT / "tiles" / d / f"{tx0 - 1}_{ty}.bin"
            if p.exists():
                cnt, _gw, runs = T.load_node(p)
                hh = int((runs[:, 0] + runs[:, 1]).max()) if len(runs) else 1
                v = vol_from_runs_fast(cnt, runs, min(max(hh, 1), HMAX))[:, :, 128]
                if nb_vol is None:
                    nb_vol = np.zeros((HMAX, N + 2), np.uint16)
                nb_vol[:v.shape[0]] = np.where(v != 0, v, nb_vol[:v.shape[0]])
        if nb_vol is not None:
            log["neighbor_apron_cols"] += 1
        for tx in range(tx0, tx1):
            assert in_band(tx, ty, 1)
            p1 = OUT / "tiles" / "1" / f"{tx}_{ty}.bin"
            pf = OUT / "tiles" / "1f" / f"{tx}_{ty}.bin"
            if p1.exists():
                log["skipped_existing_1"] += 1
                continue
            r0, c0 = ty * N - 1, tx * N - 1
            rows = cols = N + 2
            gwin = np.zeros((rows, cols), np.int32)
            swin = np.full((rows, cols), C["bare"], np.uint16)
            ya, yb = max(r0, Y0), min(r0 + rows, Y1)
            xa, xb = max(c0, X0), min(c0 + cols, X1)
            gwin[ya - r0:yb - r0, xa - c0:xb - c0] = g[ya - Y0:yb - Y0, xa - X0:xb - X0]
            swin[ya - r0:yb - r0, xa - c0:xb - c0] = surf[ya - Y0:yb - Y0, xa - X0:xb - X0]
            if tx == tx0 and nb_vol is not None:
                # neighbour ground for the apron column (exact)
                gwin[:, 0] = 0
                p = OUT / "tiles" / "1" / f"{tx0 - 1}_{ty}.bin"
                _, gwn, _ = T.load_node(p)
                gwin[:, 0] = gwn[:, 128]
            vol = np.zeros((HMAX, rows, cols), np.uint16)
            V.C = C
            V.fill_ground_fast(vol, gwin, swin)
            fill_buildings(vol, gwin, c0, r0)
            fill_trees(vol, gwin, c0, r0)
            if tx == tx0 and nb_vol is not None:
                vol[:, :, 0] = nb_vol
                log["neighbor_apron_cols"] += 0
            core = vol[:, 1:1 + N, 1:1 + N]
            ds = T.downsample(core, 8)
            r8, c8 = (ty * N - Y0) // 8, (tx * N - X0) // 8
            h8, d8, w8 = ds.shape
            vol8[:h8, r8:r8 + d8, c8:c8 + w8] = ds
            top = int(np.nonzero(vol.reshape(HMAX, -1).any(1))[0].max()) + 1 if vol.any() else 1
            win = vol[:top]
            f = is_fol[win]
            fol = np.where(f, win, 0).astype(np.uint16)
            win = np.where(f, np.where(is_grass[win], bare_id, 0), win).astype(np.uint16)
            gw = np.clip(gwin, 0, 255).astype(np.uint8)
            fc, fr = T.column_runs(fol)
            if not pf.exists():
                T.write_node(pf, fc, gw, fr)
            counts, runs = T.column_runs(win)
            T.write_node(p1, counts, gw, runs)
            log["written_1"] += 1
        note(f"1 m row ty={ty} ({ty - ty0 + 1}/{ty1 - ty0}) written {log['written_1']}")

    # ---------------- 8 m ----------------
    top8 = int(np.nonzero(vol8.reshape(H8, -1).any(1))[0].max()) + 1 if vol8.any() else 1
    vol8 = vol8[:top8]
    S8 = N * 8
    n8 = 0
    for ty in range(Y0 // S8, Y1 // S8):
        nbc = None
        p = OUT / "tiles" / "8" / f"{X0 // S8 - 1}_{ty}.bin"
        if p.exists():
            cnt, gwn, runs = T.load_node(p)
            hh = int((runs[:, 0] + runs[:, 1]).max()) if len(runs) else 1
            nbc = (vol_from_runs_fast(cnt, runs, max(hh, 1))[:, :, 128], gwn[:, 128])
        for tx in range(X0 // S8, X1 // S8):
            assert in_band(tx, ty, 8)
            p8 = OUT / "tiles" / "8" / f"{tx}_{ty}.bin"
            if p8.exists():
                note("skip existing 8 m", tx, ty)
                continue
            r0 = ty * N - (Y0 // 8) - 1
            c0 = tx * N - (X0 // 8) - 1
            rows = cols = N + 2
            Hh = max(vol8.shape[0], nbc[0].shape[0] if (nbc is not None and tx == X0 // S8) else 0)
            win = np.zeros((Hh, rows, cols), np.uint16)
            gw = np.zeros((rows, cols), np.uint8)
            rr0, rr1 = max(r0, 0), min(r0 + rows, D8)
            cc0, cc1 = max(c0, 0), min(c0 + cols, W8)
            win[:vol8.shape[0], rr0 - r0:rr1 - r0, cc0 - c0:cc1 - c0] = vol8[:, rr0:rr1, cc0:cc1]
            gw[rr0 - r0:rr1 - r0, cc0 - c0:cc1 - c0] = g8[rr0:rr1, cc0:cc1]
            if nbc is not None and tx == X0 // S8:
                win[:nbc[0].shape[0], :, 0] = nbc[0]
                gw[:, 0] = nbc[1]
            top = int(np.nonzero(win.reshape(win.shape[0], -1).any(1))[0].max()) + 1 if win.any() else 1
            counts, runs = T.column_runs(win[:top])
            T.write_node(p8, counts, gw, runs)
            n8 += 1
    note("8 m written", n8)
    log["written_8"] = n8
    log["palette_remaps"] = {str(k): v for k, v in FrozenPalette.remapped.items()}
    (WORK / "voxelize_summary.json").write_text(json.dumps(log, indent=1))
    from PIL import Image
    rgb_lut = np.array([e[:3] for e in pal.entries] + [(0, 0, 0)] * (65536 - len(pal.entries)), np.uint8)
    Image.fromarray(rgb_lut[surf[::4, ::4]]).save(WORK / "topdown_N3.jpg", quality=85)
    note("done", log)


if __name__ == "__main__":
    main()
