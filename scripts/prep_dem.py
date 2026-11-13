"""Resample a public DEM onto the rotated 1 m AOI grid and estimate bare-earth ground.

Source: Copernicus GLO-30 (30 m DSM, EPSG:4326), cached in data/raw/.
This is NOT a LiDAR DTM. Tall thin towers barely show at 30 m; dense mid-rise blocks
(the Bund) raise the surface by tens of metres. We therefore:

  * mask OSM building footprints and interpolate from neighbouring cells
  * set OSM water (the Huangpu) to a single water level
  * lightly smooth the result

Output: data/dem_grid.npz with arrays dem (raw sampled DSM, metres) and ground (estimated
terrain, metres, EGM2008-ish as published by Copernicus).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize
from affine import Affine
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from aoi import W, D, RAW, to_world, reload  # noqa: E402
from osm import OSM  # noqa: E402

reload()
from aoi import W, D, to_world  # noqa: E402

DEM = Path(RAW) / "Copernicus_DSM_COG_10_N31_00_E121_00_DEM.tif"
OUT = ROOT / "data" / "dem_grid.npz"


def sample_dem(path: Path, band: int = 256) -> np.ndarray:
    """Bilinear sample the geographic DEM onto the rotated UTM grid, in row bands."""
    from pyproj import Transformer
    from scipy.ndimage import map_coordinates
    t = Transformer.from_crs(32651, 4326, always_xy=True)
    out = np.empty((D, W), np.float32)
    with rasterio.open(path) as src:
        arr = src.read(1).astype(np.float32)
        # pixel (row, col) from lon/lat: affine of a north-up 4326 raster
        # x = a*col + c, y = e*row + f
        a, b, c, d, e, f = src.transform[:6]
        assert abs(b) < 1e-12 and abs(d) < 1e-12, src.transform
        for j0 in range(0, D, band):
            j1 = min(j0 + band, D)
            jj, ii = np.mgrid[j0:j1, 0:W].astype(np.float64)
            x, y = to_world(ii + 0.5, jj + 0.5)
            lon, lat = t.transform(x, y)
            col = (lon - c) / a - 0.5
            row = (lat - f) / e - 0.5
            out[j0:j1] = map_coordinates(arr, [row, col], order=1, mode="nearest")
            print(f"  dem rows {j0}-{j1}")
    return out


def burn(shapes, fill=0, dtype=np.uint8):
    shapes = [(s, 1) for s in shapes if s is not None and not s.is_empty]
    if not shapes:
        return np.zeros((D, W), dtype)
    return rasterize(shapes, out_shape=(D, W), transform=Affine.identity(), fill=fill,
                     all_touched=True, dtype=dtype)


def fill_nan(a: np.ndarray) -> np.ndarray:
    mask = ~np.isfinite(a)
    if not mask.any():
        return a
    _, (ir, ic) = ndimage.distance_transform_edt(mask, return_indices=True)
    return a[ir, ic]


def main():
    print("sampling Copernicus GLO-30 onto", W, "x", D, "grid")
    dem = sample_dem(DEM)
    dem = np.where(np.isfinite(dem), dem, np.nan)
    print("raw dem", "min", np.nanmin(dem), "max", np.nanmax(dem), "nan", int(np.isnan(dem).sum()))

    osm = OSM()
    blds = [g for oid, typ, t, g in osm.areas
            if t.get("building") not in (None, "no") or "building:part" in t]
    water = [g for oid, typ, t, g in osm.areas
             if t.get("natural") == "water" or t.get("waterway") in ("riverbank", "river")
             or t.get("water") in ("river", "canal", "basin", "harbour")
             or t.get("landuse") == "basin"]
    # also wide waterway lines
    for oid, t, l in osm.lines:
        if t.get("waterway") in ("river", "canal") and t.get("tunnel") not in ("yes", "culvert"):
            w = 80 if t.get("waterway") == "river" else 20
            water.append(l.buffer(w / 2))

    bmask = burn(blds)
    wmask = burn(water)
    print("building columns", int(bmask.sum()), "water columns", int(wmask.sum()))

    g = dem.copy()
    g[bmask == 1] = np.nan
    # 0 on the river is a common fill in this tile; treat as nodata over water
    g[(wmask == 1) & (np.nan_to_num(g, nan=0) <= 0.2)] = np.nan
    g = fill_nan(g)
    # water level: median of finite raw samples over water that look like a surface, else 1 m
    wz = dem[(wmask == 1) & np.isfinite(dem) & (dem > -2) & (dem < 8)]
    water_z = float(np.median(wz)) if wz.size > 100 else 1.0
    print("water level", water_z, "m from", int(wz.size), "samples")
    g = np.where(wmask == 1, water_z, g)
    # light smooth on land only
    sm = ndimage.gaussian_filter(g, 3)
    g = np.where(wmask == 1, g, sm)
    # snap single-cell pits
    g = np.round(g, 2)
    print("ground", "min", float(g.min()), "max", float(g.max()))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(OUT, dem=dem.astype(np.float32), ground=g.astype(np.float32),
                        water=wmask, buildings=bmask, water_z=np.array(water_z, np.float32))
    print("wrote", OUT, "MB", OUT.stat().st_size / 2**20)


if __name__ == "__main__":
    main()
