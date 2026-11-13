"""Cited schematic for Jing'an Temple 大雄宝殿 (and typological eaves on named halls).

Plan footprints come from OSM. Hall height 26 m (庑殿重檐, copper tiles) is from
public sources listed in overrides.json. Not a scan, not LiDAR, and not a
rebuild of the surrounding Jing'an window — only typ 5_-33, 5_-32, 6_-33, 6_-32.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from affine import Affine
from rasterio.features import rasterize

import tiles as T
from osm import OSM
from sculpt import JINGAN_MAHAVIRA_WAY, JINGAN_MAHAVIRA_H, JINGAN_FA_TANG_WAY, JINGAN_SOURCES

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "public" / "data"
TILES = ( (5, -33), (5, -32), (6, -33), (6, -32) )

# typological eaves only — no published height used as a hard number except 大雄宝殿
# 法堂 32 m appears in a weaker Sohu piece; treat as typological and cap conservatively
# if the OSM building already exceeds it. We still sculpt a hip roof, height =
# max(existing mass, typological), documented as typological.
HALLS = [
    # way id, height_m, kind, cited?
    (JINGAN_MAHAVIRA_WAY, JINGAN_MAHAVIRA_H, "mahavira", True),
    (JINGAN_FA_TANG_WAY, 28.0, "fatang", False),  # typological, not the 32 m Sohu figure
    (1246090669, 12.0, "shanmen", False),
    (1246090670, 14.0, "zhonglou", False),
    (1246090671, 14.0, "gulou", False),
]


def append_palette(meta, rgb, mat):
    pal = meta["palette"]
    for i, e in enumerate(pal):
        if list(e[:3]) == list(rgb) and int(e[3]) == mat:
            return i, False
    pal.append([int(rgb[0]), int(rgb[1]), int(rgb[2]), int(mat)])
    return len(pal) - 1, True


def paint_hall(vol, gwin, origin, geom, height_m, wall_id, roof_id, ridge_id, eave_overhang=3):
    ox, oy = origin
    H, rows, cols = vol.shape
    if geom is None or geom.is_empty:
        return 0
    tr = Affine.translation(ox, oy)
    foot = rasterize([(geom, 1)], out_shape=(rows, cols), transform=tr, fill=0, dtype=np.uint8)
    eave_lo = rasterize([(geom.buffer(eave_overhang), 1)], out_shape=(rows, cols), transform=tr, fill=0, all_touched=True, dtype=np.uint8)
    eave_hi = rasterize([(geom.buffer(2), 1)], out_shape=(rows, cols), transform=tr, fill=0, all_touched=True, dtype=np.uint8)
    if not foot.any():
        return 0
    fr, fc = np.nonzero(foot)
    fx, fy = fc + ox, fr + oy
    span_x = fx.max() - fx.min() + 1
    span_y = fy.max() - fy.min() + 1
    cx, cy = float(fx.mean()), float(fy.mean())
    n = 0
    # clear previous OSM box massing inside the hall (keep ground)
    for r, c in zip(*np.nonzero(eave_lo)):
        gz = int(gwin[r, c])
        cap = min(H, gz + int(round(height_m)) + 4)
        if cap > gz:
            vol[gz:cap, r, c] = 0
    rs, cs = np.nonzero(foot)
    for r, c in zip(rs, cs):
        gz = int(gwin[r, c])
        x, y = c + ox + 0.5, r + oy + 0.5
        t = abs(x - cx) / max(1.0, span_x / 2.0) if span_y >= span_x else abs(y - cy) / max(1.0, span_y / 2.0)
        t = min(1.2, t)
        z_ridge = gz + int(round(height_m))
        z_eave_hi = gz + int(round(height_m * 0.88))
        z_upper_wall = gz + int(round(height_m * 0.70))
        z_eave_lo = gz + int(round(height_m * 0.56))
        z_lower_wall = gz + int(round(height_m * 0.48))
        vol[gz:z_lower_wall, r, c] = wall_id
        vol[z_lower_wall:min(H, z_eave_lo + 1), r, c] = roof_id
        if z_upper_wall > z_eave_lo + 1:
            vol[z_eave_lo + 1:z_upper_wall, r, c] = wall_id
        z_roof1 = int(round(z_eave_hi + (z_ridge - z_eave_hi) * max(0.0, 1.0 - t)))
        z_roof1 = min(H, max(z_upper_wall + 1, z_roof1))
        vol[z_upper_wall:z_roof1, r, c] = roof_id
        if t < 0.14:
            lo = min(H - 1, z_ridge - 1)
            hi = min(H, z_ridge + 1)
            if hi > lo:
                vol[lo:hi, r, c] = ridge_id
        n += 1
    # lower eave collar
    for r, c in zip(*np.nonzero((eave_lo == 1) & (foot == 0))):
        gz = int(gwin[r, c])
        z0 = gz + int(round(height_m * 0.52))
        z1 = gz + int(round(height_m * 0.58)) + 1
        vol[max(0, z0):min(H, z1), r, c] = roof_id
        n += 1
    # upper eave collar
    for r, c in zip(*np.nonzero((eave_hi == 1) & (foot == 0))):
        gz = int(gwin[r, c])
        z0 = gz + int(round(height_m * 0.84))
        z1 = gz + int(round(height_m * 0.90)) + 1
        vol[max(0, z0):min(H, z1), r, c] = roof_id
        n += 1
    return n


def load_vol(path, hmin=80):
    counts, gwin, runs = T.load_node(path)
    h = hmin
    if len(runs):
        h = max(hmin, int((runs[:, 0] + runs[:, 1]).max()) + 8)
    vol = T.vol_from_runs(counts, runs, h=h)
    return vol, gwin


def main():
    meta = json.loads((OUT / "meta.json").read_text())
    # copper tiles ~ green-brown weathered bronze; teak columns / walls
    copper, new_c = append_palette(meta, [92, 98, 58], 12)   # ROOF
    ridge, new_r = append_palette(meta, [70, 72, 48], 12)
    teak, new_t = append_palette(meta, [118, 78, 48], 1)     # WALL
    print("palette copper", copper, "new", new_c, "ridge", ridge, "teak", teak, "new", new_t)

    osm = OSM(str(ROOT / "data" / "raw" / "osm_expanded.json"))
    ways = {oid: (t, g) for oid, typ, t, g in osm.areas}

    # load all four tiles into one volume covering x=640..896, y=-4224..-3968
    # tiles: 5_-33 x=640-768 y=-4224--4096; 5_-32 x=640-768 y=-4096--3968
    #        6_-33 x=768-896 y=-4224--4096; 6_-32 x=768-896 y=-4096--3968
    # with 1-cell apron, each 130. We'll work per-tile to keep palettes valid.

    total = 0
    for tx, ty in TILES:
        path = OUT / "tiles" / "1" / f"{tx}_{ty}.bin"
        if not path.exists():
            print("missing", path)
            continue
        vol, gwin = load_vol(path, hmin=80)
        origin = (tx * T.N - 1, ty * T.N - 1)  # grid of vol[:,0,0]
        added = 0
        for wid, h, kind, cited in HALLS:
            if wid not in ways:
                print("missing way", wid, kind)
                continue
            t, g = ways[wid]
            n = paint_hall(vol, gwin, origin, g, h, teak, copper, ridge, eave_overhang=3 if kind == "mahavira" else 2)
            if n:
                print(f"  {tx}_{ty} {kind} way {wid} cells {n} h={h} cited={cited}")
            added += n
        if added == 0:
            print(f"  {tx}_{ty} no overlap")
            continue
        top = int(np.nonzero(vol.reshape(vol.shape[0], -1).any(1))[0].max()) + 1
        counts, runs = T.column_runs(vol[:top])
        T.write_node(path, counts, gwin, runs)
        total += added
        print(f"wrote {path.name} runs {len(runs)}")
    (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    # also 1f: clear foliage voxels that sit inside the new copper roofs? optional; skip
    print("temple cells", total)

    # record overrides
    ov_path = ROOT / "overrides.json"
    ov = json.loads(ov_path.read_text()) if ov_path.exists() else {}
    ov["jingan_temple"] = {
        "note": (
            "Schematic 庑殿重檐 copper roofs on OSM footprints. 大雄宝殿 height 26 m is "
            "from published sources below. 法堂 / 山门 / 钟楼 / 鼓楼 use typological eaves "
            "(no cited hall height used as a hard number). Not a scan, not LiDAR."
        ),
        "mahavira_way": JINGAN_MAHAVIRA_WAY,
        "mahavira_height_m": JINGAN_MAHAVIRA_H,
        "sources": JINGAN_SOURCES,
        "tiles": ["5_-33", "5_-32", "6_-33", "6_-32"],
        "fatang_way": JINGAN_FA_TANG_WAY,
        "fatang_height": "typological (~28 m), not the 32 m figure from a weaker Sohu article",
    }
    ov_path.write_text(json.dumps(ov, ensure_ascii=False, indent=2) + "\n")
    print("updated overrides.json")


if __name__ == "__main__":
    main()
