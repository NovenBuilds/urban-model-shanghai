"""N2|EN2 seam: run by worker-N (owns both bands). Copies band EN2 y=8192 row into N2 +y apron.
"""
import argparse, sys
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import tiles as T
from band_EN2_voxelize import vol_from_runs_fast

ap = argparse.ArgumentParser(); ap.add_argument("--out", default=None); a = ap.parse_args()
SRC = ROOT / "web/public/data/tiles"
DST = Path(a.out) if a.out else SRC
n = 0
# (dir, old ty, new ty, tx range)
for d, tya, tyb, txs in (("1", 63, 64, range(112, 144)), ("1f", 63, 64, range(112, 144)), ("8", 7, 8, range(14, 18))):
    for tx in txs:
        pa, pb = SRC / d / f"{tx}_{tya}.bin", SRC / d / f"{tx}_{tyb}.bin"
        if not (pa.exists() and pb.exists()):
            continue
        ca, ga, ra = T.load_node(pa); cb, gb, rb = T.load_node(pb)
        ha = int((ra[:, 0] + ra[:, 1]).max()) if len(ra) else 1
        hb = int((rb[:, 0] + rb[:, 1]).max()) if len(rb) else 1
        h = max(ha, hb, 1)
        va = vol_from_runs_fast(ca, ra, h); vb = vol_from_runs_fast(cb, rb, h)
        va[:, 129, :] = vb[:, 1, :]; ga[129, :] = gb[1, :]
        top = int(np.nonzero(va.reshape(h, -1).any(1))[0].max()) + 1 if va.any() else 1
        c2, r2 = T.column_runs(va[:top])
        T.write_node(DST / d / pa.name, c2, ga, r2); n += 1
print("patched", n, "nodes into", DST)
