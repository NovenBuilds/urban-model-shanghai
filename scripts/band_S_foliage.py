"""Band S typological foliage (copy of foliage.py logic, band-local).

Writes crowns only into tiles/1f inside band S (tx -48..-17, ty -56..63) and a band-local 2 m class map
work/S/foliage_S.bin (gzip uint8 cls[ch,cw] + uint8 habit[ch,cw], origin x0=-6144, y0=-7168, 2 m cells) for the
integrator to paste into the shared foliage.bin. Does not touch foliage.bin / phenology.json / meta.json.
Typological planting, not a tree survey, not LiDAR.
"""
import gzip
import json
import math
import sys
from pathlib import Path

import numpy as np
from affine import Affine
from rasterio.features import rasterize

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import band_S_foliage_snapshot as F  # noqa: E402
import tiles as T  # noqa: E402
from osm import OSM  # noqa: E402

BANDS = {
    "S":  dict(x0=-6144,  x1=-2048, y0=-7168, y1=8192, osm="data/raw/S/osm_S.json"),
    "S2": dict(x0=-10240, x1=-6144, y0=-7168, y1=8192, osm="data/raw/S2/osm_S2.json"),
    "S3": dict(x0=-14336, x1=-10240, y0=-7168, y1=8192, osm="data/raw/S3/osm_S3.json"),
}
BAND = "S"
X0, X1, Y0, Y1 = -6144, -2048, -7168, 8192
M = 8
WORK = ROOT / "work" / "S"
OUT = ROOT / "web" / "public" / "data"
OSM_PATH = ROOT / "data" / "raw" / "S" / "osm_S.json"


def configure(band="S", x0=None, x1=None, y0=None, y1=None, osm=None, work=None):
    global BAND, X0, X1, Y0, Y1, WORK, OSM_PATH
    BAND = band
    spec = dict(BANDS.get(band, {}))
    X0 = int(x0 if x0 is not None else spec.get("x0", X0))
    X1 = int(x1 if x1 is not None else spec.get("x1", X1))
    Y0 = int(y0 if y0 is not None else spec.get("y0", Y0))
    Y1 = int(y1 if y1 is not None else spec.get("y1", Y1))
    WORK = Path(work) if work else ROOT / "work" / BAND
    WORK.mkdir(parents=True, exist_ok=True)
    OSM_PATH = Path(osm) if osm else ROOT / spec.get("osm", f"data/raw/{BAND}/osm_{BAND}.json")
    if not OSM_PATH.is_absolute():
        OSM_PATH = ROOT / OSM_PATH


