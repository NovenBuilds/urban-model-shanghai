"""Schematic tree crowns and a 2 m foliage class map for the viewer.

OSM tree / tree_row / woodland plus typological planting in parks (jittered
grid, not a tree survey, not LiDAR crowns). Crowns are written only into
tiles/1f so accepted 1 m city tiles are not rebuilt.

foliage.bin is gzip of uint8[RH/2, RW/2] class + uint8 habit, origin (x0, y0).
phenology.json is typological Shanghai / Jiangnan street-tree timing, not a
counted facade or crown survey.
"""
from __future__ import annotations

import gzip
import json
import math
from pathlib import Path

import numpy as np
from affine import Affine
from rasterio.features import rasterize

import tiles as T

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "public" / "data"
META = OUT / "meta.json"
RAW = ROOT / "data" / "raw"

# typological classes (ids 1–15; shader remaps 0 -> 13)
# leaf/colour/fall are day-of-year. Evergreen: no phases (shader keeps leaves).
PHENOLOGY = {
    "source": (
        "Typological timing for common Shanghai / Jiangnan urban trees "
        "(French plane, ginkgo, camphor, willow, pine). Not a counted Shanghai "
        "phenology survey and not LiDAR crowns."
    ),
    "city": {"leaf": 85, "colour": 318, "fall": 340},
    "leaf": [0.42, 0.55, 0.22],
    "grass": [
        [0.35, 0.38, 0.22], [0.38, 0.42, 0.24], [0.40, 0.50, 0.26],
        [0.36, 0.52, 0.24], [0.34, 0.50, 0.22], [0.32, 0.48, 0.20],
        [0.30, 0.46, 0.18], [0.32, 0.46, 0.18], [0.34, 0.44, 0.20],
        [0.36, 0.42, 0.20], [0.38, 0.40, 0.22], [0.36, 0.38, 0.22],
    ],
    "classes": {
        # French plane / deciduous street trees (late-March flush, Nov colour)
        "1": {"species": "Platanus (street, typological)", "leaf": {"day": 85, "sd": 10, "n": 0},
              "colour": {"day": 318, "sd": 8, "n": 0}, "fall": {"day": 340, "sd": 10, "n": 0}, "autumn": "#c47a2a"},
        "2": {"species": "Platanus (park, typological)", "leaf": {"day": 88, "sd": 10, "n": 0},
              "colour": {"day": 320, "sd": 8, "n": 0}, "fall": {"day": 342, "sd": 10, "n": 0}, "autumn": "#c98930"},
        "3": {"species": "deciduous mixed (typological)", "leaf": {"day": 82, "sd": 12, "n": 0},
              "colour": {"day": 315, "sd": 9, "n": 0}, "fall": {"day": 338, "sd": 11, "n": 0}, "autumn": "#b86a24"},
        "4": {"species": "deciduous late (typological)", "leaf": {"day": 92, "sd": 10, "n": 0},
              "colour": {"day": 322, "sd": 8, "n": 0}, "fall": {"day": 345, "sd": 9, "n": 0}, "autumn": "#d0923a"},
        # Ginkgo: earlier gold
        "5": {"species": "Ginkgo (typological)", "leaf": {"day": 90, "sd": 8, "n": 0},
              "colour": {"day": 300, "sd": 7, "n": 0}, "fall": {"day": 322, "sd": 8, "n": 0}, "autumn": "#e0b030"},
        "6": {"species": "Ginkgo late (typological)", "leaf": {"day": 94, "sd": 8, "n": 0},
              "colour": {"day": 305, "sd": 7, "n": 0}, "fall": {"day": 326, "sd": 8, "n": 0}, "autumn": "#e8bc40"},
        # Evergreen camphor / camphor-like
        "7": {"species": "Cinnamomum camphora (evergreen, typological)", "leaf": None, "colour": None, "fall": None},
        "8": {"species": "broadleaf evergreen (typological)", "leaf": None, "colour": None, "fall": None},
        # Willow / water edge
        "9": {"species": "Salix (typological)", "leaf": {"day": 70, "sd": 10, "n": 0},
              "colour": {"day": 310, "sd": 10, "n": 0}, "fall": {"day": 330, "sd": 10, "n": 0}, "autumn": "#c8a040"},
        "10": {"species": "riparian deciduous (typological)", "leaf": {"day": 74, "sd": 10, "n": 0},
               "colour": {"day": 312, "sd": 10, "n": 0}, "fall": {"day": 332, "sd": 10, "n": 0}, "autumn": "#c89838"},
        # Conifer evergreen
        "11": {"species": "pine / cedar (evergreen, typological)", "leaf": None, "colour": None, "fall": None},
        "12": {"species": "conifer (evergreen, typological)", "leaf": None, "colour": None, "fall": None},
        # default deciduous (shader uses 13 when class is 0)
        "13": {"species": "default deciduous (typological)", "leaf": {"day": 85, "sd": 10, "n": 0},
               "colour": {"day": 318, "sd": 8, "n": 0}, "fall": {"day": 340, "sd": 10, "n": 0}, "autumn": "#c47a2a"},
        "14": {"species": "bamboo / shrub (evergreen, typological)", "leaf": None, "colour": None, "fall": None},
        "15": {"species": "evergreen mixed (typological)", "leaf": None, "colour": None, "fall": None},
    },
}


