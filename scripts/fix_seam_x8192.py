"""Fix the x=8192 seam apron of the accepted typ tx=63 column (round 15).

tx=63 tiles were written while x=8192 was the window edge, so their east apron
column (local col N+1) held ground 0 and no voxels. The viewer uses the apron
only for AO / wall angles (border faces are always emitted). This copies the
real neighbour column (core col 0 of tile 64_ty, i.e. world x=8192) into the
apron of 1/63_ty, and copies its ground byte into the gw apron of 1/63_ty and
1f/63_ty. Tile ids, positions, core columns, buildings and foliage runs are
unchanged. Originals are backed up to data/backup_seam_tx63/.
"""
import gzip, shutil, sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
TL = ROOT / "web" / "public" / "data" / "tiles"
BK = ROOT / "data" / "backup_seam_tx63"
N = 128; S = N + 2; SS = S * S


def dec(p):
    b = gzip.decompress(p.read_bytes())
    counts = np.frombuffer(b, np.uint16, SS).copy()
    gw = np.frombuffer(b, np.uint8, SS, SS * 2).copy()
    nr = int(counts.sum())
    runs = np.frombuffer(b, np.uint16, nr * 3, SS * 3).reshape(-1, 3).copy()
    return counts, gw, runs


def enc(p, counts, gw, runs):
    tmp = p.with_suffix(".tmp")
    with gzip.GzipFile(tmp, "wb", compresslevel=9, mtime=0) as fh:
        fh.write(counts.astype(np.uint16).tobytes() + gw.astype(np.uint8).tobytes() + runs.astype(np.uint16).tobytes())
    tmp.replace(p)


def col_runs(counts, runs):
    off = np.concatenate([[0], np.cumsum(counts.astype(np.int64))])
    return [runs[off[i]:off[i + 1]] for i in range(SS)]


def main(ty0=-56, ty1=55):
    BK.mkdir(parents=True, exist_ok=True)
    n = 0
    for ty in range(ty0, ty1 + 1):
        a, b = TL / "1" / f"63_{ty}.bin", TL / "1" / f"64_{ty}.bin"
        af = TL / "1f" / f"63_{ty}.bin"
        if not (a.exists() and b.exists()):
            print("missing", ty); continue
        for src in (a, af):
            if src.exists():
                dst = BK / src.parent.name / src.name
                dst.parent.mkdir(parents=True, exist_ok=True)
                if not dst.exists():
                    shutil.copy2(src, dst)
        ca, ga, ra = dec(a)
        cb, gb, rb = dec(b)
        A, B = col_runs(ca, ra), col_runs(cb, rb)
        core_before = [A[r * S + c].tobytes() for r in range(S) for c in range(S - 1)]
        for r in range(S):
            ia, ib = r * S + (S - 1), r * S + 1  # a's east apron = b's core col 0 (x=8192)
            A[ia] = B[ib]
            ga[ia] = gb[ib]
        assert [A[r * S + c].tobytes() for r in range(S) for c in range(S - 1)] == core_before
        new_counts = np.array([len(x) for x in A], np.uint16)
        enc(a, new_counts, ga, np.concatenate(A) if any(len(x) for x in A) else np.zeros((0, 3), np.uint16))
        if af.exists():
            cf, gf, rf = dec(af)
            for r in range(S):
                gf[r * S + (S - 1)] = gb[r * S + 1]
            enc(af, cf, gf, rf)
        n += 1
    print("patched seam apron in", n, "tx=63 tiles; backups in", BK)


if __name__ == "__main__":
    main(*(int(x) for x in sys.argv[1:3]))
