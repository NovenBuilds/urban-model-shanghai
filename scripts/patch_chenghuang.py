"""Cited schematic for Shanghai City God Temple 大殿 (歇山顶, 16 m, green glazed tiles).

Plan from OSM way 1576988166 (area ~257 m², close to published 13.7 × 21.1 m).
Height 16 m, depth 21.1 m, 歇山顶, 翠瓦朱檐: zh.wikipedia and Huangpu heritage notes.
Not a scan, not LiDAR. Yu Garden halls have no similarly cited hall heights here,
so they stay OSM massing.

Targeted restamp of typ 0_2 and 0_3 only (the hall straddles y=384).
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
from affine import Affine
from rasterio.features import rasterize
import tiles as T
from osm import OSM

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "public" / "data"
HALL_WAY = 1576988166
HALL_H = 16.0
SOURCES = [
    "https://zh.wikipedia.org/wiki/%E4%B8%8A%E6%B5%B7%E5%9F%8E%E9%9A%8D%E5%BB%9F",
    "http://blog.sina.com.cn/s/blog_5d1bdf480102wmhs.html",
]
TILES = ((0, 2), (0, 3))  # x=0..128, y=256..512


def append_palette(meta, rgb, mat):
    pal = meta["palette"]
    for i, e in enumerate(pal):
        if list(e[:3]) == list(rgb) and int(e[3]) == mat:
            return i, False
    pal.append([int(rgb[0]), int(rgb[1]), int(rgb[2]), int(mat)])
    return len(pal) - 1, True


def paint_xieshan(vol, gwin, origin, geom, height_m, wall_id, roof_id, ridge_id):
    """Schematic 歇山 (hip-and-gable): hips on the short ends, a ridge on the long axis."""
    ox, oy = origin
    H, rows, cols = vol.shape
    if geom is None or geom.is_empty:
        return 0
    tr = Affine.translation(ox, oy)
    foot = rasterize([(geom, 1)], out_shape=(rows, cols), transform=tr, fill=0, dtype=np.uint8)
    eave = rasterize([(geom.buffer(2), 1)], out_shape=(rows, cols), transform=tr, fill=0, all_touched=True, dtype=np.uint8)
    if not foot.any():
        return 0
    fr, fc = np.nonzero(foot)
    fx, fy = fc + ox, fr + oy
    span_x = fx.max() - fx.min() + 1
    span_y = fy.max() - fy.min() + 1
    cx, cy = float(fx.mean()), float(fy.mean())
    ridge_along_y = span_y >= span_x
    n = 0
    for r, c in zip(*np.nonzero(eave)):
        gz = int(gwin[r, c])
        cap = min(H, gz + int(round(height_m)) + 3)
        if cap > gz:
            vol[gz:cap, r, c] = 0
    for r, c in zip(*np.nonzero(foot)):
        gz = int(gwin[r, c])
        x, y = c + ox + 0.5, r + oy + 0.5
        if ridge_along_y:
            t = abs(x - cx) / max(1.0, span_x / 2.0)  # 0 at ridge, 1 at eave
            u = abs(y - cy) / max(1.0, span_y / 2.0)
        else:
            t = abs(y - cy) / max(1.0, span_y / 2.0)
            u = abs(x - cx) / max(1.0, span_x / 2.0)
        z_wall = gz + int(round(height_m * 0.62))
        z_eave = gz + int(round(height_m * 0.70))
        z_ridge = gz + int(round(height_m))
        vol[gz:z_wall, r, c] = wall_id
        # hip slope, with a gable return near the short ends (u > 0.72 stays lower like a 歇山 gable wall)
        if u > 0.72:
            z1 = int(round(z_eave + (z_ridge - z_eave) * max(0.0, 1.0 - t) * 0.45))
        else:
            z1 = int(round(z_eave + (z_ridge - z_eave) * max(0.0, 1.0 - t)))
        z1 = min(H, max(z_wall + 1, z1))
        vol[z_wall:z1, r, c] = roof_id
        if t < 0.12 and u < 0.72:
            lo = min(H - 1, z_ridge - 1)
            vol[lo:min(H, z_ridge + 1), r, c] = ridge_id
        n += 1
    for r, c in zip(*np.nonzero((eave == 1) & (foot == 0))):
        gz = int(gwin[r, c])
        z0 = gz + int(round(height_m * 0.66))
        z1 = gz + int(round(height_m * 0.72)) + 1
        vol[max(0, z0):min(H, z1), r, c] = roof_id
        n += 1
    return n


def main():
    meta = json.loads((OUT / "meta.json").read_text())
    glaze, _ = append_palette(meta, [62, 110, 78], 12)   # 翠瓦
    ridge, _ = append_palette(meta, [80, 70, 52], 12)
    wall, _ = append_palette(meta, [176, 64, 48], 1)     # 朱墙
    osm = OSM(str(ROOT / "data" / "raw" / "osm_expanded.json"))
    ways = {oid: (t, g) for oid, typ, t, g in osm.areas}
    if HALL_WAY not in ways:
        raise SystemExit(f"missing hall way {HALL_WAY}")
    _, geom = ways[HALL_WAY]
    total = 0
    for tx, ty in TILES:
        path = OUT / "tiles" / "1" / f"{tx}_{ty}.bin"
        counts, gwin, runs = T.load_node(path)
        h = 80
        if len(runs):
            h = max(h, int((runs[:, 0] + runs[:, 1]).max()) + 8)
        vol = T.vol_from_runs(counts, runs, h=h)
        origin = (tx * T.N - 1, ty * T.N - 1)
        n = paint_xieshan(vol, gwin, origin, geom, HALL_H, wall, glaze, ridge)
        print(f"  {tx}_{ty} cells {n}")
        if n == 0:
            continue
        top = int(np.nonzero(vol.reshape(vol.shape[0], -1).any(1))[0].max()) + 1
        counts, runs = T.column_runs(vol[:top])
        T.write_node(path, counts, gwin, runs)
        total += n
    (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    ovp = ROOT / "scripts" / "overrides.json"
    ov = json.loads(ovp.read_text())
    ov["chenghuang_dian"] = {
        "note": (
            "Schematic 歇山顶 green-glazed roof on the City God Temple main hall. "
            "Height 16 m and depth 21.1 m from published sources. Plan is the OSM "
            "footprint (way 1576988166, area close to the published 13.7 × 21.1 m). "
            "Not a scan, not LiDAR. Yu Garden named halls stay OSM massing "
            "(no similarly cited hall heights used)."
        ),
        "way": HALL_WAY,
        "height_m": HALL_H,
        "roof": "歇山顶",
        "sources": SOURCES,
        "tiles": ["0_2", "0_3"],
    }
    ovp.write_text(json.dumps(ov, ensure_ascii=False, indent=2) + "\n")
    print("temple cells", total)


if __name__ == "__main__":
    main()