def main():
    meta_path = WORK / "meta_snapshot.json"
    if not meta_path.exists():
        meta_path = ROOT / "work" / "S" / "meta_snapshot.json"
    meta = json.loads(meta_path.read_text())
    leaves, trunks, grasses = F.palette_ids(meta)
    print("palette leaves", leaves, "trunks", trunks[:6], "band", BAND)
    osm = OSM(str(OSM_PATH))
    ex0, ey0, ex1, ey1 = X0 - M, Y0 - M, X1 + M, Y1 + M
    RW, RH = ex1 - ex0, ey1 - ey0
    tr = Affine.translation(ex0, ey0)
    bld, water = [], []
    for oid, typ, t, g in osm.areas:
        if g is None or g.is_empty:
            continue
        if t.get("building") not in (None, "no") or "building:part" in t:
            bld.append(g)
        if t.get("natural") == "water" or t.get("waterway") in ("riverbank", "dock") or t.get("landuse") == "basin":
            water.append(g)
    bmask = rasterize([(g, 1) for g in bld], out_shape=(RH, RW), transform=tr, fill=0, dtype=np.uint8)
    wmask = rasterize([(g, 1) for g in water], out_shape=(RH, RW), transform=tr, fill=0, all_touched=True, dtype=np.uint8)
    trees = F.collect_trees(osm, ex0, ey0, ex1, ey1, bmask, wmask)
    del bmask, wmask
    print("trees", len(trees))

    cw, ch = (X1 - X0) // 2, (Y1 - Y0) // 2
    cls = np.zeros((ch, cw), np.uint8)
    habit = np.zeros((ch, cw), np.uint8)
    for x, y, h, cr, c in trees:
        u, v = int((x - X0) // 2), int((y - Y0) // 2)
        if 0 <= u < cw and 0 <= v < ch:
            cls[v, u] = c
            habit[v, u] = 1 + (F._hash(x, y, 21) % 255)
        rad = max(1, cr // 2)
        for dj in range(-rad, rad + 1):
            for di in range(-rad, rad + 1):
                uu, vv = u + di, v + dj
                if 0 <= uu < cw and 0 <= vv < ch and cls[vv, uu] == 0:
                    cls[vv, uu] = c
                    habit[vv, uu] = 1 + (F._hash(uu, vv, 22) % 255)
    (WORK / f"foliage_{BAND}.bin").write_bytes(gzip.compress(cls.tobytes() + habit.tobytes(), mtime=0, compresslevel=9))
    json.dump({"x0": X0, "y0": Y0, "cell_m": 2, "cw": cw, "ch": ch, "layout": "gzip(cls uint8[ch,cw] + habit uint8[ch,cw])",
               "cls_gt0": int((cls > 0).sum())}, open(WORK / f"foliage_{BAND}.json", "w"), indent=2)
    print(f"wrote foliage_{BAND}.bin cls>0", int((cls > 0).sum()))

    by_tile = {}
    for rec in trees:
        x, y, cr = rec[0], rec[1], rec[3]
        for ty in range(int(math.floor((y - cr) / T.N)), int(math.floor((y + cr) / T.N)) + 1):
            for tx in range(int(math.floor((x - cr) / T.N)), int(math.floor((x + cr) / T.N)) + 1):
                if X0 // T.N <= tx < X1 // T.N and Y0 // T.N <= ty < Y1 // T.N:
                    by_tile.setdefault((tx, ty), []).append(rec)
    fol_dir = OUT / "tiles" / "1f"
    n_tiles = n_stamp = 0
    for (tx, ty), recs in sorted(by_tile.items()):
        path = fol_dir / f"{tx}_{ty}.bin"
        if not path.exists():
            raise SystemExit(f"missing band-S 1f tile {path}; run band_S_voxelize.py first")
        counts, gwin, runs = T.load_node(path)
        hmax = int((runs[:, 0] + runs[:, 1]).max()) if len(runs) else 1
        for _x, _y, h, cr, _c in recs:
            hmax = max(hmax, 40, h + 4)
        vol = T.vol_from_runs(counts, runs, h=hmax)
        r0, c0 = ty * T.N - 1, tx * T.N - 1
        added = 0
        for x, y, h, cr, c in recs:
            leaf = leaves[F._hash(x, y, 12) % len(leaves)]
            trunk = trunks[F._hash(x, y, 13) % len(trunks)]
            added += F.stamp_tree(vol, gwin, c0, r0, x, y, h, cr, leaf, trunk)
        if added == 0:
            continue
        top = int(np.nonzero(vol.reshape(vol.shape[0], -1).any(1))[0].max()) + 1 if vol.any() else 1
        counts, runs = T.column_runs(vol[:top])
        T.write_node(path, counts, gwin, runs)
        n_tiles += 1
        n_stamp += added
        if n_tiles % 200 == 0:
            print(f"  1f {n_tiles} tiles, last {tx}_{ty}", flush=True)
    edge = sum(1 for x, y, h, cr, c in trees if x + cr >= X1 and x < X1)
    print(f"restamped band-{BAND} 1f tiles", n_tiles, "crown cells ~", n_stamp, f"crowns crossing x={X1} into tx={X1 // T.N}:", edge)
    json.dump({"restamped_1f": n_tiles, "crown_cells": n_stamp, "trees": len(trees), "crowns_crossing_x-2048": edge},
              open(WORK / f"foliage_{BAND}_summary.json", "w"), indent=2)


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
    a = ap.parse_args()
    configure(band=a.band, x0=a.x0, x1=a.x1, y0=a.y0, y1=a.y1, osm=a.osm, work=a.work)
    main()
