"""Copy a 1-cell apron column between two already-written tile columns owned by worker-W.

Does not rebuild cores. Backs up destination tiles first.
  src_tx core column 128 (x = src_tx*128+127) -> dst_tx apron column 0 (x = dst_tx*128-1)
and optionally the reverse: dst core col 1 -> src apron col 129.
Works for tiles/1, tiles/1f, tiles/8 (N=128 cells at that level).
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import tiles as T  # noqa: E402

OUT = ROOT / "web" / "public" / "data" / "tiles"


def _patch(src, dst, src_c, dst_c):
    cs, gs, rs = T.load_node(src)
    cd, gd, rd = T.load_node(dst)
    vs = T.vol_from_runs(cs, rs)
    vd = T.vol_from_runs(cd, rd)
    h = max(vs.shape[0], vd.shape[0])
    S = np.zeros((h, T.N + 2, T.N + 2), np.uint16)
    D = np.zeros((h, T.N + 2, T.N + 2), np.uint16)
    S[: vs.shape[0]] = vs
    D[: vd.shape[0]] = vd
    before = D.copy()
    gbefore = gd.copy()
    D[:, :, dst_c] = S[:, :, src_c]
    gd[:, dst_c] = gs[:, src_c]
    n_v = int((D != before).any(0).sum())
    n_g = int((gd != gbefore).sum())
    top = int(np.nonzero(D.reshape(h, -1).any(1))[0].max()) + 1 if D.any() else 1
    counts, runs = T.column_runs(D[:top])
    T.write_node(dst, counts, gd, runs)
    return n_v, n_g


def main(level, src_tx, dst_tx, ty0, ty1, backup, also_reverse=True):
    src_c, dst_c = 128, 0  # east core of src -> west apron of dst
    n = n_miss = 0
    tv = tg = 0
    bdir = Path(backup)
    bdir.mkdir(parents=True, exist_ok=True)
    for ty in range(ty0, ty1 + 1):
        src = OUT / level / f"{src_tx}_{ty}.bin"
        dst = OUT / level / f"{dst_tx}_{ty}.bin"
        if not src.exists() or not dst.exists():
            print("MISS", src.name if not src.exists() else dst.name)
            n_miss += 1
            continue
        bak = bdir / f"{dst_tx}_{ty}.bin"
        if not bak.exists():
            shutil.copy2(dst, bak)
        v, g = _patch(src, dst, src_c, dst_c)
        tv += v
        tg += g
        n += 1
        if also_reverse:
            # west core of dst (already-owned) -> east apron of src
            bak2 = bdir / f"rev_{src_tx}_{ty}.bin"
            if not bak2.exists():
                shutil.copy2(src, bak2)
            v2, g2 = _patch(dst, src, 1, 129)
            tv += v2
            tg += g2
        if n % 16 == 0:
            print(f"  {level} {n} tiles, last ty={ty}, voxel-cols {tv}, ground {tg}", flush=True)
    print(f"done {level} src_tx={src_tx} -> dst_tx={dst_tx} ty={ty0}..{ty1}: {n} patched, {n_miss} missing, voxel-cols {tv}, ground cells {tg}")


def _patch_row(src, dst, src_r, dst_r):
    cs, gs, rs = T.load_node(src)
    cd, gd, rd = T.load_node(dst)
    vs = T.vol_from_runs(cs, rs)
    vd = T.vol_from_runs(cd, rd)
    h = max(vs.shape[0], vd.shape[0])
    S = np.zeros((h, T.N + 2, T.N + 2), np.uint16)
    D = np.zeros((h, T.N + 2, T.N + 2), np.uint16)
    S[: vs.shape[0]] = vs
    D[: vd.shape[0]] = vd
    before = D.copy()
    gbefore = gd.copy()
    D[:, dst_r, :] = S[:, src_r, :]
    gd[dst_r, :] = gs[src_r, :]
    n_v = int((D != before).any(0).sum())
    n_g = int((gd != gbefore).sum())
    top = int(np.nonzero(D.reshape(h, -1).any(1))[0].max()) + 1 if D.any() else 1
    counts, runs = T.column_runs(D[:top])
    T.write_node(dst, counts, gd, runs)
    return n_v, n_g


def main_row(level, src_ty, dst_ty, tx0, tx1, backup, also_reverse=True):
    src_r, dst_r = 128, 0  # north core of src -> south apron of dst
    n = n_miss = tv = tg = 0
    bdir = Path(backup)
    bdir.mkdir(parents=True, exist_ok=True)
    for tx in range(tx0, tx1 + 1):
        src = OUT / level / f"{tx}_{src_ty}.bin"
        dst = OUT / level / f"{tx}_{dst_ty}.bin"
        if not src.exists() or not dst.exists():
            print("MISS", src.name if not src.exists() else dst.name)
            n_miss += 1
            continue
        bak = bdir / f"{tx}_{dst_ty}.bin"
        if not bak.exists():
            shutil.copy2(dst, bak)
        v, g = _patch_row(src, dst, src_r, dst_r)
        tv += v; tg += g; n += 1
        if also_reverse:
            bak2 = bdir / f"rev_{tx}_{src_ty}.bin"
            if not bak2.exists():
                shutil.copy2(src, bak2)
            v2, g2 = _patch_row(dst, src, 1, 129)
            tv += v2; tg += g2
        if n % 16 == 0:
            print(f"  {level} {n} tiles, last tx={tx}, voxel-cols {tv}, ground {tg}", flush=True)
    print(f"done {level} src_ty={src_ty} -> dst_ty={dst_ty} tx={tx0}..{tx1}: {n} patched, {n_miss} missing, voxel-cols {tv}, ground cells {tg}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", required=True, choices=list("18")+["1f"])
    ap.add_argument("--axis", default="col", choices=("col", "row"))
    ap.add_argument("--src-tx", type=int, default=None)
    ap.add_argument("--dst-tx", type=int, default=None)
    ap.add_argument("--src-ty", type=int, default=None)
    ap.add_argument("--dst-ty", type=int, default=None)
    ap.add_argument("--tx0", type=int, default=None)
    ap.add_argument("--tx1", type=int, default=None)
    ap.add_argument("--ty0", type=int, default=None)
    ap.add_argument("--ty1", type=int, default=None)
    ap.add_argument("--backup", required=True)
    ap.add_argument("--no-reverse", action="store_true")
    a = ap.parse_args()
    if a.axis == "col":
        main(a.level, a.src_tx, a.dst_tx, a.ty0, a.ty1, a.backup, also_reverse=not a.no_reverse)
    else:
        main_row(a.level, a.src_ty, a.dst_ty, a.tx0, a.tx1, a.backup, also_reverse=not a.no_reverse)