def _hash(x, y, salt=0):
    return (int(x) * 374761393 + int(y) * 668265263 + salt * 2654435761) & 0xFFFFFFFF


def parse_len(v):
    if v is None:
        return None
    try:
        return float(str(v).split()[0].replace(",", ".").replace("m", ""))
    except (ValueError, TypeError):
        return None


def extent(meta):
    x0 = int(meta.get("x0", 0))
    y0 = int(meta.get("y0", 0))
    W, D = int(meta["W"]), int(meta["D"])
    return x0, y0, W, D


def palette_ids(meta):
    leaves, trunks, grasses = [], [], []
    for i, e in enumerate(meta["palette"]):
        mat = int(e[3])
        if mat == 3:
            leaves.append(i)
        elif mat == 8:
            grasses.append(i)
        # trunk: brown-ish solid or untyped brown
        r, g, b = e[:3]
        if mat in (0, 1) and 70 <= r <= 140 and 40 <= g <= 100 and b < g <= r and r - b >= 20:
            trunks.append(i)
    # known first-window ids as fallback
    if not leaves:
        leaves = list(range(31, 38))
    if not trunks:
        trunks = [19]
    return leaves, trunks, grasses


def collect_trees(osm, x0, y0, W, D, bmask, wmask):
    """Return list of (x, y, height, crown_r, class_id). Schematic, origin-aware coverage."""
    trees = []
    seen = set()

    def add(x, y, h, cr, cls):
        xi, yi = int(round(x)), int(round(y))
        if not (x0 <= xi < W and y0 <= yi < D):
            return
        r, c = yi - y0, xi - x0
        if wmask[r, c] or bmask[r, c]:
            return
        key = (xi, yi)
        if key in seen:
            return
        seen.add(key)
        trees.append((float(xi) + 0.5, float(yi) + 0.5, int(h), int(cr), int(cls)))

    # 1) mapped OSM trees
    for oid, t, p in osm.nodes:
        if t.get("natural") != "tree":
            continue
        h = parse_len(t.get("height")) or (8 + _hash(p.x, p.y, 1) % 8)
        h = max(4, min(int(h), 22))
        cr = parse_len(t.get("diameter_crown"))
        cr = max(2, min(int(round((cr or h * 0.5) / 2)), 6))
        sp = (t.get("species") or t.get("genus") or t.get("leaf_type") or "").lower()
        if "ginkgo" in sp or "银杏" in (t.get("species") or ""):
            cls = 5 + _hash(p.x, p.y, 2) % 2
        elif "camphor" in sp or "cinnamomum" in sp or "香樟" in (t.get("species") or "") or t.get("leaf_cycle") == "evergreen":
            cls = 7
        elif "salix" in sp or "willow" in sp or "柳" in (t.get("species") or ""):
            cls = 9
        elif "pinus" in sp or "pine" in sp:
            cls = 11
        else:
            cls = 1 + _hash(p.x, p.y, 3) % 4
        add(p.x, p.y, h, cr, cls)

    # 2) tree rows
    for oid, t, line in osm.lines:
        if t.get("natural") != "tree_row" and t.get("barrier") != "hedge":
            continue
        hedge = t.get("barrier") == "hedge"
        step = 4.0 if hedge else 7.0
        try:
            L = line.length
        except Exception:
            continue
        n = max(1, int(L // step))
        h = 3 + _hash(oid, 0, 4) % 3 if hedge else 8 + _hash(oid, 0, 4) % 6
        cr = 2 if hedge else 3 + _hash(oid, 0, 5) % 3
        cls = 14 if hedge else 1 + _hash(oid, 0, 6) % 4
        for i in range(n + 1):
            p = line.interpolate(0 if n == 0 else i / n, normalized=True)
            add(p.x, p.y, h, cr, cls)

    # 3) typological planting in parks, gardens, woods
    from shapely.geometry import Point
    for oid, typ, t, geom in osm.areas:
        lu = t.get("leisure") or t.get("landuse") or t.get("natural")
        if lu in ("wood", "forest", "scrub"):
            spacing, h0, cr0, cls0 = 7.0, 10, 4, 8
        elif lu in ("park", "garden", "village_green", "recreation_ground"):
            spacing, h0, cr0, cls0 = 10.0, 9, 4, 2
        else:
            continue
        if geom is None or geom.is_empty or geom.area < 200:
            continue
        minx, miny, maxx, maxy = geom.bounds
        # jittered grid
        x = math.floor(minx / spacing) * spacing
        while x <= maxx:
            y = math.floor(miny / spacing) * spacing
            while y <= maxy:
                jx = x + ((_hash(x, y, oid) % 1000) / 1000.0 - 0.5) * spacing * 0.6
                jy = y + ((_hash(x, y, oid + 17) % 1000) / 1000.0 - 0.5) * spacing * 0.6
                if geom.contains(Point(jx, jy)):
                    h = h0 + _hash(jx, jy, 8) % 6
                    cr = cr0 + _hash(jx, jy, 9) % 3
                    # mix in some evergreens in parks
                    if lu in ("park", "garden") and _hash(jx, jy, 10) % 5 == 0:
                        cls = 7
                    elif lu in ("wood", "forest") and _hash(jx, jy, 10) % 3 == 0:
                        cls = 11
                    else:
                        cls = cls0 if lu not in ("park", "garden") else (2 + _hash(jx, jy, 11) % 3)
                    add(jx, jy, h, min(cr, 6), cls)
                y += spacing
            x += spacing

    return trees


def stamp_tree(vol, gwin, c0, r0, x, y, h, cr, leaf_id, trunk_id):
    rows, cols = vol.shape[1], vol.shape[2]
    ci, rj = int(round(x - c0)), int(round(y - r0))
    if not (0 <= ci < cols and 0 <= rj < rows):
        # crown may still overlap
        if not (-cr <= ci < cols + cr and -cr <= rj < rows + cr):
            return 0
        gz = None
        for dj, di in ((0, 0), (0, 1), (1, 0), (0, -1), (-1, 0)):
            cc, rr = ci + di, rj + dj
            if 0 <= cc < cols and 0 <= rr < rows:
                gz = int(gwin[rr, cc])
                break
        if gz is None:
            return 0
    else:
        gz = int(gwin[rj, ci])
    H = vol.shape[0]
    n = 0
    trunk_top = gz + max(1, h - cr)
    if 0 <= ci < cols and 0 <= rj < rows:
        z1 = min(H, trunk_top)
        if z1 > gz:
            vol[gz:z1, rj, ci] = trunk_id
            n += 1
    cz = gz + h - cr
    for dj in range(-cr, cr + 1):
        for di in range(-cr, cr + 1):
            if di * di + dj * dj > cr * cr + 1:
                continue
            cc, rr = ci + di, rj + dj
            if 0 <= cc < cols and 0 <= rr < rows:
                z0 = cz + (0 if di * di + dj * dj < (cr - 1) ** 2 else 1)
                z1 = min(H, cz + cr)
                if z1 > max(0, z0):
                    vol[max(0, z0):z1, rr, cc] = leaf_id
                    n += 1
    return n


def main(tx_max=None, tx_min=None, ty_min=None, ty_max=None):
    from osm import OSM

    meta = json.loads(META.read_text())
    x0, y0, W, D = extent(meta)
    RW, RH = W - x0, D - y0
    leaves, trunks, grasses = palette_ids(meta)
    print("palette leaves", leaves, "trunks", trunks[:6], "grasses", grasses)

    osm_path = RAW / "osm_expanded.json"
    if not osm_path.exists():
        osm_path = RAW / "osm.json"
    osm = OSM(str(osm_path))

    # building + water masks on the coverage raster (1 m)
    tr = Affine.translation(x0, y0)
    bld, water = [], []
    for oid, typ, t, g in osm.areas:
        if t.get("building") not in (None, "no") or "building:part" in t:
            if g is not None and not g.is_empty:
                bld.append(g)
        if t.get("natural") == "water" or t.get("waterway") in ("riverbank", "dock") or t.get("landuse") == "basin":
            if g is not None and not g.is_empty:
                water.append(g)
    bmask = rasterize([(g, 1) for g in bld], out_shape=(RH, RW), transform=tr, fill=0, dtype=np.uint8) if bld else np.zeros((RH, RW), np.uint8)
    wmask = rasterize([(g, 1) for g in water], out_shape=(RH, RW), transform=tr, fill=0, all_touched=True, dtype=np.uint8) if water else np.zeros((RH, RW), np.uint8)
    print("masks buildings", int(bmask.sum()), "water", int(wmask.sum()))

    trees = collect_trees(osm, x0, y0, W, D, bmask, wmask)
    print("trees", len(trees))

    # 2 m class + habit map; if x0/y0 grew, pad a previously accepted foliage.bin
    cw, ch = RW // 2, RH // 2
    cls = np.zeros((ch, cw), np.uint8)
    habit = np.zeros((ch, cw), np.uint8)
    old_fol = OUT / "foliage.bin"
    # previous accepted window was x0=0, y0=-5120, cw=2048, ch=4096
    if old_fol.exists():
        import gzip as _gz
        raw = _gz.decompress(old_fol.read_bytes())
        # try current size first, then the pre-south (x0=0) size
        candidates = [(ch, cw, x0, y0)]
        # previously accepted rasters
        if x0 < 0 or y0 < -5120:
            candidates.append(((7168 - (-7168))//2, (10240 - (-2048))//2, -2048, -7168))
            candidates.append(((7168 - (-7168))//2, (8192 - (-2048))//2, -2048, -7168))
            candidates.append(((6144 - (-7168))//2, (8192 - (-2048))//2, -2048, -7168))
            candidates.append(((5120 - (-7168))//2, (8192 - (-2048))//2, -2048, -7168))
            candidates.append(((3072 - (-7168))//2, (8192 - (-2048))//2, -2048, -7168))
            candidates.append(((D - (-7168))//2, (6144 - (-2048))//2, -2048, -7168))
            candidates.append(((D - (-7168))//2, (4096 - (-2048))//2, -2048, -7168))
            candidates.append(((D - (-5120))//2, (W - (-2048))//2, -2048, -5120))
        if x0 < 0:
            candidates.append(((D - y0)//2, (W - 0)//2, 0, y0))
        candidates.append(((D - (-5120))//2, (W - 0)//2, 0, -5120))
        for pch, pcw, px0, py0 in candidates:
            need = pcw * pch * 2
            if len(raw) == need:
                ocls = np.frombuffer(raw, np.uint8, count=pcw*pch).reshape(pch, pcw)
                ohab = np.frombuffer(raw, np.uint8, count=pcw*pch, offset=pcw*pch).reshape(pch, pcw)
                # dest row 0 = y0, src row 0 = py0; dest col 0 = x0, src col 0 = px0
                r0 = (py0 - y0) // 2
                c0 = (px0 - x0) // 2
                if r0 + pch <= ch and c0 + pcw <= cw and r0 >= 0 and c0 >= 0:
                    cls[r0:r0+pch, c0:c0+pcw] = ocls
                    habit[r0:r0+pch, c0:c0+pcw] = ohab
                    print("padded accepted foliage at", c0, r0, "from", px0, py0, ocls.shape)
                    break
    for x, y, h, cr, c in trees:
        u, v = int((x - x0) // 2), int((y - y0) // 2)
        if 0 <= u < cw and 0 <= v < ch:
            cls[v, u] = c
            habit[v, u] = 1 + (_hash(x, y, 21) % 255)
        # also paint a small disk so 2 m cells under the crown get a class
        rad = max(1, cr // 2)
        for dj in range(-rad, rad + 1):
            for di in range(-rad, rad + 1):
                uu, vv = u + di, v + dj
                if 0 <= uu < cw and 0 <= vv < ch and cls[vv, uu] == 0:
                    cls[vv, uu] = c
                    habit[vv, uu] = 1 + (_hash(uu, vv, 22) % 255)

    # typological class for grass cells in parks without a tree (for seasonal grass / litter)
    # already zero -> shader uses class 13, which is deciduous; evergreen parks look better as 7/14
    # skip: grass tint comes from uGrass monthly, class 0 is fine

    (OUT / "foliage.bin").write_bytes(gzip.compress(cls.tobytes() + habit.tobytes(), mtime=0, compresslevel=9))
    json.dump(PHENOLOGY, open(OUT / "phenology.json", "w"), ensure_ascii=False, indent=2)
    print("wrote foliage.bin", (OUT / "foliage.bin").stat().st_size, "cls>0", int((cls > 0).sum()),
          "shape", cls.shape, "y0", y0)

    # restamp 1f tiles that intersect a tree (do not touch 1 m city tiles)
    # index trees by 128 m tile
    by_tile = {}
    for rec in trees:
        x, y = rec[0], rec[1]
        cr = rec[3]
        for ty in range(int(math.floor((y - cr) / T.N)), int(math.floor((y + cr) / T.N)) + 1):
            for tx in range(int(math.floor((x - cr) / T.N)), int(math.floor((x + cr) / T.N)) + 1):
                by_tile.setdefault((tx, ty), []).append(rec)

    fol_dir = OUT / "tiles" / "1f"
    ground_all = None
    gp = ROOT / "data" / "ground_all.npz"
    base = int(meta.get("base", -9))
    if gp.exists():
        z = np.load(gp)
        ground_all = z["ground"].astype(np.float32)
        gy0 = int(z["y0"]) if "y0" in z.files else y0

    if tx_max is not None or tx_min is not None or ty_min is not None or ty_max is not None:
        kept = {k: v for k, v in by_tile.items()
                if (tx_max is None or k[0] <= tx_max)
                and (tx_min is None or k[0] >= tx_min)
                and (ty_min is None or k[1] >= ty_min)
                and (ty_max is None or k[1] <= ty_max)}
        print("1f filter tx", tx_min, "..", tx_max, "ty", ty_min, "..", ty_max, "tiles", len(kept), "of", len(by_tile))
        by_tile = kept

    n_tiles = n_new = n_stamp = 0
    for (tx, ty), recs in sorted(by_tile.items()):
        path = fol_dir / f"{tx}_{ty}.bin"
        r0, c0 = ty * T.N - 1, tx * T.N - 1  # apron origin
        rows = cols = T.N + 2
        if path.exists():
            counts, gwin, runs = T.load_node(path)
            # leave room for crowns above existing grass
            hmax = 1
            if len(runs):
                hmax = int((runs[:, 0] + runs[:, 1]).max())
            for _x, _y, h, cr, _c in recs:
                hmax = max(hmax, 40, h + 4)
            vol = T.vol_from_runs(counts, runs, h=hmax)
        else:
            gwin = np.zeros((rows, cols), np.uint8)
            hmax = 40
            vol = np.zeros((hmax, rows, cols), np.uint16)
            # fill ground from ground_all
            if ground_all is not None:
                for rr in range(rows):
                    yy = r0 + rr
                    if gy0 <= yy < gy0 + ground_all.shape[0]:
                        for cc in range(cols):
                            xx = c0 + cc
                            if 0 <= xx < ground_all.shape[1]:
                                gwin[rr, cc] = np.clip(np.round(ground_all[yy - gy0, xx] - base), 1, 250)
            n_new += 1

        # if existing gwin is all zero, try ground_all
        if int(gwin.max()) == 0 and ground_all is not None:
            for rr in range(rows):
                yy = r0 + rr
                if gy0 <= yy < gy0 + ground_all.shape[0]:
                    for cc in range(cols):
                        xx = c0 + cc
                        if 0 <= xx < ground_all.shape[1]:
                            gwin[rr, cc] = np.clip(np.round(ground_all[yy - gy0, xx] - base), 1, 250)

        added = 0
        for x, y, h, cr, c in recs:
            leaf = leaves[_hash(x, y, 12) % len(leaves)]
            trunk = trunks[_hash(x, y, 13) % len(trunks)]
            added += stamp_tree(vol, gwin, c0, r0, x, y, h, cr, leaf, trunk)
        if added == 0 and path.exists():
            continue
        top = int(np.nonzero(vol.reshape(vol.shape[0], -1).any(1))[0].max()) + 1 if vol.any() else 1
        counts, runs = T.column_runs(vol[:top])
        T.write_node(path, counts, gwin, runs)
        n_tiles += 1
        n_stamp += added
        if n_tiles % 50 == 0:
            print(f"  1f {n_tiles} tiles, last {tx}_{ty}, stamps {n_stamp}")
    print("restamped 1f tiles", n_tiles, "new", n_new, "crown cells ~", n_stamp)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--tx-max", type=int, default=None, help="only restamp 1f tiles with tx <= this (e.g. -1 for a south band)")
    ap.add_argument("--tx-min", type=int, default=None)
    ap.add_argument("--ty-min", type=int, default=None)
    ap.add_argument("--ty-max", type=int, default=None)
    args = ap.parse_args()
    main(tx_max=args.tx_max, tx_min=args.tx_min, ty_min=args.ty_min, ty_max=args.ty_max)
