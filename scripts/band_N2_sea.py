"""Sea/estuary polygon from OSM natural=coastline for a grid box (OSM rule: land on the
left of the way direction, water on the right). The Yangtze estuary beyond the coastline
has no natural=water polygon in OSM, so without this it would voxelize as land.
Returns a shapely geometry (may be empty) in grid coords, flat water like the Huangpu."""
from shapely.geometry import box, LineString, Point
from shapely.ops import linemerge, split, unary_union


def sea_polygon(osm, x0, y0, x1, y1):
    B = box(x0, y0, x1, y1)
    coast = [l for oid, t, l in osm.lines if t.get("natural") == "coastline"]
    if not coast:
        return B.difference(B)
    merged = linemerge(unary_union(coast))
    lines = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    clipped = []
    for ln in lines:
        c = ln.intersection(B)
        if c.is_empty:
            continue
        clipped += list(c.geoms) if hasattr(c, "geoms") else [c]
    clipped = [c for c in clipped if isinstance(c, LineString) and c.length > 0]
    if not clipped:
        return B.difference(B)
    pieces = split(B, unary_union(clipped))
    water = []
    for poly in pieces.geoms:
        votes = 0
        for ln in clipped:
            cs = list(ln.coords)
            for (ax, ay), (bx, by) in zip(cs[:-1], cs[1:]):
                dx, dy = bx - ax, by - ay
                L = (dx * dx + dy * dy) ** 0.5
                if L < 1e-6:
                    continue
                mx, my = (ax + bx) / 2, (ay + by) / 2
                # right-hand normal in a y-down?  grid (i,j) handedness is checked against
                # world: we test both and use the one consistent with lon/lat orientation (set below)
                nx, ny = dy / L * SIDE, -dx / L * SIDE
                p = Point(mx + nx * 2.0, my + ny * 2.0)
                if poly.contains(p):
                    votes += 1
                elif poly.contains(Point(mx - nx * 2.0, my - ny * 2.0)):
                    votes -= 1
        if votes > 0:
            water.append(poly)
    return unary_union(water) if water else B.difference(B)


SIDE = -1.0  # grid (i,j) is mirrored vs world (det=-1): world-right == grid-left
