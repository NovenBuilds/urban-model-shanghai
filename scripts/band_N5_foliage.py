"""Band N5 (parametrized copy of band_N4_foliage.py) typological foliage: same rules as foliage.py (collect_trees / stamp_tree, same
hashes, same palette pick), restricted to band N. Restamps ONLY tiles/1f with
tx 208..239, ty -56..63. Does not write foliage.bin / phenology.json; instead writes the
band's 2 m class+habit map to work/N5/foliage_N5.bin for the integrator to paste.
Trees are collected from x>=26616 (8 m west margin) so crowns of trees just west of the
seam reach into tx=80 the same way the accepted side would draw them.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from affine import Affine
from rasterio.features import rasterize

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import foliage as F  # noqa: E402
import tiles as T  # noqa: E402
from osm import OSM  # noqa: E402

OUT = ROOT / "web" / "public" / "data"
WORK = ROOT / "work" / "N5"
X0, X1, Y0, Y1 = 26624, 30720, -7168, 8192
M = 8


def main():
    meta = json.loads((OUT / "meta.json").read_text())
    leaves, trunks, grasses = F.palette_ids(meta)
    print("palette leaves", leaves, "trunks", trunks[:6])
    osm = OSM(str(ROOT / "data" / "raw" / "N5" / "osm_N5.json"))
    ex0 = X0 - M
    RW, RH = X1 - ex0, Y1 - Y0
    tr = Affine.translation(ex0, Y0)
    bld = [g for oid, typ, t, g in osm.areas if (t.get("building") not in (None, "no") or "building:part" in t) and g is not None and not g.is_empty]
    water = [g for oid, typ, t, g in osm.areas if (t.get("natural") == "water" or t.get("waterway") in ("riverbank", "dock") or t.get("landuse") == "basin") and g is not None and not g.is_empty]
    import band_N2_sea
    sea = band_N2_sea.sea_polygon(osm, ex0, Y0, X1, Y1)
    if not sea.is_empty:
        water.append(sea)
    bmask = rasterize([(g, 1) for g in bld], out_shape=(RH, RW), transform=tr, fill=0, dtype=np.uint8) if bld else np.zeros((RH, RW), np.uint8)
    wmask = rasterize([(g, 1) for g in water], out_shape=(RH, RW), transform=tr, fill=0, all_touched=True, dtype=np.uint8) if water else np.zeros((RH, RW), np.uint8)
    trees = F.collect_trees(osm, ex0, Y0, X1, Y1, bmask, wmask)
    del bmask, wmask
    print("trees", len(trees))

    # 2 m class map, band only (origin x=26624, y=-7168)
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
    import gzip
    (WORK / "foliage_N5.bin").write_bytes(gzip.compress(cls.tobytes() + habit.tobytes(), mtime=0, compresslevel=9))
    (WORK / "foliage_N5.json").write_text(json.dumps({"x0": X0, "y0": Y0, "cw": cw, "ch": ch, "cell": 2,
        "format": "gzip uint8[ch,cw] class + uint8[ch,cw] habit"}))
    print("foliage cls>0", int((cls > 0).sum()))

    by_tile = {}
    spill_w = 0
    for rec in trees:
        x, y, cr = rec[0], rec[1], rec[3]
        for ty in range(int(math.floor((y - cr) / T.N)), int(math.floor((y + cr) / T.N)) + 1):
            for tx in range(int(math.floor((x - cr) / T.N)), int(math.floor((x + cr) / T.N)) + 1):
                if X0 // T.N <= tx < X1 // T.N and Y0 // T.N <= ty < Y1 // T.N:
                    by_tile.setdefault((tx, ty), []).append(rec)
                elif x >= X0:
                    spill_w += 1
    print("tiles with trees", len(by_tile), "crown spills outside band (left for integrator)", spill_w)
    n_tiles = n_stamp = 0
    for (tx, ty), recs in sorted(by_tile.items()):
        path = OUT / "tiles" / "1f" / f"{tx}_{ty}.bin"
        if not path.exists():
            raise SystemExit(f"missing band 1f {path}; run band_N5_voxelize first")
        counts, gwin, runs = T.load_node(path)
        hmax = int((runs[:, 0] + runs[:, 1]).max()) if len(runs) else 1
        hmax = max([hmax, 40] + [h + 4 for _x, _y, h, _cr, _c in recs])
        # need ground under crowns; 1f gwin carries the tile ground (apron incl.)
        vol = T.vol_from_runs(counts, runs, h=hmax)
        added = 0
        for x, y, h, cr, c in recs:
            leaf = leaves[F._hash(x, y, 12) % len(leaves)]
            trunk = trunks[F._hash(x, y, 13) % len(trunks)]
            added += F.stamp_tree(vol, gwin.astype(np.int32), tx * T.N - 1, ty * T.N - 1, x, y, h, cr, leaf, trunk)
        if added == 0:
            continue
        top = int(np.nonzero(vol.reshape(vol.shape[0], -1).any(1))[0].max()) + 1 if vol.any() else 1
        c2, r2 = T.column_runs(vol[:top])
        T.write_node(path, c2, gwin, r2)
        n_tiles += 1
        n_stamp += added
        if n_tiles % 100 == 0:
            print(f"  1f {n_tiles} tiles, last {tx}_{ty}", flush=True)
    print("restamped band N5 1f tiles", n_tiles, "crown cells ~", n_stamp)
    (WORK / "foliage_summary.json").write_text(json.dumps({"trees": len(trees), "tiles_restamped": n_tiles, "crown_cells": n_stamp, "spills_outside_band": spill_w}))


if __name__ == "__main__":
    main()
