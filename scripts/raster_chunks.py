"""Split origin-aware full-window rasters into square chunks (≤8192 on a side).

zones.bin is gzip of uint16[RH,RW] block ids + uint16[RH,RW] building ids.
windows.bin is gzip of uint8[RH,RW] typological window kinds.

Each chunk is a padded CS×CS tile, stored gzipped as:
  zones/cx_cy.bin  — uint16 blk[CS*CS] + uint16 bld[CS*CS]
  windows/cx_cy.bin — uint8 kind[CS*CS]
CS=2048 so a 6144×10240 window is an exact 3×5 grid, and the same layout
grows toward the whole municipality without ever uploading one >8192 texture.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "public" / "data"
CS = 2048  # metres / texels; must stay ≤ 8192 (typical WebGL MAX_TEXTURE_SIZE)


def grid(RW: int, RH: int, cs: int = CS):
    nx = (RW + cs - 1) // cs
    ny = (RH + cs - 1) // cs
    return nx, ny


def write_uint_chunks(arr: np.ndarray, dest: Path, extra: np.ndarray | None = None, cs: int = CS):
    """arr is (RH, RW). extra, if given, is packed after arr in the same chunk file."""
    RH, RW = arr.shape
    nx, ny = grid(RW, RH, cs)
    dest.mkdir(parents=True, exist_ok=True)
    nbytes = 0
    for cy in range(ny):
        for cx in range(nx):
            r0, c0 = cy * cs, cx * cs
            r1, c1 = min(r0 + cs, RH), min(c0 + cs, RW)
            tile = np.zeros((cs, cs), arr.dtype)
            tile[: r1 - r0, : c1 - c0] = arr[r0:r1, c0:c1]
            payload = tile.tobytes()
            if extra is not None:
                ex = np.zeros((cs, cs), extra.dtype)
                ex[: r1 - r0, : c1 - c0] = extra[r0:r1, c0:c1]
                payload += ex.tobytes()
            path = dest / f"{cx}_{cy}.bin"
            with gzip.GzipFile(path, "wb", compresslevel=9, mtime=0) as fh:
                fh.write(payload)
            nbytes += path.stat().st_size
    return nx, ny, nbytes


def split_existing(meta_path: Path | None = None, cs: int = CS):
    meta_path = meta_path or (OUT / "meta.json")
    meta = json.loads(meta_path.read_text())
    x0, y0 = int(meta.get("x0", 0)), int(meta.get("y0", 0))
    W, D = int(meta["W"]), int(meta["D"])
    RW, RH = W - x0, D - y0

    zpath = OUT / "zones.bin"
    raw = gzip.open(zpath, "rb").read()
    n = RH * RW
    blk = np.frombuffer(raw, np.uint16, count=n).reshape(RH, RW)
    bld = np.frombuffer(raw, np.uint16, offset=n * 2, count=n).reshape(RH, RW)
    nx, ny, zb = write_uint_chunks(blk, OUT / "zones", extra=bld, cs=cs)
    print("zones chunks", nx, "x", ny, "cs", cs, zb // 1024, "KB")

    wpath = OUT / "windows.bin"
    wr = gzip.open(wpath, "rb").read()
    win = np.frombuffer(wr, np.uint8, count=n).reshape(RH, RW)
    _, _, wb = write_uint_chunks(win, OUT / "windows", cs=cs)
    print("windows chunks", nx, "x", ny, wb // 1024, "KB")

    info = {"s": cs, "nx": nx, "ny": ny, "x0": x0, "y0": y0, "RW": RW, "RH": RH,
            "note": "1 m rasters split into CS×CS chunks (≤8192) for WebGL; layer = cy*nx+cx"}
    (OUT / "raster_chunks.json").write_text(json.dumps(info, indent=2) + "\n")
    meta["raster_chunks"] = info
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    print("wrote raster_chunks.json and meta.raster_chunks")
    return info



def record_index(x0: int, y0: int, RW: int, RH: int, cs: int = CS, meta_path: Path | None = None):
    nx, ny = grid(RW, RH, cs)
    info = {"s": cs, "nx": nx, "ny": ny, "x0": int(x0), "y0": int(y0), "RW": int(RW), "RH": int(RH),
            "note": "1 m rasters split into CS×CS chunks (≤8192) for WebGL; layer = cy*nx+cx"}
    (OUT / "raster_chunks.json").write_text(json.dumps(info, indent=2) + "\n")
    meta_path = meta_path or (OUT / "meta.json")
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        meta["raster_chunks"] = info
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    print("raster_chunks", nx, "x", ny, "cs", cs, "window", RW, "x", RH)
    return info


if __name__ == "__main__":

    split_existing()
