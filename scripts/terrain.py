"""Bare terrain for the first moment of loading, covering the streamed x0/y0 window.

Heights: stitched ground_all (Copernicus GLO-30, road/park flattened — not LiDAR),
every 4 m, decimetres above the model base. Colours: keep the first window's
existing 2 m colours where y>=0; burn OSM surfaces for y<0 from the current palette.
"""
import gzip
import json
from pathlib import Path

import numpy as np
from affine import Affine
from rasterio.features import rasterize
from scipy import ndimage

from aoi import RAW

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "public" / "data"
META = OUT / "meta.json"
STEP = 4


def _pal_id(meta, hex_s, mat=0):
    h = hex_s.lstrip("#")
    rgb = tuple(int(h[i:i+2], 16) for i in (0, 2, 4)) + (mat,)
    # palette is already quantized; find nearest existing entry of this material
    best, bd = 0, 1e9
    for i, e in enumerate(meta["palette"]):
        if e[3] != mat and mat != 0:
            continue
        d = sum((a - b) ** 2 for a, b in zip(e[:3], rgb[:3]))
        if d < bd:
            best, bd = i, d
    return best


def surface_colors(osm, pal_lut, x0, y0, RW, RH, water_mask):
    tr = Affine.translation(x0, y0)
    bare, grass, grass2, garden, paving, paving2, asphalt, water_id, sett, concrete, ballast = pal_lut
    surf = np.full((RH, RW), bare, np.uint16)
    buckets = {}

    def add(geom, pid):
        if geom is None or geom.is_empty:
            return
        buckets.setdefault(int(pid), []).append(geom)

    for oid, typ, t, geom in osm.areas:
        lu = t.get("landuse") or t.get("leisure") or t.get("natural") or t.get("amenity")
        if lu in ("grass", "park", "garden", "village_green", "meadow", "recreation_ground",
                  "cemetery", "pitch", "golf_course", "allotments"):
            key = garden if lu == "garden" else (grass if (oid & 1) == 0 else grass2)
            add(geom, key)
        elif lu in ("forest", "wood", "scrub"):
            add(geom, garden)
        elif lu in ("residential", "commercial", "retail", "industrial", "construction"):
            add(geom, paving2 if lu == "residential" else paving)
        elif lu in ("square",) or t.get("place") == "square" or t.get("highway") == "pedestrian":
            add(geom, paving)
        if t.get("landuse") == "railway" or t.get("railway") in ("rail", "yard"):
            add(geom, ballast)
    for oid, t, line in osm.lines:
        hwy = t.get("highway")
        if not hwy or t.get("tunnel") in ("yes", "building_passage", "culvert") or t.get("bridge") not in (None, "no"):
            continue
        w = 5 if hwy in ("footway", "path", "steps") else (14 if hwy == "pedestrian" else 8)
        if "南京" in (t.get("name") or ""):
            w = max(w, 16)
        try:
            g = line.buffer(w / 2)
        except Exception:
            continue
        if t.get("surface") in ("paving_stones", "sett", "cobblestone"):
            pid = sett
        elif t.get("surface") in ("concrete",):
            pid = concrete
        elif t.get("surface") in ("dirt", "earth", "grass", "ground"):
            pid = grass
        elif hwy in ("footway", "path", "pedestrian"):
            pid = paving
        else:
            pid = asphalt
        add(g, pid)

    for pid, geoms in buckets.items():
        shapes = [(g, 1) for g in geoms]
        m = rasterize(shapes, out_shape=(RH, RW), transform=tr, fill=0, all_touched=True, dtype=np.uint8)
        surf[(m == 1) & (water_mask == 0)] = pid
        print("  burnt", len(geoms), "shapes pid", pid, flush=True)
    surf[water_mask == 1] = water_id
    return surf


