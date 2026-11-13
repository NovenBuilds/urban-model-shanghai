"""FOR THE INTEGRATOR (band N does not run this on accepted tiles).
Copy the x=10240 voxel column of band N nodes (1 m tx=80 col 1, 8 m tx=10 col 1) into the
+x apron column (col 129) of the accepted nodes tiles/{1,1f}/79_ty.bin and tiles/8/9_ty.bin,
ground byte included, so face culling at x=10240 is exact from both sides.
usage: python scripts/band_N_seam_patch.py [--out DIR]   (default writes in place; --out for a dry run)
"""
import argparse, sys
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import tiles as T
from band_N_voxelize import vol_from_runs_fast

ap = argparse.ArgumentParser(); ap.add_argument("--out", default=None); a = ap.parse_args()
SRC = ROOT / "web/public/data/tiles"
DST = Path(a.out) if a.out else SRC
n = 0
for d, txa, txb, tys in (("1", 79, 80, range(-56, 64)), ("1f", 79, 80, range(-56, 64)), ("8", 9, 10, range(-7, 8))):
    for ty in tys:
        pa, pb = SRC / d / f"{txa}_{ty}.bin", SRC / d / f"{txb}_{ty}.bin"
        if not (pa.exists() and pb.exists()):
            continue
        ca, ga, ra = T.load_node(pa); cb, gb, rb = T.load_node(pb)
        ha = int((ra[:, 0] + ra[:, 1]).max()) if len(ra) else 1
        hb = int((rb[:, 0] + rb[:, 1]).max()) if len(rb) else 1
        h = max(ha, hb, 1)
        va = vol_from_runs_fast(ca, ra, h); vb = vol_from_runs_fast(cb, rb, h)
        va[:, :, 129] = vb[:, :, 1]; ga[:, 129] = gb[:, 1]
        top = int(np.nonzero(va.reshape(h, -1).any(1))[0].max()) + 1 if va.any() else 1
        c2, r2 = T.column_runs(va[:top])
        T.write_node(DST / d / pa.name, c2, ga, r2); n += 1
print("patched", n, "nodes into", DST)
