"""Shanghai area of interest and the citywide grid it sits in.

World coords: EPSG:32651 (UTM zone 51N), x = easting, y = northing.
Grid coords: (i, j) in metres, i along the Bund / Huangpu's local axis, j perpendicular
toward Pudong (growing inland from Puxi). Cell (c, r) spans [c, c+1) × [r, r+1).

The grid is anchored on the Oriental Pearl (东方明珠) and rotated so the Bund line and
river walls fall on voxel faces instead of staircases — the same idea as Warsaw's 21.5°
rotation around the Palace of Culture.

Citywide layout (so later tiles do not rewrite this AOI):
  A 100 km × 100 km rotated grid with the Pearl at (GLOBAL_I0, GLOBAL_J0) = (50000, 50000).
  This first window is a local 0..W × 0..D crop. meta.json records global_i0 / global_j0,
  the global index of local cell (0, 0). Tile files in v1 use local indices (0_0, …);
  a manifest lists the matching global tile indices.

Runtime values (theta, exact Pearl coordinates, I0/J0) are measured from OSM during
`sh scripts/build_data.sh` and cached in data/aoi.json; this module loads that file when
present, otherwise falls back to the defaults below.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
RAW = "data/raw"
AOI_FILE = ROOT / "data" / "aoi.json"

# Defaults (refined at build time). Pearl: widely published coordinates; OSM may adjust.
CX, CY = 357124.18, 3457137.48  # Oriental Pearl, EPSG:32651
THETA_DEG = 57.0  # ~ Bund azimuth 33° from north; measured from 中山东一路 at build
THETA = math.radians(THETA_DEG)
# First slice: ~4.1 × 3.1 km (multiples of the 128 m tile) covering the Bund to Lujiazui.
W, D = 4096, 3072
I0, J0 = 2000, 1150  # local grid position of the Pearl
GLOBAL_I0, GLOBAL_J0 = 50000, 50000  # Pearl in the citywide grid
CRS = "EPSG:32651"

EU = np.array([math.cos(THETA), math.sin(THETA)])   # grid +i in world
EV = np.array([math.sin(THETA), -math.cos(THETA)])  # grid +j in world


def _apply(d: dict) -> None:
    global CX, CY, THETA_DEG, THETA, W, D, I0, J0, GLOBAL_I0, GLOBAL_J0, EU, EV, CRS
    CX, CY = float(d["CX"]), float(d["CY"])
    THETA_DEG = float(d["THETA_DEG"])
    THETA = math.radians(THETA_DEG)
    W, D = int(d["W"]), int(d["D"])
    I0, J0 = int(d["I0"]), int(d["J0"])
    GLOBAL_I0 = int(d.get("GLOBAL_I0", GLOBAL_I0))
    GLOBAL_J0 = int(d.get("GLOBAL_J0", GLOBAL_J0))
    CRS = d.get("CRS", CRS)
    EU = np.array([math.cos(THETA), math.sin(THETA)])
    EV = np.array([math.sin(THETA), -math.cos(THETA)])


def reload() -> None:
    if AOI_FILE.exists():
        _apply(json.loads(AOI_FILE.read_text()))


def save(path: Path | None = None) -> None:
    path = path or AOI_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(
        CX=CX, CY=CY, THETA_DEG=THETA_DEG, W=W, D=D, I0=I0, J0=J0,
        GLOBAL_I0=GLOBAL_I0, GLOBAL_J0=GLOBAL_J0, CRS=CRS,
        global_i0=GLOBAL_I0 - I0, global_j0=GLOBAL_J0 - J0,
        note="global_i0/j0 are the citywide indices of local cell (0,0)",
    ), indent=2))


def to_grid(x, y):
    dx, dy = np.asarray(x) - CX, np.asarray(y) - CY
    return I0 + dx * EU[0] + dy * EU[1], J0 + dx * EV[0] + dy * EV[1]


def to_world(i, j):
    u, v = np.asarray(i) - I0, np.asarray(j) - J0
    return CX + u * EU[0] + v * EV[0], CY + u * EU[1] + v * EV[1]


def world_bounds(margin=0):
    xs, ys = to_world(np.array([0, W, W, 0]), np.array([0, 0, D, D]))
    return xs.min() - margin, ys.min() - margin, xs.max() + margin, ys.max() + margin


def wgs84_bounds(margin_m=250):
    """WGS84 (S, W, N, E) covering the rotated rectangle plus a metre margin."""
    from pyproj import Transformer
    t = Transformer.from_crs(32651, 4326, always_xy=True)
    xs, ys = to_world(np.array([0, W, W, 0]), np.array([0, 0, D, D]))
    # expand in world metres first
    minx, miny, maxx, maxy = xs.min() - margin_m, ys.min() - margin_m, xs.max() + margin_m, ys.max() + margin_m
    corners = [(minx, miny), (minx, maxy), (maxx, miny), (maxx, maxy)]
    lons, lats = zip(*[t.transform(x, y) for x, y in corners])
    return min(lats), min(lons), max(lats), max(lons)


reload()
