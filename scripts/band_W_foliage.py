"""Band W copy of foliage.py: typological crowns restamped into tiles/1f ONLY for band W
(x=-2048..10240, y=-11264..-7168). Does NOT write foliage.bin / phenology.json (integrator owns them);
writes the band's 2 m class+habit map to work/W/foliage_W.bin instead (origin x0=-2048, y0=-11264).
Tree collection / stamping logic is imported unchanged from foliage.py.
"""
from __future__ import annotations

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
import tiles as T  # noqa: E402
import foliage as F  # noqa: E402
from osm import OSM  # noqa: E402

X0, X1, Y0, Y1 = -2048, 10240, -11264, -7168
WORK = ROOT / "work" / "W"
BANDS = {"W": (-2048, 10240, -11264, -7168), "W2": (-2048, 10240, -15360, -11264)}
OUT = ROOT / "web" / "public" / "data"


def main(osm_path, band="W", pad_n=0, pad_s=0, ty_only=None):
    global X0, X1, Y0, Y1, WORK
    X0, X1, Y0, Y1 = BANDS[band]
    WORK = ROOT / "work" / band
    meta = json.loads((OUT / "meta.json").read_text())
    leaves, trunks, grasses = F.palette_ids(meta)
    # trees are collected over the band padded into worker-W's own neighbouring band (pad_n: +y, pad_s: -y)
    # so crowns spilling across an owned seam land in this band's tiles; stamping stays in-band only.
    CY0, CY1 = Y0 - pad_s, Y1 + pad_n
    RW, RH = X1 - X0, CY1 - CY0
    osm = OSM(str(osm_path))
    tr = Affine.translation(X0, CY0)
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
    print("masks buildings", int(bmask.sum()), "water", int(wmask.sum()), flush=True)
    # collect_trees(osm, x0, y0, W, D, ...) uses W/D as exclusive max coords
    trees = F.collect_trees(osm, X0, CY0, X1, CY1, bmask, wmask)
    del bmask, wmask
    print("trees", len(trees), flush=True)

    cw, ch = RW // 2, (Y1 - Y0) // 2
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
    if ty_only is None:
        (WORK / f"foliage_{band}.bin").write_bytes(gzip.compress(cls.tobytes() + habit.tobytes(), mtime=0, compresslevel=9))
    (WORK / f"foliage_{band}.json").write_text(json.dumps({"x0": X0, "y0": Y0, "cw": cw, "ch": ch, "cell": 2,
                                                     "format": "gzip uint8[ch,cw] class + uint8[ch,cw] habit"}))
    print("wrote foliage map", band, cls.shape, "cls>0", int((cls > 0).sum()), flush=True)

    by_tile = {}
    tx_lo, tx_hi, ty_lo, ty_hi = X0 // T.N, X1 // T.N - 1, Y0 // T.N, Y1 // T.N - 1
    spill = 0
    for rec in trees:
        x, y, cr = rec[0], rec[1], rec[3]
        for ty in range(int(math.floor((y - cr) / T.N)), int(math.floor((y + cr) / T.N)) + 1):
            for tx in range(int(math.floor((x - cr) / T.N)), int(math.floor((x + cr) / T.N)) + 1):
                if tx_lo <= tx <= tx_hi and ty_lo <= ty <= ty_hi and (ty_only is None or ty in ty_only):
                    by_tile.setdefault((tx, ty), []).append(rec)
                else:
                    spill += 1
    print("band tiles with crowns", len(by_tile), "crown spills outside band (dropped)", spill, flush=True)
    fol_dir = OUT / "tiles" / "1f"
    n_tiles = n_stamp = 0
    for (tx, ty), recs in sorted(by_tile.items()):
        assert tx_lo <= tx <= tx_hi and ty_lo <= ty <= ty_hi and X0 <= tx * T.N and (tx + 1) * T.N <= X1 \
            and Y0 <= ty * T.N and (ty + 1) * T.N <= Y1
        path = fol_dir / f"{tx}_{ty}.bin"
        r0, c0 = ty * T.N - 1, tx * T.N - 1
        if not path.exists():
            print("WARN missing 1f (voxelize first)", path.name)
            continue
        counts, gwin, runs = T.load_node(path)
        hmax = 1
        if len(runs):
            hmax = int((runs[:, 0] + runs[:, 1]).max())
        for _x, _y, h, cr, _c in recs:
            hmax = max(hmax, 40, h + 4)
        vol = T.vol_from_runs(counts, runs, h=hmax)
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
        if n_tiles % 100 == 0:
            print(f"  1f {n_tiles} tiles, last {tx}_{ty}, stamps {n_stamp}", flush=True)
    print("restamped 1f tiles", n_tiles, "crown cells ~", n_stamp, flush=True)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("osm", nargs="?", default=str(ROOT / "data" / "raw" / "W" / "osm_W.json"))
    ap.add_argument("--band", default="W", choices=sorted(BANDS))
    ap.add_argument("--pad-n", type=int, default=0)
    ap.add_argument("--pad-s", type=int, default=0)
    ap.add_argument("--ty", type=int, action="append", default=None, help="restrict restamp to these ty rows")
    a = ap.parse_args()
    main(a.osm, a.band, a.pad_n, a.pad_s, set(a.ty) if a.ty else None)