def main():
    from osm import OSM
    meta = json.loads(META.read_text())
    x0 = int(meta.get("x0", 0))
    y0 = int(meta.get("y0", 0))
    W, D = int(meta["W"]), int(meta["D"])
    base = int(meta.get("base", -9))
    RW, RH = W - x0, D - y0

    z = np.load(ROOT / "data" / "ground_all.npz")
    gy0 = int(z["y0"]) if "y0" in z.files else y0
    gx0 = int(z["x0"]) if "x0" in z.files else 0
    ground = z["ground"].astype(np.float32)
    if ground.shape != (RH, RW) or gy0 != y0 or gx0 != x0:
        canvas = np.full((RH, RW), 4.0, np.float32)
        r0 = gy0 - y0
        c0 = gx0 - x0
        aa, bb = max(0, r0), min(RH, r0 + ground.shape[0])
        ca, cb = max(0, c0), min(RW, c0 + ground.shape[1])
        if aa < bb and ca < cb:
            canvas[aa:bb, ca:cb] = ground[aa - r0:bb - r0, ca - c0:cb - c0]
        ground = canvas
    g = np.clip(np.round(ground - base), 1, 250).astype(np.float32)
    h = np.round(ndimage.uniform_filter(g, STEP)[::STEP, ::STEP] * 10).astype(np.uint16)

    # colours
    pal = meta["palette"]
    def pid(hx, mat=0):
        return _pal_id(meta, hx, mat)

    pal_lut = (
        pid("#a8977c", 10),  # bare / soil
        pid("#86b45a", 8),   # grass
        pid("#79a852", 8),
        pid("#73a34e", 8),
        pid("#c6c2b8"),
        pid("#b5b4af"),
        pid("#5f636b"),
        pid("#5a6e7a", 4),
        pid("#8f8981"),
        pid("#bdbab2"),
        pid("#8a8178"),
    )
    osm_path = ROOT / RAW / "osm_expanded.json"
    if not osm_path.exists():
        osm_path = ROOT / RAW / "osm.json"
    osm = OSM(str(osm_path))
    # water from ground<=0.2 in the first window is not reliable; use OSM + any saved mask
    water = np.zeros((RH, RW), np.uint8)
    for npz_name in ("ground_west.npz", "ground_jingan.npz", "dem_west.npz", "dem_jingan.npz", "ground_fixed.npz"):
        p = ROOT / "data" / npz_name
        if not p.exists():
            continue
        zz = np.load(p)
        if "water" not in zz.files:
            continue
        w = zz["water"].astype(np.uint8)
        wy0 = int(zz["y0"]) if "y0" in zz.files else (0 if "fixed" in npz_name else y0)
        r0 = wy0 - y0
        if r0 + w.shape[0] <= 0 or r0 >= RH:
            continue
        aa, bb = max(0, r0), min(RH, r0 + w.shape[0])
        water[aa:bb, :w.shape[1]] = np.maximum(water[aa:bb, :w.shape[1]], w[aa - r0:bb - r0, :water.shape[1]])
    # also burn OSM water
    water_geoms = [g for _, _, t, g in osm.areas if t.get("natural") == "water" or t.get("waterway") in ("riverbank", "dock") or t.get("landuse") == "basin"]
    if water_geoms:
        m = rasterize([(g, 1) for g in water_geoms], out_shape=(RH, RW), transform=Affine.translation(x0, y0),
                      fill=0, all_touched=True, dtype=np.uint8)
        water = np.maximum(water, m)

    surf = surface_colors(osm, pal_lut, x0, y0, RW, RH, water)
    rgb_lut = np.array([e[:3] for e in pal] + [(0, 0, 0)] * (65536 - len(pal)), np.uint8)
    rgb = rgb_lut[surf]

    # keep already-accepted terrain colours from the previous terrain.bin
    old = OUT / "terrain.bin"
    if old.exists() and (y0 < 0 or x0 < 0):
        import gzip as gz
        raw = gz.decompress(old.read_bytes())
        def try_decode(oy, ox, od, ow):
            rrw, rrh = ow - ox, od - oy
            tw_, th_ = int(np.ceil(rrw / 4)), int(np.ceil(rrh / 4))
            cw_, ch_ = rrw // 2, rrh // 2
            need = tw_ * th_ * 2 + cw_ * ch_ * 3
            if len(raw) != need:
                return None
            return np.frombuffer(raw, np.uint8, count=cw_ * ch_ * 3, offset=tw_ * th_ * 2).reshape(ch_, cw_, 3), oy, ox
        kept = src_y0 = src_x0 = None
        for oy, ox, od, ow in (
            (y0, x0, D, W),
            (y0, x0, 7168, 10240),  # previous exclusive max before Gongqing +y grow
            (y0, x0, 7168, 8192),  # previous exclusive max before Jiangwan/SUFE +x grow
            (y0, x0, 6144, 8192),  # previous exclusive max before Yangpu Pudong +y grow
            (y0, x0, 5120, 8192),  # previous exclusive max before Yangpu Bridge +y grow
            (y0, x0, 3072, 8192),  # previous exclusive max before +y grow
            (y0, x0, D, 6144),  # previous exclusive max before Yangpu +x grow
            (y0, x0, D, 4096),  # previous exclusive max before a +x grow
            (-7168, -2048, 7168, 10240),
            (-7168, -2048, 7168, 8192),
            (-7168, -2048, 6144, 8192),
            (-7168, -2048, 5120, 8192),
            (-7168, -2048, 3072, 8192),
            (-7168, -2048, 3072, 6144),
            (-7168, -2048, 3072, 4096),
            (-5120, -2048, 3072, 4096),
            (-5120, 0, 3072, 4096),
            (-2048, 0, 3072, 4096),
            (0, 0, 3072, 4096),
        ):
            got = try_decode(oy, ox, od, ow)
            if got is not None:
                kept, src_y0, src_x0 = got
                break
        if kept is not None:
            dest = rgb[1::2, 1::2]
            # snapshot freshly burnt colours before keep overwrites the shared view
            fresh = dest.copy()
            # dest row k is y = y0+1+2k; kept row j is y = src_y0+1+2j
            k0 = (src_y0 - y0) // 2
            c0 = (src_x0 - x0) // 2
            ra, rb = max(0, k0), min(dest.shape[0], k0 + kept.shape[0])
            ca, cb = max(0, c0), min(dest.shape[1], c0 + kept.shape[1])
            if rb > ra and cb > ca:
                dest[ra:rb, ca:cb] = kept[ra - k0:rb - k0, ca - c0:cb - c0]
                print("kept earlier terrain colours", ra, rb, ca, cb, "from", src_x0, src_y0)
            # previously used to refresh an unfilled +x typ ty<-16 corner; that band is
            # now accepted, so do not overwrite kept colours. New +y strip is outside
            # the kept D=3072 raster and already has freshly burnt colours.
            for fx0, fy0, fx1, fy1 in ():
                # dest row/col of world (x,y) ≈ ((y - (y0+1))/2, (x - (x0+1))/2)
                r0 = (fy0 - (y0 + 1)) // 2
                r1 = (fy1 - (y0 + 1)) // 2
                c0_ = (fx0 - (x0 + 1)) // 2
                c1_ = (fx1 - (x0 + 1)) // 2
                ra, rb = max(0, r0), min(dest.shape[0], r1)
                ca, cb = max(0, c0_), min(dest.shape[1], c1_)
                if rb > ra and cb > ca:
                    dest[ra:rb, ca:cb] = fresh[ra:rb, ca:cb]
                    print("refreshed terrain colours", ra, rb, ca, cb, "world", fx0, fy0, fx1, fy1)

    c = np.ascontiguousarray(rgb[1::2, 1::2])
    outp = OUT / "terrain.bin"
    with gzip.GzipFile(outp, "wb", compresslevel=9, mtime=0) as fh:
        fh.write(h.tobytes())
        fh.write(c.tobytes())
    print("terrain", "h", h.shape, "c", c.shape, "y0", y0, "RH", RH, outp.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main()
