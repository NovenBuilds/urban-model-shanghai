"""Hand-authored, cited adjustments on top of OSM + Copernicus GLO-30.

Not LiDAR. Used by voxelize.py (ground + landmark sculpture) and traffic.py (z_at).
"""
from __future__ import annotations

import math

import numpy as np
from scipy.ndimage import binary_dilation, minimum_filter1d, uniform_filter1d
from shapely.geometry import LineString

# Bund / Huangpu flood-wall crest, 苏州河～复兴东路, Shanghai Wusong datum.
# Shanghai Water Authority 2024 design table, and the 1989–1993 Bund rebuild.
WALL_CREST_M = 6.9
# 1997 Xinmin Evening News: a 5.72 m tide stood 2.5 m above Bund street level.
BUND_STREET_M = 3.2
PROMENADE_W = 14
WALL_T = 3
RAMP_W = 12
RIVER_MIN_WIDTH = 80

# Waibaidu (Garden) Bridge — camelback through truss, two equal spans.
# Official / published figures; the OSM centreline is used for placement.
WAIBAIDU_WAY = 27498117
WAIBAIDU_LEN = 104.24          # m, zh.wikipedia / baike
WAIBAIDU_SPAN = 52.178         # m, two equal spans
WAIBAIDU_WIDTH = 18.4          # m overall
WAIBAIDU_ROAD = 11.2           # m carriageway
WAIBAIDU_TRUSS_GAP = 12.192    # m, centres of the two main trusses
WAIBAIDU_H_MID = 9.144         # m, truss height at mid-span
WAIBAIDU_PANEL = 4.744         # m, 11 nodes / 10 panels per span
WAIBAIDU_NODES = 11
WAIBAIDU_DECK_M = 7.0          # m Wusong: beam soffit 5.75 m + ~1.25 m structure
WAIBAIDU_SOURCES = [
    "https://zh.wikipedia.org/wiki/%E5%A4%96%E7%99%BD%E6%B8%A1%E6%A1%A5",
    "https://en.wikipedia.org/wiki/Waibaidu_Bridge",
    "https://swj.sh.gov.cn/swyw/20240812/5d55065d81ab4d2299cb718f0349dcd8.html",
]

DRIVE = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
    "residential", "living_street", "pedestrian", "service",
    "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link",
}
ROAD_W = {
    "motorway": 22, "motorway_link": 10, "trunk": 18, "trunk_link": 8,
    "primary": 16, "primary_link": 7, "secondary": 12, "secondary_link": 6,
    "tertiary": 10, "tertiary_link": 6, "unclassified": 8, "residential": 8,
    "living_street": 7, "service": 5, "pedestrian": 14, "footway": 4, "path": 3,
}


def _num(v, default=None):
    try:
        return float(str(v).split(";")[0].split()[0].replace(",", "."))
    except (ValueError, IndexError, AttributeError, TypeError):
        return default


