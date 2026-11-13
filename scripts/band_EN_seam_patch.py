"""N|EN and EN|EN2 seams: run by worker-N (owns N, EN, EN2).
1) Copy EN y=8192 row into N +y apron (ty=63 / 8 m ty=7).
2) Copy EN x=14335 column into EN2 -x apron (tx=112 / 8 m tx=14).
"""
import argparse, sys
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import tiles as T
from band_EN_voxelize import vol_from_runs_fast

ap = argparse.ArgumentParser(); ap.add_argument("--out", default=None); a = ap.parse_args()
SRC = ROOT / "web/public/data/tiles"
DST = Path(a.out) if a.out else SRC
n = 0

def load(p):
    c, g, r = T.load_node(p)
    h = int((r[:, 0] + r[:, 1]).max()) if len(r) else 1
    return c, g, r, max(h, 1)

# N +y apron: old typ ty=63, new typ ty=64, tx=80..111; 8 m typ ty=7 vs 8, tx=10..13
for d, tya, tyb, txs in (("1", 63, 64, range(80, 112)), ("1f", 63, 64, range(80, 112)), ("8", 7, 8, range(10, 14))):
    for tx in txs:
        pa, pb = SRC / d / f"{tx}_{tya}.bin", SRC / d / f"{tx}_{tyb}.bin"
        if not (pa.exists() and pb.exists()):
            continue
        ca, ga, ra, ha = load(pa); cb, gb, rb, hb = load(pb)
        h = max(ha, hb)
        va = vol_from_runs_fast(ca, ra, h); vb = vol_from_runs_fast(cb, rb, h)
        va[:, 129, :] = vb[:, 1, :]; ga[129, :] = gb[1, :]
        top = int(np.nonzero(va.reshape(h, -1).any(1))[0].max()) + 1 if va.any() else 1
        c2, r2 = T.column_runs(va[:top])
        T.write_node(DST / d / pa.name, c2, ga, r2); n += 1

# EN2 -x apron: old typ tx=112, new typ tx=111, ty=64..95; 8 m typ tx=14 vs 13, typ ty=8..11
for d, txa, txb, tys in (("1", 112, 111, range(64, 96)), ("1f", 112, 111, range(64, 96)), ("8", 14, 13, range(8, 12))):
    for ty in tys:
        pa, pb = SRC / d / f"{txa}_{ty}.bin", SRC / d / f"{txb}_{ty}.bin"
        if not (pa.exists() and pb.exists()):
            continue
        ca, ga, ra, ha = load(pa); cb, gb, rb, hb = load(pb)
        h = max(ha, hb)
        va = vol_from_runs_fast(ca, ra, h); vb = vol_from_runs_fast(cb, rb, h)
        va[:, :, 0] = vb[:, :, 128]; ga[:, 0] = gb[:, 128]
        top = int(np.nonzero(va.reshape(h, -1).any(1))[0].max()) + 1 if va.any() else 1
        c2, r2 = T.column_runs(va[:top])
        T.write_node(DST / d / pa.name, c2, ga, r2); n += 1

print("patched", n, "nodes into", DST)
