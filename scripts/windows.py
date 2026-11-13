"""Lit-window kinds for every 1 m column (gzip uint8[RH, RW], RH=D-y0, RW=W-x0).

Kind is taken from the OSM building tag, or from a hotel/office/shop/amenity
inside the footprint, or from the surrounding landuse. The high 5 bits are a
stable per-building habit number the viewer uses to vary how fully a facade is
lit.

windows.json holds schematic typological curves (weekday/weekend medians in
the same five clock-time bins the viewer already uses). They are not a counted
Shanghai facade survey and not the Warsaw photo-count medians.
"""
import gzip
import json
from collections import Counter
from pathlib import Path

import numpy as np
from affine import Affine
from rasterio.features import rasterize
from shapely.strtree import STRtree

from aoi import RAW

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "public" / "data"
META = OUT / "meta.json"

KINDS = ["none", "residential", "office", "hotel", "retail", "public", "religious", "other"]
BY_TAG = {
    "residential": {"apartments", "residential", "house", "detached", "semidetached_house", "terrace", "dormitory"},
    "office": {"office", "commercial"},
    "hotel": {"hotel"},
    "retail": {"retail", "supermarket", "kiosk", "mall"},
    "public": {"government", "public", "civic", "university", "school", "college", "kindergarten", "hospital", "museum",
               "theatre", "palace", "train_station", "transportation", "library"},
    "religious": {"church", "cathedral", "chapel", "religious", "synagogue", "mosque", "temple", "shrine"},
}
TAG = {v: k for k, vs in BY_TAG.items() for v in vs}
PUBLIC_AMENITY = {"university", "school", "college", "hospital", "townhall", "courthouse", "library", "theatre",
                  "arts_centre", "kindergarten", "police", "embassy"}

# Schematic typological curves: typical urban mix, not measured on these facades.
# bins: 16-17, 18-19, 20-21, 22-23, 0-6
SCHEMATIC = {
    "bins": [[15, 17], [18, 19], [20, 21], [22, 23], [0, 6]],
    "source": "schematic typological curves (not a counted Shanghai facade survey; not Warsaw photo counts)",
    "types": {
        "residential": {
            "med": {"weekday": [0.14, 0.26, 0.32, 0.22, 0.10], "weekend": [0.16, 0.28, 0.30, 0.22, 0.12]},
            "shape": [0.0, 0.20, 0.50, 0.80, 1.0, 1.20, 1.50, 2.00, 3.00],
            "floors": 0.03,
            "colour": [0.75, 0.20, 0.05],
        },
        "office": {
            "med": {"weekday": [0.34, 0.22, 0.10, 0.08, 0.06], "weekend": [0.08, 0.06, 0.05, 0.05, 0.04]},
            "shape": [0.0, 0.25, 0.55, 0.85, 1.0, 1.15, 1.50, 2.10, 3.20],
            "floors": 0.16,
            "colour": [0.30, 0.55, 0.15],
        },
        "hotel": {
            "med": {"weekday": [0.18, 0.22, 0.24, 0.20, 0.14], "weekend": [0.20, 0.24, 0.24, 0.20, 0.14]},
            "shape": [0.0, 0.40, 0.70, 0.90, 1.0, 1.15, 1.40, 1.90, 2.60],
            "floors": 0.04,
            "colour": [0.78, 0.20, 0.02],
        },
        "public": {
            "med": {"weekday": [0.16, 0.08, 0.05, 0.03, 0.02], "weekend": [0.10, 0.08, 0.05, 0.03, 0.02]},
            "shape": [0.0, 0.10, 0.40, 0.75, 1.0, 1.30, 1.80, 2.50, 4.00],
            "floors": 0.10,
            "colour": [0.60, 0.32, 0.08],
        },
        "religious": {
            "med": {"weekday": [0.08, 0.06, 0.05, 0.04, 0.03], "weekend": [0.10, 0.14, 0.08, 0.04, 0.03]},
            "shape": [0.0, 0.10, 0.35, 0.70, 1.0, 1.40, 2.00, 3.00, 5.00],
            "floors": 0.08,
            "colour": [0.70, 0.25, 0.05],
        },
    },
}


def kind_of(t, inside, landuse):
    k = TAG.get(t.get("building"))
    if k:
        return k
    for tags in [t, *inside]:
        if tags.get("tourism") in ("hotel", "hostel", "apartment"):
            return "hotel"
        if tags.get("amenity") == "place_of_worship" or tags.get("building") in ("temple", "shrine"):
            return "religious"
        if tags.get("amenity") in PUBLIC_AMENITY or tags.get("office") in ("government", "diplomatic"):
            return "public"
    if any("office" in tags for tags in [t, *inside]):
        return "office"
    if t.get("shop") or t.get("building") == "yes" and inside and all("shop" in x for x in inside):
        return "retail"
    return {"residential": "residential", "commercial": "office", "retail": "retail"}.get(landuse, "other")


def extent():
    meta = json.loads(META.read_text())
    x0 = int(meta.get("x0", 0))
    y0 = int(meta.get("y0", 0))
    W, D = int(meta["W"]), int(meta["D"])
    return x0, y0, W, D


def main():
    from osm import OSM
    x0, y0, W, D = extent()
    RW, RH = W - x0, D - y0
    osm_path = ROOT / RAW / "osm_expanded.json"
    if not osm_path.exists():
        osm_path = ROOT / RAW / "osm.json"
    osm = OSM(str(osm_path))
    blds = [(g, t) for _, _, t, g in osm.areas
            if t.get("building") not in (None, "no") and "building:part" not in t and t.get("location") != "underground"]
    blds.sort(key=lambda b: -b[0].area)
    pts = [(t, p) for _, t, p in osm.nodes if any(k in t for k in ("office", "shop", "amenity", "tourism"))]
    ptree = STRtree([p for _, p in pts]) if pts else None
    uses = [(t["landuse"], g) for _, _, t, g in osm.areas if t.get("landuse")]
    utree = STRtree([g for _, g in uses]) if uses else None
    shapes, count = [], Counter()
    for i, (g, t) in enumerate(blds):
        inside = [pts[j][0] for j in ptree.query(g, predicate="contains")] if ptree is not None else []
        c = g.representative_point()
        lu = next((uses[j][0] for j in utree.query(c, predicate="within")), None) if utree is not None else None
        k = kind_of(t, inside, lu)
        count[k] += g.area
        habit = (i * 2654435761 >> 7) % 32
        shapes.append((g, KINDS.index(k) | habit << 3))
    tr = Affine.translation(x0, y0)
    grid = rasterize(shapes, out_shape=(RH, RW), transform=tr, fill=0, dtype=np.uint8) if shapes else np.zeros((RH, RW), np.uint8)
    (OUT / "windows.bin").write_bytes(gzip.compress(grid.tobytes(), mtime=0, compresslevel=9))
    json.dump(SCHEMATIC, open(OUT / "windows.json", "w"), indent=2, ensure_ascii=False)
    from raster_chunks import write_uint_chunks, record_index
    write_uint_chunks(grid, OUT / "windows")
    record_index(x0, y0, RW, RH)
    total = sum(count.values()) or 1
    print({k: f"{v / total:.0%}" for k, v in count.most_common()},
          "grid", grid.shape, "lit cols", int((grid > 0).sum()),
          (OUT / "windows.bin").stat().st_size, "bytes")


if __name__ == "__main__":
    main()