def _densify(coords, step=2.0):
    out = [coords[0]]
    for a, b in zip(coords, coords[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        if L < 1e-6:
            continue
        n = max(1, int(L // step))
        for k in range(1, n + 1):
            f = k / n
            out.append((a[0] + dx * f, a[1] + dy * f))
    return out


def flatten_highways(ground_m, osm, water_mask, origin=(0, 0)):
    """Lower DSM spikes on OSM streets to a smooth, walkable grade.

    Copernicus GLO-30 is a 30 m DSM. Building-masking often leaves 20–30 m
    'hills' in street canyons; East Nanjing Road became a staircase of that.
    We only ever lower ground, and we leave water alone.

    `origin` is the grid (x, y) of array cell (row 0, col 0), so a west window
    with y starting at -2048 can share this helper.
    """
    H, W = ground_m.shape
    ox, oy = origin
    prop = np.full((H, W), np.inf, np.float32)

    def paint(x, y, half, z):
        # x, y are already in array coordinates
        r = max(2, int(round(half)))
        c0, c1 = max(0, int(x) - r), min(W, int(x) + r + 1)
        r0, r1 = max(0, int(y) - r), min(H, int(y) + r + 1)
        if c0 >= c1 or r0 >= r1:
            return
        yy, xx = np.ogrid[r0:r1, c0:c1]
        disk = (xx + 0.5 - x) ** 2 + (yy + 0.5 - y) ** 2 <= (half + 0.4) ** 2
        sl = prop[r0:r1, c0:c1]
        sl[disk] = np.minimum(sl[disk], z)

    for oid, t, line in osm.lines:
        hwy = t.get("highway")
        if hwy not in DRIVE and not (hwy in ("footway", "path") and t.get("name")):
            continue
        if t.get("bridge") not in (None, "no"):
            continue
        if t.get("tunnel") in ("yes", "building_passage", "culvert"):
            continue
        coords = list(line.coords)
        if len(coords) < 2:
            continue
        pts = _densify(coords, 2.0)
        raw = []
        for x, y in pts:
            ci, ri = int(round(x - ox)), int(round(y - oy))
            if not (0 <= ci < W and 0 <= ri < H) or water_mask[ri, ci]:
                raw.append(np.nan)
                continue
            r0, r1 = max(0, ri - 2), min(H, ri + 3)
            c0, c1 = max(0, ci - 2), min(W, ci + 3)
            local = float(np.min(ground_m[r0:r1, c0:c1]))
            # 25 m window, 10th percentile of plausible street heights (2–16 m):
            # street canyons in GLO-30 are often a solid 20–35 m plateau
            R = 40
            rr0, rr1 = max(0, ri - R), min(H, ri + R + 1)
            cc0, cc1 = max(0, ci - R), min(W, ci + R + 1)
            patch = ground_m[rr0:rr1, cc0:cc1]
            land = (water_mask[rr0:rr1, cc0:cc1] == 0) & (patch >= 1.5) & (patch <= 20.0)
            if land.any():
                wide = float(np.percentile(patch[land], 5))
                local = min(local, wide + 1.0)
            raw.append(local)
        raw = np.array(raw, np.float32)
        if np.isfinite(raw).sum() < 3:
            continue
        # fill tiny gaps, then a rolling minimum (kills building spikes) and a light smooth
        idx = np.arange(len(raw))
        good = np.isfinite(raw)
        filled = raw.copy()
        filled[~good] = np.interp(idx[~good], idx[good], raw[good])
        win = 15 if len(filled) >= 15 else max(3, len(filled) // 2 * 2 + 1)
        low = minimum_filter1d(filled, size=win, mode="nearest")
        sm = uniform_filter1d(low, size=min(11, win), mode="nearest")
        # cap *climbs* at 4 % so a street cannot run up a building, but always
        # allow descent toward a known low (the Bund end of East Nanjing Road)
        step = 2.0
        max_dz = 0.04 * step
        for i in range(1, len(sm)):
            sm[i] = min(sm[i], sm[i - 1] + max_dz)
        for i in range(len(sm) - 2, -1, -1):
            sm[i] = min(sm[i], sm[i + 1] + max_dz)
        half = ( _num(t.get("width")) or ROAD_W.get(hwy, 8) ) / 2.0
        if "南京" in (t.get("name") or "") or "Nanjing" in (t.get("name:en") or ""):
            half = max(half, 18.0)
        for (x, y), z in zip(pts, sm):
            xa, ya = x - ox, y - oy
            if not (0 <= xa < W and 0 <= ya < H):
                continue
            paint(xa, ya, half, float(z))

    hit = np.isfinite(prop) & (water_mask == 0)
    lowered = hit & (prop + 0.15 < ground_m)
    out = ground_m.copy()
    out[lowered] = prop[lowered]
    return out, int(lowered.sum())


def flatten_parks(ground_m, osm, water_mask, origin=(0, 0), building_mask=None):
    """Lower leftover GLO-30 steps inside OSM parks, gardens and paved squares.

    Only lowers, toward a robust low percentile of already-plausible ground
    (2–16 m) inside the same park. Not LiDAR; no invented topography.
    """
    from affine import Affine
    from rasterio.features import rasterize
    from scipy.ndimage import uniform_filter

    H, W = ground_m.shape
    ox, oy = origin
    transform = Affine.translation(ox, oy)
    park_geoms = []
    for oid, typ, t, g in osm.areas:
        if g is None or g.is_empty:
            continue
        lu = t.get("leisure") or t.get("landuse") or t.get("place")
        if lu in ("park", "garden", "village_green", "playground", "pitch",
                  "recreation_ground", "square", "grass"):
            park_geoms.append(g)
        elif t.get("highway") == "pedestrian" and g.area and g.area > 400:
            park_geoms.append(g)
    if not park_geoms:
        return ground_m, 0
    shapes = [(g, i + 1) for i, g in enumerate(park_geoms)]
    lab = rasterize(shapes, out_shape=(H, W), transform=transform, fill=0, dtype=np.uint16)
    out = ground_m.copy()
    bld = building_mask.astype(bool) if building_mask is not None else np.zeros((H, W), bool)
    n = 0
    for i in range(1, int(lab.max()) + 1):
        m = (lab == i) & (water_mask == 0) & (~bld)
        if int(m.sum()) < 20:
            continue
        vals = out[m]
        plausible = vals[(vals >= 1.5) & (vals <= 16.0)]
        target = float(np.percentile(plausible if plausible.size >= 10 else vals, 20))
        high = m & (out > target + 0.6)
        out[high] = np.minimum(out[high], target + 0.3)
        n += int(high.sum())
    union = (lab > 0) & (water_mask == 0) & (~bld)
    if union.any():
        sm = uniform_filter(out.astype(np.float32), size=7)
        lower = union & (sm + 0.15 < out)
        out[lower] = sm[lower]
        n = int(((ground_m - out) > 0.2).sum())
    return out, n


def bund_waterfront(ground_m, water_mask, buildings=None):
    """Replace the smeared GLO-30 ramp with a 6.9 m quay + promenade + short ramp.

    Applies to Huangpu banks (water runs at least RIVER_MIN_WIDTH m). Not LiDAR:
    crest and typical Bund street height are published figures (see WALL_CREST_M).
    """
    H, W = ground_m.shape
    out = ground_m.copy()
    promenade = np.zeros((H, W), bool)
    wall = np.zeros((H, W), bool)
    bld = buildings.astype(bool) if buildings is not None else np.zeros((H, W), bool)
    water = water_mask.astype(bool)
    n_col = 0
    for x in range(W):
        col = water[:, x]
        # runs of water
        padded = np.concatenate([[False], col, [False]])
        diff = np.diff(padded.astype(np.int8))
        starts = np.flatnonzero(diff == 1)
        ends = np.flatnonzero(diff == -1)
        for a, b in zip(starts, ends):
            if (b - a) < RIVER_MIN_WIDTH:
                continue
            n_col += 1
            # Puxi bank (smaller j): land just before water
            _stamp_bank(out, promenade, wall, bld, water, x, edge=a, inland=-1,
                        h=H, w=W)
            # Pudong bank (larger j): land just after water
            _stamp_bank(out, promenade, wall, bld, water, x, edge=b - 1, inland=+1,
                        h=H, w=W)
    return out, promenade, wall, n_col


def _stamp_bank(ground, promenade, wall, bld, water, x, edge, inland, h, w):
    """edge = last/first water cell; inland = -1 toward Puxi, +1 toward Pudong."""
    # wall occupies the water-edge landward cells
    for t in range(0, WALL_T):
        j = edge + inland * (t + 1)
        if not (0 <= j < h) or bld[j, x] or water[j, x]:
            continue
        # skip if this cell is already deep inland (edge finding error)
        ground[j, x] = WALL_CREST_M
        wall[j, x] = True
    # promenade further inland at crest
    for t in range(WALL_T, WALL_T + PROMENADE_W):
        j = edge + inland * (t + 1)
        if not (0 <= j < h) or bld[j, x] or water[j, x]:
            continue
        ground[j, x] = WALL_CREST_M
        promenade[j, x] = True
    # short ramp down to local street (or the published Bund street height)
    j_in = edge + inland * (WALL_T + PROMENADE_W + 1)
    if 0 <= j_in < h and not water[j_in, x]:
        # a representative inland height 20–40 m further in, ignoring spikes
        samples = []
        for t in range(WALL_T + PROMENADE_W + 8, WALL_T + PROMENADE_W + 40):
            j = edge + inland * t
            if 0 <= j < h and not water[j, x] and not bld[j, x]:
                samples.append(float(ground[j, x]))
        street = BUND_STREET_M
        if samples:
            street = float(np.percentile(samples, 20))
            street = min(max(street, 1.5), WALL_CREST_M - 1.0)
        for k in range(RAMP_W):
            j = edge + inland * (WALL_T + PROMENADE_W + 1 + k)
            if not (0 <= j < h) or bld[j, x] or water[j, x]:
                continue
            f = (k + 1) / RAMP_W
            ground[j, x] = WALL_CREST_M * (1 - f) + street * f


def apply(ground_m, water_mask, osm, buildings=None):
    g1, n_flat = flatten_highways(ground_m, osm, water_mask)
    g2, promenade, wall, n_col = bund_waterfront(g1, water_mask, buildings)
    return g2, promenade, wall, dict(road_cells=n_flat, waterfront_cols=n_col)


# ---------------------------------------------------------------------------
# Waibaidu camelback truss
# ---------------------------------------------------------------------------

def _waibaidu_axis(osm):
    for oid, t, line in osm.lines:
        if oid == WAIBAIDU_WAY or (t.get("name") in ("外白渡桥", "Waibaidu Bridge") and t.get("bridge") not in (None, "no") and t.get("highway")):
            c = list(line.coords)
            if len(c) >= 2:
                return c[0], c[-1]
    return None


def camelback_h(d, span=WAIBAIDU_SPAN, h_mid=WAIBAIDU_H_MID, h_end=2.6):
    """Height of the polygonal/sine top chord above the deck, 0 at the piers."""
    u = min(max(d / span, 0.0), 1.0)
    return h_end + (h_mid - h_end) * math.sin(math.pi * u)


def fill_waibaidu(vol, gwin, c0, r0, rows, cols, base, steel_id, deck_id, pier_id, osm):
    """Schematic riveted camelback truss from published dimensions (see overrides)."""
    axis = _waibaidu_axis(osm)
    if not axis:
        return 0
    (x0, y0), (x1, y1) = axis
    dx, dy = x1 - x0, y1 - y0
    L = math.hypot(dx, dy)
    if L < 20:
        return 0
    ux, uy = dx / L, dy / L
    vx, vy = -uy, ux
    minx, maxx = min(x0, x1) - 16, max(x0, x1) + 16
    miny, maxy = min(y0, y1) - 16, max(y0, y1) + 16
    if c0 + cols < minx or c0 > maxx or r0 + rows < miny or r0 > maxy:
        return 0

    z_deck = int(round(WAIBAIDU_DECK_M - base))
    z_deck = max(2, min(vol.shape[0] - 14, z_deck))
    half_w = WAIBAIDU_WIDTH / 2.0
    truss_t = WAIBAIDU_TRUSS_GAP / 2.0
    mid_s = L / 2.0
    n_set = 0

    def put(z, rr, cc, pid):
        nonlocal n_set
        if 0 <= rr < rows and 0 <= cc < cols and 0 <= z < vol.shape[0]:
            vol[z, rr, cc] = pid
            n_set += 1

    def span_of(s):
        if s <= mid_s:
            return 0.0, mid_s, s
        return mid_s, L - mid_s, s - mid_s

    # node s-positions for verticals
    node_s = []
    for s0, span, _ in ((0.0, mid_s, 0), (mid_s, L - mid_s, 0)):
        for i in range(WAIBAIDU_NODES):
            node_s.append(s0 + span * (i / (WAIBAIDU_NODES - 1)))

    for rr in range(rows):
        for cc in range(cols):
            x, y = c0 + cc + 0.5, r0 + rr + 0.5
            s = (x - x0) * ux + (y - y0) * uy
            t = (x - x0) * vx + (y - y0) * vy
            if s < -3 or s > L + 3 or abs(t) > half_w + 2:
                continue
            # deck, official 18.4 m width, 2 voxels thick
            if 0 <= s <= L and abs(t) <= half_w:
                put(z_deck, rr, cc, deck_id)
                put(z_deck - 1, rr, cc, deck_id)
                if abs(t) >= half_w - 1.15:
                    put(z_deck + 1, rr, cc, steel_id)
            # mid pier
            if abs(s - mid_s) <= 3.5 and abs(t) <= 4.5:
                gz = int(gwin[rr, cc]) if 0 <= rr < gwin.shape[0] and 0 <= cc < gwin.shape[1] else z_deck - 6
                for z in range(max(1, gz), z_deck):
                    put(z, rr, cc, pier_id)

            # truss planes at ±6.096 m
            if not (0 <= s <= L) or abs(abs(t) - truss_t) > 1.25:
                continue
            s0, span, d = span_of(s)
            h = camelback_h(d * WAIBAIDU_SPAN / max(span, 1e-3))
            z_top = z_deck + max(2, int(round(h)))
            # bottom chord
            put(z_deck + 1, rr, cc, steel_id)
            put(z_deck + 2, rr, cc, steel_id)
            # top chord (2 voxels)
            put(z_top, rr, cc, steel_id)
            put(z_top - 1, rr, cc, steel_id)
            # verticals near a panel node
            if any(abs(s - ns) <= 1.05 for ns in node_s):
                for z in range(z_deck + 1, z_top + 1):
                    put(z, rr, cc, steel_id)
            # Warren diagonals: in each panel, a band from low to high
            # panel index along this span
            u = d / max(span, 1e-3)
            panel = min(WAIBAIDU_NODES - 2, int(u * (WAIBAIDU_NODES - 1)))
            p0 = panel / (WAIBAIDU_NODES - 1)
            p1 = (panel + 1) / (WAIBAIDU_NODES - 1)
            f = 0 if p1 == p0 else (u - p0) / (p1 - p0)
            f = min(max(f, 0.0), 1.0)
            h0 = camelback_h(p0 * WAIBAIDU_SPAN)
            h1 = camelback_h(p1 * WAIBAIDU_SPAN)
            if panel % 2 == 0:
                z_diag = z_deck + 1 + int(round(((1 - f) * 0 + f * h1)))
            else:
                z_diag = z_deck + 1 + int(round(((1 - f) * h0 + f * 0)))
            z_diag = min(max(z_diag, z_deck + 1), z_top)
            put(z_diag, rr, cc, steel_id)
            put(z_diag + 1, rr, cc, steel_id)
            put(z_diag - 1, rr, cc, steel_id)

    # portal beams: join the two trusses at mid-span tops and at the pier
    for s_pt, d_frac in ((mid_s * 0.5, 0.5), (mid_s, 0.0), (mid_s + (L - mid_s) * 0.5, 0.5)):
        s0, span, d = span_of(s_pt)
        h = camelback_h((d_frac if d_frac else 0.0) * WAIBAIDU_SPAN) if d_frac else camelback_h(0.0)
        # at pier (d_frac 0) the truss is at end height
        if abs(s_pt - mid_s) < 1:
            h = camelback_h(0.0)
        z_top = z_deck + max(2, int(round(h)))
        for k in range(int(WAIBAIDU_TRUSS_GAP) + 2):
            t = -truss_t + k
            x = x0 + s_pt * ux + t * vx
            y = y0 + s_pt * uy + t * vy
            rr, cc = int(y - r0), int(x - c0)
            put(z_top, rr, cc, steel_id)
            put(z_top - 1, rr, cc, steel_id)
    return n_set


JINGAN_MAHAVIRA_WAY = 1246090668
JINGAN_MAHAVIRA_H = 26.0  # m, published hall height
JINGAN_FA_TANG_WAY = 1246090687
JINGAN_FA_TANG_H = 32.0   # m, secondary source (hall / 法堂)
JINGAN_SOURCES = [
    "https://zh.wikipedia.org/wiki/%E9%9D%99%E5%AE%89%E5%AF%BA",
    "https://m.thepaper.cn/newsDetail_forward_3252617",
    "https://www.fjdh.cn/bnznews/2009/01/10360234000.html",
]


def _paint_hip_hall(vol, gwin, origin, geom, height_m, wall_id, roof_id, ridge_id, eave_overhang=3):
    """Schematic 庑殿 (hip) hall, optionally with a second eave, on an OSM footprint.

    height_m is the published building height above ground. Plan comes from OSM.
    Not a scan and not invented plan dimensions.
    """
    from rasterio.features import rasterize
    from affine import Affine
    from shapely.geometry import box as _box

    ox, oy = origin  # grid (x,y) of vol[:,0,0]
    H, rows, cols = vol.shape
    if geom is None or geom.is_empty:
        return 0
    dilated = geom.buffer(eave_overhang)
    tr = Affine.translation(ox, oy)
    foot = rasterize([(geom, 1)], out_shape=(rows, cols), transform=tr, fill=0, dtype=np.uint8)
    eave = rasterize([(dilated, 1)], out_shape=(rows, cols), transform=tr, fill=0, all_touched=True, dtype=np.uint8)
    if not foot.any():
        return 0
    rs, cs = np.nonzero(eave)
    # long axis of the footprint for the main ridge
    fr, fc = np.nonzero(foot)
    fx = fc + ox  # grid x
    fy = fr + oy
    span_x, span_y = fx.max() - fx.min() + 1, fy.max() - fy.min() + 1
    ridge_along_y = span_y >= span_x  # ridge parallel to +y
    cx, cy = float(fx.mean()), float(fy.mean())
    half = (span_y if ridge_along_y else span_x) / 2.0
    n = 0
    for r, c in zip(rs, cs):
        gz = int(gwin[r, c]) if 0 <= r < gwin.shape[0] and 0 <= c < gwin.shape[1] else 8
        x, y = c + ox + 0.5, r + oy + 0.5
        inside = foot[r, c] == 1
        # distance from ridge line (0 at ridge, 1 at the long-side eave)
        if ridge_along_y:
            t = abs(x - cx) / max(1.0, span_x / 2.0)
        else:
            t = abs(y - cy) / max(1.0, span_y / 2.0)
        t = min(1.0, t)
        z_ridge = gz + int(round(height_m))
        z_eave_hi = gz + int(round(height_m * 0.88))
        z_upper_wall = gz + int(round(height_m * 0.72))
        z_eave_lo = gz + int(round(height_m * 0.58))
        z_lower_wall = gz + int(round(height_m * 0.48))
        if inside:
            # teak body, leave a doorway gap on the +y (courtyard) side of the lower storey
            vol[gz:z_lower_wall, r, c] = wall_id
            # mid belt (lower eave sits on this)
            if z_eave_lo > z_lower_wall:
                vol[z_lower_wall:min(z_eave_lo + 1, H), r, c] = roof_id
            if z_upper_wall > z_eave_lo + 1:
                vol[z_eave_lo + 1:z_upper_wall, r, c] = wall_id
            # hip roof up to the ridge, higher near the ridge
            z_roof0 = z_upper_wall
            z_roof1 = int(round(z_eave_hi + (z_ridge - z_eave_hi) * (1.0 - t)))
            z_roof1 = min(H, max(z_roof0 + 1, z_roof1))
            vol[z_roof0:z_roof1, r, c] = roof_id
            if t < 0.12 and z_ridge <= H:
                vol[min(H - 1, z_ridge - 1):min(H, z_ridge + 1), r, c] = ridge_id
            n += 1
        else:
            # lower + upper eave collar, sloping down outward
            # distance outside the wall, 0 at wall, 1 at overhang edge
            # approximate with how far from foot
            ring = 1
            # lower eave
            z0 = z_eave_lo - 1
            z1 = z_eave_lo + 1
            if 0 <= z0 < H:
                vol[max(0, z0):min(H, z1), r, c] = roof_id
                n += 1
            # upper eave, slightly tighter (skip if far from foot)
            # use chebyshev distance to footprint
            n += 0
    # upper eave: 1–2 m collar
    upper = rasterize([(geom.buffer(2), 1)], out_shape=(rows, cols), transform=tr, fill=0, all_touched=True, dtype=np.uint8)
    rs, cs = np.nonzero((upper == 1) & (foot == 0))
    for r, c in zip(rs, cs):
        gz = int(gwin[r, c]) if 0 <= r < gwin.shape[0] and 0 <= c < gwin.shape[1] else 8
        z0 = gz + int(round(height_m * 0.72))
        z1 = gz + int(round(height_m * 0.88))
        vol[max(0, z0):min(H, z1), r, c] = roof_id
        n += 1
    return n
