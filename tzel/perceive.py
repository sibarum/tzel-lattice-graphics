"""Computed perception: plain-language descriptions from exact geometry."""
import math

from . import geom as G
from .lattice import fmt


def _pca(pts):
    cx, cy = G.centroid(pts)
    sxx = sum((p[0] - cx) ** 2 for p in pts) / len(pts)
    syy = sum((p[1] - cy) ** 2 for p in pts) / len(pts)
    sxy = sum((p[0] - cx) * (p[1] - cy) for p in pts) / len(pts)
    tr, det = sxx + syy, sxx * syy - sxy * sxy
    disc = math.sqrt(max(tr * tr / 4 - det, 0))
    l1, l2 = tr / 2 + disc, tr / 2 - disc
    ang = 0.5 * math.degrees(math.atan2(2 * sxy, sxx - syy))
    return l1, l2, ang


def lattice_axis(lat, deg):
    """Name an axis with the lattice's own directions when one is close."""
    best = None
    for name, (i, j) in lat.directions.items():
        v = lat.vec(i, j)
        a = math.degrees(math.atan2(v[1], v[0])) % 180
        diff = abs((deg % 180) - a)
        diff = min(diff, 180 - diff)
        if diff < 8 and (best is None or diff < best[0]):
            opp = [n for n, (p, q) in lat.directions.items() if (p, q) == (-i, -j)]
            best = (diff, f"{name}–{opp[0]}" if opp else name)
    return best[1] if best else axis_name(deg)


def axis_name(deg):
    d = deg % 180
    names = [(0, "E–W"), (22.5, "ENE–WSW"), (45, "NE–SW"), (67.5, "NNE–SSW"),
             (90, "N–S"), (112.5, "NNW–SSE"), (135, "NW–SE"), (157.5, "WNW–ESE"),
             (180, "E–W")]
    return min(names, key=lambda n: abs(n[0] - d))[1]


def resample(pts, n=120):
    return [G.point_at(pts, k / (n - 1)) for k in range(n)]


def mirror_error(pts, axis_deg, c):
    """Mean distance from mirrored samples to the original, relative to size."""
    S = resample(pts, 90)
    m = G.mirror_line(c, G.add(c, G.unit_at(axis_deg)))
    M = [G.affine_apply(m, p) for p in S]
    bb = G.bbox(S)
    size = max(bb[2] - bb[0], bb[3] - bb[1], 1e-9)
    tot = 0.0
    for q in M:
        tot += min(math.dist(q, p) for p in S)
    return tot / len(M) / size


def turning(pts):
    """Signed turning (deg) along a polyline, plus count of direction flips."""
    S = resample(pts, 60)
    total, flips, last_sign, run = 0.0, 0, 0, 0.0
    for k in range(1, len(S) - 1):
        a = G.heading_of(G.sub(S[k], S[k - 1]))
        b = G.heading_of(G.sub(S[k + 1], S[k]))
        d = G.norm_deg(b - a)
        total += d
        run += d
        if abs(run) > 20:
            s = 1 if run > 0 else -1
            if last_sign and s != last_sign:
                flips += 1
            last_sign, run = s, 0.0
    return total, flips


def count_corners(ring, thresh=25):
    """Vertices where the outline turns sharply (sampled curves turn gently)."""
    pts = [p for k, p in enumerate(ring) if k == 0 or math.dist(p, ring[k - 1]) > 1e-9]
    n, c = len(pts), 0
    if n < 3:
        return 0
    for k in range(n):
        a = G.sub(pts[k], pts[k - 1])
        b = G.sub(pts[(k + 1) % n], pts[k])
        if abs(G.norm_deg(G.heading_of(b) - G.heading_of(a))) > thresh:
            c += 1
    return c


def region(p, canvas):
    w, h = canvas
    col = ["left", "center", "right"][min(2, max(0, int(3 * p[0] / w)))]
    row = ["lower", "middle", "upper"][min(2, max(0, int(3 * p[1] / h)))]
    if row == "middle" and col == "center":
        return "the center"
    if row == "middle":
        return f"the middle {col}"
    if col == "center":
        return f"the {row} middle"
    return f"the {row} {col}"


