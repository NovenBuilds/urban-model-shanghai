"""Close a worker-S east|west seam both ways (worker-S must own both bands).

Copies the voxel column and ground byte at x=X1-1 from the west band's last core
column (col 128) into the east band's -x apron (col 0), and the column at x=X1
from the east band's first core (col 1) into the west band's +x apron (col 129).
Applies to tiles/1, tiles/1f and tiles/8. Does not touch tile cores, meta.json,
ground_all.npz, foliage.bin, or any typ ty outside both bands' ty range.

Default: S2 | S3 at x=-10240 (tx_east=-80, tx_west=-81; 8 m -10 / -11).
Do not pass typ ty < -56 — that is WX / other workers.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import tiles as T  # noqa: E402

SRC = ROOT / "web" / "public" / "data" / "tiles"

# east_band is the already-built one on the +x side (larger x); west_band is the new -x strip
SEAMS = {
    "S|S2":  dict(east="S",  west="S2", x1=-6144,  tx_e=-48, tx_w=-49, t8_e=-6,  t8_w=-7,  ty0=-56, ty1=64, ty80=-7, ty81=8),
    "S2|S3": dict(east="S2", west="S3", x1=-10240, tx_e=-80, tx_w=-81, t8_e=-10, t8_w=-11, ty0=-56, ty1=64, ty80=-7, ty81=8),
}


def patch_pair(kind: str, tx_e: int, tx_w: int, tys, stats):
    n = 0
    for ty in tys:
        if ty < -56:
            raise RuntimeError(f"refusing typ ty={ty} < -56 (WX / other workers)")
        pa, pb = SRC / kind / f"{tx_e}_{ty}.bin", SRC / kind / f"{tx_w}_{ty}.bin"
        if not pa.exists() or not pb.exists():
            stats["missing"] += 1
            stats["missing_names"].append(f"{kind}/{tx_e}_{ty} or {tx_w}_{ty}")
            continue
        ca, ga, ra = T.load_node(pa)
        cb, gb, rb = T.load_node(pb)
        ha = int((ra[:, 0] + ra[:, 1]).max()) if len(ra) else 1
        hb = int((rb[:, 0] + rb[:, 1]).max()) if len(rb) else 1
        h = max(ha, hb, 1)
        va = T.vol_from_runs(ca, ra, h)
        vb = T.vol_from_runs(cb, rb, h)
        # east band west apron col 0 (x=X1-1) <- west band last core col 128
        va[:, :, 0] = vb[:, :, 128]
        ga[:, 0] = gb[:, 128]
        # west band east apron col 129 (x=X1) <- east band first core col 1
        vb[:, :, 129] = va[:, :, 1]
        gb[:, 129] = ga[:, 1]
        ta = int(np.nonzero(va.reshape(h, -1).any(1))[0].max()) + 1 if va.any() else 1
        tb = int(np.nonzero(vb.reshape(h, -1).any(1))[0].max()) + 1 if vb.any() else 1
        c2, r2 = T.column_runs(va[:ta])
        T.write_node(pa, c2, ga, r2)
        c3, r3 = T.column_runs(vb[:tb])
        T.write_node(pb, c3, gb, r3)
        n += 2
    stats["written"] += n
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seam", default="S2|S3", choices=sorted(SEAMS))
    a = ap.parse_args()
    sp = SEAMS[a.seam]
    work = ROOT / "work" / sp["west"]
    work.mkdir(parents=True, exist_ok=True)
    stats = {"written": 0, "missing": 0, "missing_names": []}
    n1 = patch_pair("1", sp["tx_e"], sp["tx_w"], range(sp["ty0"], sp["ty1"]), stats)
    nf = patch_pair("1f", sp["tx_e"], sp["tx_w"], range(sp["ty0"], sp["ty1"]), stats)
    n8 = patch_pair("8", sp["t8_e"], sp["t8_w"], range(sp["ty80"], sp["ty81"]), stats)
    print(f"{a.seam}: patched 1m {n1} files, 1f {nf}, 8m {n8}; missing {stats['missing']}")
    json.dump({"seam": a.seam, "x1": sp["x1"], "patched_1m": n1, "patched_1f": nf, "patched_8m": n8,
               "missing": stats["missing"], "missing_names": stats["missing_names"],
               "ty_range_1m": [sp["ty0"], sp["ty1"] - 1], "note": "did not touch typ ty<-56"},
              open(work / f"seam_patch_{sp['east']}_{sp['west']}.json", "w"), indent=2)


if __name__ == "__main__":
    main()
