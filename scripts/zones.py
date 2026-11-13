"""Which building and which city block every 1 m column belongs to.

Adapted from the MIT Warsaw viewer pipeline to the origin-aware coverage
(row = y - y0, col = x - x0). Output: zones.bin = uint16 block[RH, RW] +
uint16 building[RH, RW] (0: none), plus zones.json. Does not rewrite voxel tiles.
"""
import gzip
import json
from pathlib import Path

import numpy as np
import rasterio.features
from affine import Affine
from scipy import ndimage

from aoi import RAW

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "public" / "data"
META = OUT / "meta.json"


def extent():
    meta = json.loads(META.read_text())
    x0 = int(meta.get("x0", 0))
    y0 = int(meta.get("y0", 0))
    W, D = int(meta["W"]), int(meta["D"])
    return x0, y0, W, D


def tunnelish(t):
    return t.get("tunnel") in ("yes", "building_passage", "culvert") or t.get("layer", "0").lstrip("-").isdigit() and int(t.get("layer", "0")) < 0 \
        or t.get("covered") == "yes" or t.get("location") == "underground"


def main():
    from osm import OSM
    x0, y0, W, D = extent()
    RW, RH = W - x0, D - y0
    tr = Affine.translation(x0, y0)
    osm_path = ROOT / RAW / "osm_expanded.json"
    if not osm_path.exists():
        osm_path = ROOT / RAW / "osm.json"
    osm = OSM(str(osm_path))

    def burn(shapes, dtype=np.uint16, all_touched=False):
        shapes = [(s, int(v)) for s, v in shapes if s is not None and not s.is_empty]
        if not shapes:
            return np.zeros((RH, RW), dtype)
        return rasterio.features.rasterize(shapes, out_shape=(RH, RW), transform=tr, fill=0,
                                           all_touched=all_touched, dtype=dtype)

    blds = [(g, t) for oid, typ, t, g in osm.areas
            if t.get("building") not in (None, "no") and "building:part" not in t and t.get("location") != "underground"]
    blds.sort(key=lambda b: -b[0].area)
    bld = burn([(g, i + 1) for i, (g, t) in enumerate(blds)])
    print("buildings", len(blds), "columns", int((bld > 0).sum()), "raster", bld.shape)

    ROADS = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street",
             "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link", "pedestrian"}
    lines = [l.buffer(1.5) for oid, t, l in osm.lines if (t.get("highway") in ROADS or t.get("railway") in ("rail", "tram", "light_rail"))
             and not tunnelish(t) and t.get("bridge") is None]
    water = [g for oid, typ, t, g in osm.areas if t.get("natural") == "water" or t.get("waterway") == "riverbank"]
    sep = burn([(g, 1) for g in lines + water], np.uint8, all_touched=True) > 0
    n_blds = len(blds)
    # drop OSM geometries before the full-window EDT; same block logic, lower peak RAM
    del osm, lines, water, blds
    sep &= bld == 0
    lab, n = ndimage.label(~sep)
    print("raw blocks", n)
    del sep
    ir, ic = ndimage.distance_transform_edt(lab == 0, return_distances=False, return_indices=True)
    blk = lab[ir, ic].astype(np.int32, copy=False)
    del lab, ir, ic
    area = np.bincount(blk.ravel(), minlength=n + 1)
    big = area[blk] > 120_000
    r_id = ((np.arange(RH, dtype=np.int32) + y0) // 256)
    c_id = ((np.arange(RW, dtype=np.int32) + x0) // 256)
    blk = np.where(big, (n + 1) + r_id[:, None] * (RW // 256 + 1) + c_id[None, :] + blk * 10_000, blk).astype(np.int32, copy=False)
    del big, area, r_id, c_id
    _, blk = np.unique(blk, return_inverse=True)
    blk = (blk.reshape(RH, RW) + 1).astype(np.int32, copy=False)
    area = np.bincount(blk.ravel())
    small = area[blk] < 400
    if small.any():
        ir, ic = ndimage.distance_transform_edt(small, return_distances=False, return_indices=True)
        blk = blk[ir, ic]
        del ir, ic
    del small, area
    idx = np.flatnonzero(bld.ravel())
    if idx.size:
        b, k = bld.ravel()[idx], blk.ravel()[idx]
        pairs = np.unique(np.stack([b, k], 1), axis=0, return_counts=True)
        best = {}
        for (bi, ki), cnt in zip(*pairs):
            if cnt > best.get(bi, (0, 0))[1]:
                best[bi] = (int(ki), int(cnt))
        lut = np.zeros(n_blds + 1, np.int32)
        for bi, (ki, _) in best.items():
            lut[bi] = ki
        blk = np.where(bld > 0, lut[bld], blk)
        del b, k, pairs, lut, best
    del idx
    _, blk = np.unique(blk, return_inverse=True)
    blk = (blk.reshape(RH, RW) + 1).astype(np.uint16)
    nb = int(blk.max())
    print("blocks", nb)

    def stats(lab, n, x0=x0, y0=y0):
        # same centroids/bboxes as the previous mgrid + sum_labels path, without 2-D meshes
        flat = lab.ravel()
        cnt = np.bincount(flat, minlength=n + 1)[1:].astype(np.float64)
        ys = np.repeat(np.arange(lab.shape[0], dtype=np.float64) + y0, lab.shape[1])
        xs = np.tile(np.arange(lab.shape[1], dtype=np.float64) + x0, lab.shape[0])
        cy = np.bincount(flat, weights=ys, minlength=n + 1)[1:] / np.maximum(cnt, 1)
        cx = np.bincount(flat, weights=xs, minlength=n + 1)[1:] / np.maximum(cnt, 1)
        del ys, xs, flat
        sl = ndimage.find_objects(lab, n)
        out = []
        for i in range(n):
            s = sl[i]
            box = [s[1].start + x0, s[0].start + y0, s[1].stop + x0, s[0].stop + y0] if s else [0, 0, 0, 0]
            out.append([round(float(cx[i]), 1), round(float(cy[i]), 1), int(cnt[i]), *box])
        return out

    meta_z = {"blocks": stats(blk, nb), "buildings": stats(bld.astype(np.int32), n_blds) if n_blds else []}
    # no cost1m for the expanded window; a column-count stand-in keeps budget ordering honest
    for b in meta_z["blocks"]:
        b.append(max(80, int(b[2] * 1.5)))
    with gzip.GzipFile(OUT / "zones.bin", "wb", compresslevel=9, mtime=0) as fh:
        fh.write(blk.tobytes() + bld.astype(np.uint16).tobytes())
    json.dump(meta_z, open(OUT / "zones.json", "w"))
    print("wrote zones.bin", (OUT / "zones.bin").stat().st_size // 1024, "KB", "shape", blk.shape)
    from raster_chunks import write_uint_chunks, record_index
    write_uint_chunks(blk.astype(np.uint16), OUT / "zones", extra=bld.astype(np.uint16))
    record_index(x0, y0, RW, RH)


if __name__ == "__main__":
    main()