def describe(sess, sh):
    pts = sess.world(sh)
    bb = G.bbox(pts)
    w, h = bb[2] - bb[0], bb[3] - bb[1]
    c = ((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2)
    parts = []
    if sh.kind == "text":
        return (f"text \"{sh.text}\", about {fmt(w, 1)}×{fmt(h, 1)} cells, "
                f"centered at {sess.lattice.describe(c)}, in {region(c, sess.canvas)}")
    if sh.closed:
        ring = pts[:-1] if math.dist(pts[0], pts[-1]) < 1e-6 else pts
        A = abs(G.area(ring))
        hl = G.hull(ring)
        HA = abs(G.area(hl)) if len(hl) > 2 else 0
        conv = A / HA if HA > 0 else 1
        shape = "convex" if conv > 0.97 else (
            "slightly concave" if conv > 0.85 else "strongly concave")
        l1, l2, ang = _pca(resample(pts))
        ratio = math.sqrt(l1 / l2) if l2 > 1e-9 else float("inf")
        corners = count_corners(ring)
        if sh.kind == "circle" and ratio < 1.05:
            form = "circle"
        elif 3 <= corners <= 8:
            form = {3: "triangle", 4: "four-cornered", 5: "five-cornered",
                    6: "six-cornered"}.get(corners, f"{corners}-cornered")
            if ratio >= 1.15:
                form += f", elongated {fmt(ratio, 1)}:1 along {lattice_axis(sess.lattice, ang)}"
        elif ratio < 1.15:
            form = "roundish"
        else:
            form = f"elongated {fmt(ratio, 1)}:1 along {lattice_axis(sess.lattice, ang)}"
        if A < 0.05 * max(w * h, 1e-9):
            parts.append(f"collapsed: closed but nearly zero area, so its sides overlap; "
                         f"{fmt(w, 1)}×{fmt(h, 1)} cells")
            parts.append(f"in {region(c, sess.canvas)} of the canvas")
            return "; ".join(parts)
        parts.append(f"closed {shape} shape, {form}")
        parts.append(f"{fmt(w, 1)}×{fmt(h, 1)} cells, area {fmt(A, 1)}")
        top = max(ring, key=lambda p: p[1])
        if w > 0.5 and abs(top[0] - c[0]) > 0.15 * w:
            parts.append("peak " + ("left" if top[0] < c[0] else "right") + " of center")
        if ratio >= 1.15 and len(hl) > 2:
            # tip: farthest point along the long axis, on each end
            u = G.unit_at(ang)
            proj = [((p[0] - c[0]) * u[0] + (p[1] - c[1]) * u[1], p) for p in ring]
            hi = max(proj)[1]
            lo = min(proj)[1]
            parts.append(f"ends at {sess.lattice.describe(lo)} and {sess.lattice.describe(hi)}")
    else:
        L = G.length(pts)
        chord = math.dist(pts[0], pts[-1])
        tot, flips = turning(pts)
        if abs(tot) < 8 and flips == 0:
            bend = "straight"
        elif flips >= 2:
            bend = f"wavy ({flips + 1} bends)"
        elif flips == 1:
            bend = "S-curve"
        else:
            bend = f"curves {'left' if tot > 0 else 'right'} {fmt(abs(tot), 0)}°"
        parts.append(f"open path, {bend}, length {fmt(L, 1)}")
        parts.append(f"from {sess.lattice.describe(pts[0])} to {sess.lattice.describe(pts[-1])}")
        parts.append(f"spans {fmt(w, 1)}×{fmt(h, 1)} cells")
        if chord < 0.05 * L:
            parts.append("nearly closes on itself")
    if w > 0.3 and h > 0.3:
        ev = mirror_error(pts, 90, c)
        eh = mirror_error(pts, 0, c)
        if ev < 0.02 and eh < 0.02:
            parts.append("symmetric both ways")
        elif ev < 0.03:
            parts.append("near-mirror about a vertical axis")
        elif eh < 0.03:
            parts.append("near-mirror about a horizontal axis")
    parts.append(f"in {region(c, sess.canvas)} of the canvas")
    rel = relations(sess, sh, pts)
    if rel:
        parts.append(rel)
    return "; ".join(parts)


def relations(sess, sh, pts=None):
    pts = pts or sess.world(sh)
    out = []
    for n in sess.order:
        if n == sh.name:
            continue
        o = sess.shapes[n]
        op = sess.world(o)
        xs = G.intersections(pts, op)
        ends = [pts[0], pts[-1]] + [sess.anchor_world(sh, a) for a in sh.attached]
        real = [q for q in xs if all(math.dist(q, e) > 0.08 for e in ends)]
        if real:
            out.append(f"crosses `{n}`")
        elif xs:
            out.append(f"touches `{n}`")
        elif o.closed and len(op) > 2 and all(G.point_in_poly(p, op) for p in pts[::max(1, len(pts) // 12)]):
            out.append(f"inside `{n}`")
        elif sh.closed and len(pts) > 2 and all(G.point_in_poly(p, pts) for p in op[::max(1, len(op) // 12)]):
            out.append(f"contains `{n}`")
        else:
            d = G.min_distance(resample(pts, 60), resample(op, 60))
            if d < 0.1:
                out.append(f"touches `{n}`")
    return ", ".join(out)


def balance(sess):
    W, H = sess.canvas
    wsum, xs, ys = 0.0, 0.0, 0.0
    occ = [[0.0] * 3 for _ in range(3)]
    for n in sess.order:
        sh = sess.shapes[n]
        pts = sess.world(sh)
        if sh.closed:
            ring = pts[:-1]
            wgt = abs(G.area(ring)) + 0.1
        else:
            wgt = G.length(pts) * 0.3
        c = G.centroid(pts)
        wsum += wgt
        xs += c[0] * wgt
        ys += c[1] * wgt
        for p in resample(pts, 40):
            i = min(2, max(0, int(3 * p[0] / W)))
            j = min(2, max(0, int(3 * p[1] / H)))
            occ[j][i] += wgt / 40
    if wsum == 0:
        return "the canvas is empty"
    cx, cy = xs / wsum, ys / wsum
    dx, dy = (cx - W / 2) / W, (cy - H / 2) / H
    lean = []
    if abs(dx) > 0.06:
        lean.append("right" if dx > 0 else "left")
    if abs(dy) > 0.06:
        lean.append("up" if dy > 0 else "down")
    s = ("visual weight is centered" if not lean
         else f"visual weight leans {' and '.join(lean)}")
    s += f" (center of weight at {fmt(100 * cx / W, 0)}% across, {fmt(100 * cy / H, 0)}% up)"
    names = [["lower left", "lower middle", "lower right"],
             ["middle left", "center", "middle right"],
             ["upper left", "upper middle", "upper right"]]
    empty = [names[j][i] for j in range(3) for i in range(3) if occ[j][i] < 0.01]
    if empty:
        s += "; empty regions: " + ", ".join(empty)
    return s
