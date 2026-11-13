"""Road + pedestrian graphs for the live simulation (web/src/sim/life.ts).

Matches Warsaw's TrafficData: roads (directed, split at junctions), peds (undirected
footpaths + implicit sidewalks), ctrls (signal clusters), optional heads / restr.

Coordinates are grid metres (x = col, y = row) with z = walking/driving surface voxel
level, taken from the building-masked DEM (plus a tagged-bridge deck offset). There is
no full city volume on disk, so paths are not fitted to voxels the way Warsaw's
traffic.py does with final_vol.npy.
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from pyproj import Transformer

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from aoi import CRS, D, RAW, W, reload, to_grid  # noqa: E402

reload()

OUT = ROOT / "web" / "public" / "data" / "traffic.json"
META = ROOT / "web" / "public" / "data" / "meta.json"
DEM = ROOT / "data" / "dem_grid.npz"
DEM_FIXED = ROOT / "data" / "ground_fixed.npz"
GROUND_ALL = ROOT / "data" / "ground_all.npz"
_T = Transformer.from_crs(4326, CRS, always_xy=True)

DRIVE = {"motorway": 22, "trunk": 16, "primary": 14, "secondary": 13.9, "tertiary": 12.5, "unclassified": 11,
         "residential": 9, "motorway_link": 14, "trunk_link": 12, "primary_link": 11, "secondary_link": 11,
         "tertiary_link": 10}
CLASS = {"primary": 3, "trunk": 3, "secondary": 3, "tertiary": 2, "unclassified": 1, "residential": 1}
WALK = {"footway", "pedestrian", "path", "steps", "living_street", "platform", "cycleway"}
WEIGHT = {"pedestrian": 6.0, "living_street": 5.0, "footway": 1.0, "path": 0.6, "steps": 0.5,
          "platform": 0.8, "cycleway": 0.15}


def _num(v, default=None):
    try:
        return float(str(v).split(";")[0].split()[0].replace(",", "."))
    except (ValueError, IndexError, AttributeError, TypeError):
        return default


def underground(t):
    return (t.get("tunnel") in ("yes", "building_passage") or _num(t.get("layer"), 0) < 0
            or t.get("indoor") == "yes" or t.get("location") == "underground"
            or (_num(t.get("level"), 0) or 0) < 0 or t.get("covered") == "yes")


def note(*a):
    print("traffic:", *a, flush=True)


def load_ground():
    meta = json.loads(META.read_text())
    base = int(meta.get("base", -9))
    if GROUND_ALL.exists():
        z = np.load(GROUND_ALL)
        if "base" in z.files:
            base = int(z["base"])
        ground = z["ground"]
        meta = {**meta, "_gx0": int(z["x0"]) if "x0" in z.files else int(meta.get("x0", 0)),
                "_gy0": int(z["y0"]) if "y0" in z.files else int(meta.get("y0", 0))}
        g = np.clip(np.round(ground - base), 1, 250).astype(np.int32)
        return g, meta
    src = DEM_FIXED if DEM_FIXED.exists() else DEM
    ground = np.load(src)["ground"]
    g = np.clip(np.round(ground - base), 1, 250).astype(np.int32)
    return g, meta


def build(g, meta):
    osm_file = Path(RAW) / "osm_expanded.json"
    if not osm_file.exists():
        osm_file = Path(RAW) / "osm.json"
    data = json.load(open(osm_file))
    bridge_nodes = {n for e in data["elements"]
                    if e["type"] == "way" and e.get("tags", {}).get("bridge") not in (None, "no")
                    and ("highway" in e["tags"] or "railway" in e["tags"])
                    for n in e.get("nodes", [])}
    nodes = {}
    ntags = {}
    ways = []
    for e in data["elements"]:
        if e["type"] == "node" and e.get("tags"):
            ntags[e["id"]] = e["tags"]
        if e["type"] != "way" or "highway" not in e.get("tags", {}):
            continue
        geo = e.get("geometry") or []
        if len(geo) != len(e.get("nodes") or []):
            continue
        for nid, p in zip(e["nodes"], geo):
            if p and nid not in nodes:
                x, y = _T.transform(p["lon"], p["lat"])
                i, j = to_grid(x, y)
                nodes[nid] = (float(i), float(j))
        if all(n in nodes for n in e["nodes"]):
            ways.append(e)

    x0 = int(meta.get("_gx0", meta.get("x0", 0)))
    y0 = int(meta.get("_gy0", meta.get("y0", 0)))
    xmax = int(meta.get("W", W))
    ymax = int(meta.get("D", D))

    def inside(nid, m=3):
        x, y = nodes[nid]
        return x0 + m <= x < xmax - m and y0 + m <= y < ymax - m

    def z_at(x, y, bridge, layer=0):
        # g may be stitched ground_all with origin (x0,y0)
        if g.shape[0] == ymax - y0 and y0 != 0:
            c = min(max(int(x) - x0, 0), g.shape[1] - 1)
            r = min(max(int(y) - y0, 0), g.shape[0] - 1)
        else:
            c = min(max(int(x), 0), g.shape[1] - 1)
            r = min(max(int(y), 0), g.shape[0] - 1)
        z = int(g[r, c])
        if bridge:
            deck = 6 + max(0, int(layer or 0)) * 4
            z += deck
        return z

    def split(ws):
        use = defaultdict(int)
        for w in ws:
            for n in w["nodes"]:
                use[n] += 1
        out = []
        for w in ws:
            ns = w["nodes"]
            cur = [ns[0]]
            for n in ns[1:]:
                cur.append(n)
                if use[n] > 1 or n == ns[-1]:
                    out.append((w, cur))
                    cur = [n]
        res = []
        for w, seg in out:
            run = []
            for n in seg:
                if inside(n):
                    run.append(n)
                else:
                    if len(run) >= 2:
                        res.append((w, run))
                    run = []
            if len(run) >= 2:
                res.append((w, run))
        return res

    def pts_of(seq, bridge, layer=0):
        out = []
        for a, b in zip(seq, seq[1:] + [None]):
            xa, ya = nodes[a]
            br = bridge or a in bridge_nodes
            out.append([round(xa, 1), round(ya, 1), z_at(xa, ya, br, layer)])
            if b is None:
                continue
            xb, yb = nodes[b]
            L = math.hypot(xb - xa, yb - ya)
            on = br and (bridge or b in bridge_nodes)
            for k in range(1, int(L // 3)):
                f = k * 3 / L
                x, y = xa + (xb - xa) * f, ya + (yb - ya) * f
                out.append([round(x, 1), round(y, 1), z_at(x, y, on, layer)])
        for i in range(1, len(out) - 1):
            za, zb, zc = out[i - 1][2], out[i][2], out[i + 1][2]
            if (zb - za) * (zb - zc) > 0 and abs(zb - (za + zc) / 2) >= 1.5:
                out[i][2] = int(round((za + zc) / 2))
        return out

    def length(p):
        return float(sum(math.dist(a[:2], b[:2]) for a, b in zip(p, p[1:])))

    # ---------------- roads
    rways = [w for w in ways if w["tags"]["highway"] in DRIVE and not underground(w["tags"])
             and w["tags"].get("access") not in ("no", "private") and w["tags"].get("area") != "yes"]
    sig_nodes = {n for n, t in ntags.items() if t.get("highway") == "traffic_signals" and n in nodes and inside(n)}

    # cluster signal nodes within 45 m into controllers
    ctrl_of, ctrls = {}, []
    sig_list = list(sig_nodes)
    used = set()
    for n in sig_list:
        if n in used:
            continue
        x, y = nodes[n]
        cluster = [m for m in sig_list if m not in used and math.dist((x, y), nodes[m]) <= 45]
        if not cluster:
            continue
        sx = sum(nodes[m][0] for m in cluster) / len(cluster)
        sy = sum(nodes[m][1] for m in cluster) / len(cluster)
        for m in cluster:
            ctrl_of[m] = len(ctrls)
            used.add(m)
        ctrls.append(dict(x=round(sx, 1), y=round(sy, 1), n=len(cluster)))
    note(f"{len(ctrls)} signal controllers from {len(sig_nodes)} nodes")

    nid_index = {}

    def node_idx(n):
        if n not in nid_index:
            nid_index[n] = len(nid_index)
        return nid_index[n]

    roads = []
    for w, seg in split(rways):
        t = w["tags"]
        hw = t["highway"]
        ow = t.get("oneway") in ("yes", "true", "1") or t.get("junction") in ("roundabout", "circular") \
            or hw in ("motorway",)
        rev = t.get("oneway") == "-1"
        lanes = _num(t.get("lanes"))
        lf, lb = _num(t.get("lanes:forward")), _num(t.get("lanes:backward"))
        if ow or rev:
            lf = lanes or 1
        else:
            lf = lf or max(1, (lanes or 2) // 2)
            lb = lb or max(1, (lanes or 2) - lf)
        v = (_num(t.get("maxspeed")) or 50) / 3.6
        v = min(v, DRIVE[hw] + 2)
        bridge = t.get("bridge") not in (None, "no")
        layer = _num(t.get("layer"), 0) or 0
        dirs = [(seg, int(lf), "forward")] if not rev else [(seg[::-1], int(lf), "backward")]
        if not ow and not rev:
            dirs.append((seg[::-1], int(lb), "backward"))
        for s, nl, dname in dirs:
            p = pts_of(s, bridge, layer)
            L = length(p)
            if L < 1:
                continue
            e = dict(a=node_idx(s[0]), b=node_idx(s[-1]), w=w["id"], p=p, L=round(L, 1),
                     lanes=max(1, min(int(nl), 4)), v=round(v, 1),
                     c=CLASS.get(hw.replace("_link", ""), 1), two=not (ow or rev))
            cum = np.concatenate([[0], np.cumsum([math.dist(a[:2], b[:2]) for a, b in zip(p, p[1:])])])
            best = None
            for k, n in enumerate(s[1:], 1):
                if n not in sig_nodes or L - cum[min(k, len(cum) - 1)] > 45:
                    continue
                sd = ntags[n].get("traffic_signals:direction")
                if sd in ("forward", "backward") and sd != dname:
                    continue
                stop = cum[min(k, len(cum) - 1)] - (5.5 if k == len(s) - 1 else 1.0)
                best = (max(0.5, stop), ctrl_of[n], k)
            if best:
                k = best[2]
                dx, dy = p[min(k, len(p) - 1)][0] - p[max(k - 1, 0)][0], p[min(k, len(p) - 1)][1] - p[max(k - 1, 0)][1]
                e["sig"] = dict(s=round(best[0], 1), ctrl=best[1], axis=0 if abs(dx) >= abs(dy) else 1)
            roads.append(e)

    for e in roads:
        e["o0"] = 1.5 if e["two"] else round((0.5 - e["lanes"] / 2) * 3.0, 2)

    n_inf = 0
    for e in roads:
        if "sig" in e or not ctrls or len(e["p"]) < 2:
            continue
        x, y = e["p"][-1][:2]
        k = int(np.argmin([math.dist((x, y), (c["x"], c["y"])) for c in ctrls]))
        if math.dist((x, y), (ctrls[k]["x"], ctrls[k]["y"])) > 25 or e["L"] < 8:
            continue
        dx, dy = e["p"][-1][0] - e["p"][-2][0], e["p"][-1][1] - e["p"][-2][1]
        e["sig"] = dict(s=round(max(0.5, e["L"] - 5.5), 1), ctrl=k, axis=0 if abs(dx) >= abs(dy) else 1)
        n_inf += 1
    note(f"{n_inf} side-street approaches inferred onto nearby signals")

    restr = []
    for rel in data["elements"]:
        t = rel.get("tags", {})
        if rel["type"] != "relation" or t.get("type") != "restriction" or not t.get("restriction"):
            continue
        fr = [m["ref"] for m in rel.get("members", []) if m.get("role") == "from" and m["type"] == "way"]
        to = [m["ref"] for m in rel.get("members", []) if m.get("role") == "to" and m["type"] == "way"]
        via = [m["ref"] for m in rel.get("members", []) if m.get("role") == "via" and m["type"] == "node"]
        if fr and to and via and via[0] in nid_index:
            restr.append([fr[0], nid_index[via[0]], to[0], t["restriction"]])
    note(f"{len(restr)} turn restrictions")

    # ---------------- pedestrians
    pways = [w for w in ways if (w["tags"]["highway"] in WALK or w["tags"].get("footway") == "sidewalk")
             and not underground(w["tags"]) and w["tags"].get("access") not in ("no", "private")]
    ped_sig = {n for n, t in ntags.items()
               if (t.get("crossing") == "traffic_signals" or t.get("crossing:signals") == "yes") and n in nodes}
    peds, pidx = [], {}

    def pnode(n):
        if n not in pidx:
            pidx[n] = len(pidx)
        return pidx[n]

    for w, seg in split(pways):
        t = w["tags"]
        bridge = t.get("bridge") not in (None, "no")
        layer = _num(t.get("layer"), 0) or 0
        p = pts_of(seg, bridge, layer)
        L = length(p)
        if L < 1:
            continue
        wt = WEIGHT.get(t["highway"], 1.0) * (1.5 if t.get("footway") == "sidewalk" else 1.0)
        e = dict(a=pnode(seg[0]), b=pnode(seg[-1]), w=wt, p=p, L=round(L, 1))
        if t.get("highway") == "cycleway" or t.get("bicycle") == "designated":
            e["bk"] = 1.0
        is_cross = t.get("footway") == "crossing" or t.get("highway") == "crossing"
        if is_cross:
            e["x"] = True  # filled below
            mx, my = p[len(p) // 2][:2]
            if ctrls:
                k = int(np.argmin([math.dist((mx, my), (c["x"], c["y"])) for c in ctrls]))
                if math.dist((mx, my), (ctrls[k]["x"], ctrls[k]["y"])) < 70:
                    dx, dy = p[-1][0] - p[0][0], p[-1][1] - p[0][1]
                    e["x"] = dict(ctrl=k, axis=0 if abs(dx) >= abs(dy) else 1)
                else:
                    e.pop("x", None)
            else:
                e.pop("x", None)
            # also treat a crossing with its own signal node
            if "x" not in e and any(n in ped_sig or n in sig_nodes for n in seg) and ctrls:
                mx, my = p[len(p) // 2][:2]
                k = int(np.argmin([math.dist((mx, my), (c["x"], c["y"])) for c in ctrls]))
                dx, dy = p[-1][0] - p[0][0], p[-1][1] - p[0][1]
                e["x"] = dict(ctrl=k, axis=0 if abs(dx) >= abs(dy) else 1)
        peds.append(e)

    # implicit sidewalks along drive roads that have no mapped footway nearby
    # sample midpoints of existing ped edges into a coarse hash
    def cell(x, y):
        return int(x) // 8, int(y) // 8

    occupied = defaultdict(list)
    for e in peds:
        mid = e["p"][len(e["p"]) // 2]
        occupied[cell(mid[0], mid[1])].append((mid[0], mid[1]))

    def near_foot(x, y, r=8.0):
        i, j = cell(x, y)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for px, py in occupied.get((i + di, j + dj), ()):
                    if (px - x) ** 2 + (py - y) ** 2 <= r * r:
                        return True
        return False

    n_imp = 0
    next_id = -1
    for w, seg in split(rways):
        t = w["tags"]
        hw = t["highway"]
        if hw in ("motorway", "motorway_link"):
            continue
        if t.get("sidewalk") in ("no", "none"):
            continue
        bridge = t.get("bridge") not in (None, "no")
        layer = _num(t.get("layer"), 0) or 0
        p = pts_of(seg, bridge, layer)
        if length(p) < 12:
            continue
        mid = p[len(p) // 2]
        if near_foot(mid[0], mid[1], 8.0):
            continue
        # offset to the right of the centreline (~ lane + kerb)
        lanes = _num(t.get("lanes")) or (1 if t.get("oneway") in ("yes", "true", "1") else 2)
        off = 2.0 + min(lanes, 4) * 1.5
        # build a right-hand offset polyline
        xy = np.array([q[:2] for q in p], float)
        if len(xy) < 2:
            continue
        d = np.diff(xy, axis=0)
        d = np.vstack([d[0], d])
        nrm = np.hypot(d[:, 0], d[:, 1])
        nrm = np.maximum(nrm, 1e-6)
        right = np.stack([d[:, 1] / nrm, -d[:, 0] / nrm], 1)  # right-hand
        sides = [1.0]
        if t.get("sidewalk") in (None, "both", "yes") and t.get("oneway") not in ("yes", "true", "1"):
            sides = [1.0, -1.0]
        elif t.get("sidewalk") == "left":
            sides = [-1.0]
        for sgn in sides:
            op = []
            for q, nvec in zip(p, right):
                x, y = q[0] + sgn * off * nvec[0], q[1] + sgn * off * nvec[1]
                if not (x0 + 3 <= x < xmax - 3 and y0 + 3 <= y < ymax - 3):
                    op = []
                    break
                op.append([round(x, 1), round(y, 1), int(q[2])])
            if len(op) < 2:
                continue
            L = length(op)
            if L < 12:
                continue
            a, b = next_id, next_id - 1
            next_id -= 2
            peds.append(dict(a=pnode(a), b=pnode(b), w=0.8, p=op, L=round(L, 1), side=int(sgn)))
            occupied[cell(op[len(op) // 2][0], op[len(op) // 2][1])].append((op[len(op) // 2][0], op[len(op) // 2][1]))
            n_imp += 1
    note(f"{n_imp} implicit sidewalks added along roads without mapped footways")

    # signal heads (optional): right-hand kerb at the stop line
    heads = []
    for e in roads:
        sg = e.get("sig")
        if not sg or len(e["p"]) < 2:
            continue
        p = np.array(e["p"], float)
        cum = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(p[:, :2], axis=0).T))])
        s = min(sg["s"], cum[-1])
        k = int(np.searchsorted(cum, s))
        k = min(max(k, 1), len(p) - 1)
        f = 0 if cum[k] == cum[k - 1] else (s - cum[k - 1]) / (cum[k] - cum[k - 1])
        x = p[k - 1, 0] + (p[k, 0] - p[k - 1, 0]) * f
        y = p[k - 1, 1] + (p[k, 1] - p[k - 1, 1]) * f
        dx, dy = p[k, 0] - p[k - 1, 0], p[k, 1] - p[k - 1, 1]
        Lxy = math.hypot(dx, dy) or 1.0
        fx, fy = dx / Lxy, dy / Lxy
        off = (e["lanes"] + (0 if e["two"] else -e["lanes"] / 2)) * 3.1 + 1.2
        hx, hy = x + fy * off, y - fx * off
        heads.append([round(hx, 1), round(hy, 1), z_at(hx, hy, True),
                      round(math.atan2(dx, dy), 2), sg["ctrl"], sg["axis"]])

    ncross = sum(1 for e in peds if "x" in e)
    note(f"{len(roads)} directed road edges ({round(sum(e['L'] * e['lanes'] for e in roads) / 1000, 1)} lane-km), "
         f"{len(ctrls)} controllers; {len(peds)} footpath edges "
         f"({round(sum(e['L'] for e in peds) / 1000, 1)} km), {ncross} signalled crossings")

    # StreetLife only needs roads, peds, ctrls; extras match the Warsaw file
    return dict(roads=roads, peds=peds, ctrls=[{"x": c["x"], "y": c["y"]} for c in ctrls],
                heads=heads, trams=[], trains=[], restr=restr)


def main():
    g, meta = load_ground()
    data = build(g, meta)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    note("wrote", OUT, "bytes", OUT.stat().st_size)


if __name__ == "__main__":
    main()
