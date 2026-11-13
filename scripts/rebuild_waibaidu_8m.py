"""Re-encode 8 m clay typ 1_0 and 2_0 from current 1 m (incl. cited Waibaidu truss).

Does not rewrite typ≥0 1 m tiles. Assembles 1 m cores (not 1f), downsamples by 8,
writes only tiles/8/1_0.bin and tiles/8/2_0.bin.

Thin riveted-truss steel is kept even when an 8³ block is below the usual
occupancy cut, so the span still reads at a distance.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

import tiles as T

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "public" / "data"
TARGETS = ((1, 0), (2, 0))
# OSM way 27498117 bounds, padded
WB_BOX = (1980, 525, 2085, 585)  # xmin, ymin, xmax, ymax in city metres


def assemble_1m(tx8, ty8, metal_ids):
    x0, y0 = tx8 * T.N * 8, ty8 * T.N * 8  # core origin, metres
    span = (T.N + 2) * 8  # 1040
    ox, oy = x0 - 8, y0 - 8  # vol origin (8 m apron)
    tx0, ty0 = ox // T.N, oy // T.N
    tx1, ty1 = (ox + span - 1) // T.N, (oy + span - 1) // T.N

    tiles_1 = []
    hmax = 80
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            path = OUT / "tiles" / "1" / f"{tx}_{ty}.bin"
            if not path.exists():
                continue
            counts, gwin, runs = T.load_node(path)
            if len(runs):
                hmax = max(hmax, int((runs[:, 0] + runs[:, 1]).max()) + 8)
            tiles_1.append((tx, ty, counts, gwin, runs))
    print(f"8m {tx8}_{ty8}: 1m tiles {len(tiles_1)} hmax {hmax} window x={ox}..{ox+span} y={oy}..{oy+span}")
    vol = np.zeros((hmax, span, span), np.uint16)
    gsrc = np.zeros((span, span), np.uint8)

    # paste each 1 m CORE (cells 1..N), so neighbour aprons cannot overwrite
    for tx, ty, counts, gwin, runs in tiles_1:
        v = T.vol_from_runs(counts, runs, h=hmax)
        # core of the 1 m tile in city metres is [tx*N, (tx+1)*N)
        for r_src in range(1, T.N + 1):
            yy = ty * T.N + (r_src - 1)
            rd = yy - oy
            if not (0 <= rd < span):
                continue
            for c_src in range(1, T.N + 1):
                xx = tx * T.N + (c_src - 1)
                cd = xx - ox
                if 0 <= cd < span:
                    vol[:, rd, cd] = v[:, r_src, c_src]
                    gsrc[rd, cd] = gwin[r_src, c_src]

    v8 = T.downsample(vol, 8)
    # keep thin Waibaidu steel that the 1/4 occupancy cut would drop
    metal = np.array(sorted(metal_ids), np.uint16)
    Hs, Ds, Ws = v8.shape
    xmin, ymin, xmax, ymax = WB_BOX
    r0 = max(0, (ymin - oy) // 8)
    r1 = min(Ds, (ymax - oy) // 8 + 1)
    c0 = max(0, (xmin - ox) // 8)
    c1 = min(Ws, (xmax - ox) // 8 + 1)
    kept = 0
    if r1 > r0 and c1 > c0:
        sub = vol[:, r0 * 8:r1 * 8, c0 * 8:c1 * 8]
        # pad to multiples of 8
        pr, pc = r1 * 8 - r0 * 8, c1 * 8 - c0 * 8
        if pr % 8 or pc % 8:
            sub = np.pad(sub, ((0, 0), (0, (-pr) % 8), (0, (-pc) % 8)))
        hb, db, wb = -(-sub.shape[0] // 8), sub.shape[1] // 8, sub.shape[2] // 8
        sub = np.pad(sub, ((0, hb * 8 - sub.shape[0]), (0, 0), (0, 0)))
        blk = sub.reshape(hb, 8, db, 8, wb, 8)
        # topmost metal in the 8³ block
        col = np.zeros((hb, db, wb), np.uint16)
        for dz in range(7, -1, -1):
            layer = blk[:, dz]
            for dy in range(8):
                for dx in range(8):
                    v = layer[:, :, dy, :, dx]
                    m = np.isin(v, metal)
                    col = np.where((col == 0) & m, v, col)
        dest = v8[:, r0:r0 + db, c0:c0 + wb]
        mask = (dest == 0) & (col != 0)
        dest[mask] = col[mask]
        v8[:, r0:r0 + db, c0:c0 + wb] = dest
        kept = int(mask.sum())
    print(f"  kept thin steel cells {kept}")

    # ground: same sampling as tiles.export (stride, not mean) so seams match typ 0_0
    g8 = gsrc[::8, ::8]
    if g8.shape[0] < T.N + 2 or g8.shape[1] < T.N + 2:
        g8 = np.pad(g8, ((0, max(0, T.N + 2 - g8.shape[0])), (0, max(0, T.N + 2 - g8.shape[1]))))
    g8 = g8[: T.N + 2, : T.N + 2]
    counts, runs = T.column_runs(v8)
    path = OUT / "tiles" / "8" / f"{tx8}_{ty8}.bin"
    T.write_node(path, counts, g8, runs)
    print(f"wrote {path} runs {len(runs)} g {g8.shape} vol8 {v8.shape}")


def main():
    meta = json.loads((OUT / "meta.json").read_text())
    metal = [i for i, e in enumerate(meta["palette"]) if int(e[3]) == 6]
    for t in TARGETS:
        assemble_1m(*t, metal)


if __name__ == "__main__":
    main()
