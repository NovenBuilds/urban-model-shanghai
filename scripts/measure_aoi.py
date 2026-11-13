"""Measure the Bund's orientation from OSM and place the first AOI window.

Writes data/aoi.json, which aoi.py loads on import after this has run.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from pyproj import Transformer

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import aoi as A  # noqa: E402

PBF_BBOX_DEFAULT = "31.215,121.455,31.260,121.535"  # S,W,N,E generous cut around Bund–Lujiazui


def _names(tags: dict) -> str:
    return " ".join(str(tags.get(k, "")) for k in ("name", "name:zh", "name:en", "name:zh-Hans", "alt_name"))


def _is_pearl(tags: dict) -> bool:
    n = _names(tags)
    return "东方明珠" in n or "Oriental Pearl" in n


def _is_bund_road(tags: dict) -> bool:
    n = _names(tags)
    # The classic Bund carriageway, not 中山东二路 further north.
    if "中山东一路" in n or "Zhongshan East 1st" in n or "Zhongshan Rd. E. 1" in n:
        return True
    if tags.get("highway") and ("the Bund" in n or n.strip() in {"外滩", "The Bund", "Waitan"}):
        return True
    return False


def mean_bearing_from_east(coords_xy: list[tuple[float, float]]) -> float | None:
    """Length-weighted mean direction of segments, returned as Warsaw-style theta
    (angle of +i from +east, degrees, in (-90, 90]).

    We want +i along the Bund (heading roughly NNE) and +j toward Pudong (ESE).
    """
    acc_x = acc_y = 0.0  # double-angle sum so 0° and 180° agree
    total = 0.0
    for (x0, y0), (x1, y1) in zip(coords_xy, coords_xy[1:]):
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length < 8:
            continue
        # direction modulo 180°
        ang = math.atan2(dy, dx)  # from +east, ccw
        acc_x += math.cos(2 * ang) * length
        acc_y += math.sin(2 * ang) * length
        total += length
    if total < 80:
        return None
    # half of the double angle
    twice = math.atan2(acc_y, acc_x)
    theta = 0.5 * twice  # in (-90°, 90°] from east
    # Prefer the orientation whose +i points northward (Bund runs NNE, not SSW)
    # EU = (cos theta, sin theta); north component is sin theta.
    if math.sin(theta) < 0:
        theta += math.pi
    # wrap to (-pi, pi]
    theta = (theta + math.pi) % (2 * math.pi) - math.pi
    return math.degrees(theta)


def main():
    osm_path = ROOT / "data" / "raw" / "osm.json"
    data = json.loads(osm_path.read_text())
    t = Transformer.from_crs(4326, "EPSG:32651", always_xy=True)

    pearl = None
    bund_xy: list[list[tuple[float, float]]] = []
    for e in data["elements"]:
        tags = e.get("tags") or {}
        if e["type"] in ("node", "way", "relation") and _is_pearl(tags):
            if e["type"] == "node" and "lat" in e:
                pearl = t.transform(e["lon"], e["lat"])
            elif e["type"] == "way" and e.get("geometry"):
                xs, ys = [], []
                for p in e["geometry"]:
                    if p:
                        x, y = t.transform(p["lon"], p["lat"])
                        xs.append(x); ys.append(y)
                if xs:
                    pearl = (float(np.mean(xs)), float(np.mean(ys)))
        if e["type"] == "way" and e.get("geometry") and _is_bund_road(tags):
            xy = [t.transform(p["lon"], p["lat"]) for p in e["geometry"] if p]
            if len(xy) >= 2:
                bund_xy.append(xy)

    if pearl:
        A.CX, A.CY = float(pearl[0]), float(pearl[1])
        print(f"Pearl from OSM: E={A.CX:.2f} N={A.CY:.2f}")
    else:
        print(f"Pearl OSM node not found, keeping default E={A.CX:.2f} N={A.CY:.2f}")

    # Flatten Bund ways for a single bearing
    flat = [p for way in bund_xy for p in (way + [(math.nan, math.nan)])]
    # compute per-way and average
    thetas = []
    weights = []
    for way in bund_xy:
        th = mean_bearing_from_east(way)
        if th is None:
            continue
        length = sum(math.hypot(x1 - x0, y1 - y0) for (x0, y0), (x1, y1) in zip(way, way[1:]))
        thetas.append(th)
        weights.append(length)
        print(f"  Bund way length {length:.0f}m  theta={th:.2f}°")
    if thetas:
        # circular-ish mean on the 180° line already unwrapped around the first
        ref = thetas[0]
        adj = [ref + ((th - ref + 90) % 180) - 90 for th in thetas]
        theta = float(np.average(adj, weights=weights))
        A.THETA_DEG = round(theta, 2)
        print(f"Bund-derived THETA_DEG={A.THETA_DEG} (Warsaw convention, from +east)")
    else:
        print(f"No Bund road found, keeping THETA_DEG={A.THETA_DEG}")

    A.THETA = math.radians(A.THETA_DEG)
    A.EU = np.array([math.cos(A.THETA), math.sin(A.THETA)])
    A.EV = np.array([math.sin(A.THETA), -math.cos(A.THETA)])

    # Fit I0,J0 so key landmarks sit comfortably inside the window.
    # Landmark approximate WGS84 (used only to place the window; voxel positions come from OSM later).
    key = {
        "pearl": (A.CX, A.CY),
        "tower": Transformer.from_crs(4326, 32651, always_xy=True).transform(121.50556, 31.23351),
        "jinmao": Transformer.from_crs(4326, 32651, always_xy=True).transform(121.50637, 31.23525),
        "swfc": Transformer.from_crs(4326, 32651, always_xy=True).transform(121.50163, 31.23524),
        "customs": Transformer.from_crs(4326, 32651, always_xy=True).transform(121.4854, 31.2382),
        "waibaidu": Transformer.from_crs(4326, 32651, always_xy=True).transform(121.4908, 31.2456),
        "peace": Transformer.from_crs(4326, 32651, always_xy=True).transform(121.4866, 31.2410),
    }
    # Temporarily set I0=J0=0 at the Pearl to read relative grid coords
    A.I0, A.J0 = 0, 0
    rel = {n: A.to_grid(xy[0], xy[1]) for n, xy in key.items()}
    for n, (i, j) in rel.items():
        print(f"  {n:10} relative i={float(i):7.1f} j={float(j):7.1f}")
    margin = 280
    i_min = min(float(i) for i, j in rel.values()) - margin
    i_max = max(float(i) for i, j in rel.values()) + margin
    j_min = min(float(j) for i, j in rel.values()) - margin
    j_max = max(float(j) for i, j in rel.values()) + margin
    # Place the window so the bounding box sits inside, Pearl not too close to an edge
    # Prefer leftover space toward Pudong (+j) and along the Bund both ways.
    need_i, need_j = i_max - i_min, j_max - j_min
    if need_i > A.W - 40 or need_j > A.D - 40:
        print(f"WARNING: landmark bbox {need_i:.0f}×{need_j:.0f} m is tight for {A.W}×{A.D}")
    # Pearl local position: shift so bbox starts at ~40 m padding
    A.I0 = int(round(-i_min + 80))
    A.J0 = int(round(-j_min + 80))
    # Keep Pearl reasonably central if there is slack
    A.I0 = int(np.clip(A.I0, 400, A.W - 400))
    A.J0 = int(np.clip(A.J0, 300, A.D - 400))
    # Verify
    ok = True
    for n, xy in key.items():
        i, j = A.to_grid(xy[0], xy[1])
        inside = 40 <= i < A.W - 40 and 40 <= j < A.D - 40
        print(f"  placed {n:10} i={float(i):7.1f} j={float(j):7.1f}  {'OK' if inside else 'OUT'}")
        ok = ok and inside
    A.save()
    print("wrote", A.AOI_FILE, "ok" if ok else "SOME LANDMARKS OUT")
    S, W_, N, E = A.wgs84_bounds(400)
    print(f"WGS84 extract bbox (S,W,N,E) = {S:.5f},{W_:.5f},{N:.5f},{E:.5f}")


if __name__ == "__main__":
    main()
