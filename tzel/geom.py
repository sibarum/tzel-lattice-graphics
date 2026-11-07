"""Polyline geometry. Every drawn thing is sampled to polylines."""
import cmath
import math


def add(a, b):
    return (a[0] + b[0], a[1] + b[1])


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def mul(a, k):
    return (a[0] * k, a[1] * k)


def unit_at(deg):
    r = math.radians(deg)
    return (math.cos(r), math.sin(r))


def heading_of(v):
    return math.degrees(math.atan2(v[1], v[0]))


def norm_deg(d):
    d = (d + 180.0) % 360.0 - 180.0
    return d


def length(pts):
    return sum(math.dist(pts[k], pts[k + 1]) for k in range(len(pts) - 1))


def point_at(pts, t):
    """Point at fraction t of arclength."""
    total = length(pts)
    if total == 0:
        return pts[0]
    target = max(0.0, min(1.0, t)) * total
    acc = 0.0
    for k in range(len(pts) - 1):
        d = math.dist(pts[k], pts[k + 1])
        if acc + d >= target and d > 0:
            u = (target - acc) / d
            return (pts[k][0] + (pts[k + 1][0] - pts[k][0]) * u,
                    pts[k][1] + (pts[k + 1][1] - pts[k][1]) * u)
        acc += d
    return pts[-1]


def end_heading(pts):
    for k in range(len(pts) - 1, 0, -1):
        v = sub(pts[k], pts[k - 1])
        if math.hypot(*v) > 1e-9:
            return heading_of(v)
    return 0.0


def bbox(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def bbox_union(boxes):
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def area(pts):
    s = 0.0
    for k in range(len(pts)):
        x1, y1 = pts[k]
        x2, y2 = pts[(k + 1) % len(pts)]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def centroid(pts):
    xs = sum(p[0] for p in pts) / len(pts)
    ys = sum(p[1] for p in pts) / len(pts)
    return (xs, ys)


def hull(pts):
    P = sorted(set((round(p[0], 9), round(p[1], 9)) for p in pts))
    if len(P) < 3:
        return P

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, hi = [], []
    for p in P:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(P):
        while len(hi) >= 2 and cross(hi[-2], hi[-1], p) <= 0:
            hi.pop()
        hi.append(p)
    return lo[:-1] + hi[:-1]


def seg_intersect(p1, p2, p3, p4):
    d = (p2[0] - p1[0]) * (p4[1] - p3[1]) - (p2[1] - p1[1]) * (p4[0] - p3[0])
    if abs(d) < 1e-12:
        return None
    t = ((p3[0] - p1[0]) * (p4[1] - p3[1]) - (p3[1] - p1[1]) * (p4[0] - p3[0])) / d
    u = ((p3[0] - p1[0]) * (p2[1] - p1[1]) - (p3[1] - p1[1]) * (p2[0] - p1[0])) / d
    if 0 <= t <= 1 and 0 <= u <= 1:
        return (p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1]))
    return None


def intersections(a, b, merge=0.05):
    """Crossing points between polylines a and b (merged when close)."""
    ba, bb = bbox(a), bbox(b)
    if ba[2] < bb[0] or bb[2] < ba[0] or ba[3] < bb[1] or bb[3] < ba[1]:
        return []
    out = []
    for i in range(len(a) - 1):
        a1, a2 = a[i], a[i + 1]
        if max(a1[0], a2[0]) < bb[0] or min(a1[0], a2[0]) > bb[2]:
            continue
        for j in range(len(b) - 1):
            p = seg_intersect(a1, a2, b[j], b[j + 1])
            if p and all(math.dist(p, q) > merge for q in out):
                out.append(p)
    return out


def min_distance(a, b):
    """Approximate minimum distance between two polylines (vertex based)."""
    best = float("inf")
    for p in a:
        for q in b:
            d = (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2
            if d < best:
                best = d
    return math.sqrt(best)


def point_in_poly(p, poly):
    x, y = p
    inside = False
    n = len(poly)
    for k in range(n):
        x1, y1 = poly[k]
        x2, y2 = poly[(k + 1) % n]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if xi > x:
                inside = not inside
    return inside


# ---- curve samplers -------------------------------------------------------

def sample_arc(p, e, sweep_deg, n=None):
    """Arc from p to e turning sweep_deg (CCW positive)."""
    th = math.radians(sweep_deg)
    if abs(th) < 1e-9:
        return [p, e]
    c = sub(e, p)
    L = math.hypot(*c)
    if L < 1e-12:
        raise ValueError("arc endpoints coincide")
    m = mul(add(p, e), 0.5)
    left = (-c[1] / L, c[0] / L)
    d = (L / 2.0) / math.tan(th / 2.0)
    ctr = add(m, mul(left, d))
    a0 = math.atan2(p[1] - ctr[1], p[0] - ctr[0])
    r = math.dist(p, ctr)
    n = n or max(8, int(abs(sweep_deg) / 3))
    pts = [(ctr[0] + r * math.cos(a0 + th * k / n),
            ctr[1] + r * math.sin(a0 + th * k / n)) for k in range(n + 1)]
    pts[-1] = e
    return pts


def arc_by_heading(p, heading, sweep_deg, s):
    """Arc starting at p tangent to heading, turning sweep_deg, arclength s."""
    th = math.radians(sweep_deg)
    if abs(th) < 1e-9:
        return [p, add(p, mul(unit_at(heading), s))]
    r = s / abs(th)
    sgn = 1 if th > 0 else -1
    h = math.radians(heading)
    ctr = (p[0] - sgn * r * math.sin(h), p[1] + sgn * r * math.cos(h))
    a0 = math.atan2(p[1] - ctr[1], p[0] - ctr[0])
    n = max(8, int(abs(sweep_deg) / 3))
    return [(ctr[0] + r * math.cos(a0 + th * k / n),
             ctr[1] + r * math.sin(a0 + th * k / n)) for k in range(n + 1)]


def solve_sweep_for_length(chord, s):
    """Sweep (deg, >0) for an arc with this chord whose arclength is s."""
    if s <= chord * (1 + 1e-9):
        return 0.0
    lo, hi = 1e-6, 2 * math.pi - 1e-6
    f = lambda th: (chord * (th / 2) / math.sin(th / 2)) - s
    for _ in range(100):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            hi = mid
        else:
            lo = mid
    return math.degrees((lo + hi) / 2)


def sample_quad(p, c, e, n=32):
    out = []
    for k in range(n + 1):
        t = k / n
        a = (1 - t) ** 2
        b = 2 * (1 - t) * t
        d = t * t
        out.append((a * p[0] + b * c[0] + d * e[0], a * p[1] + b * c[1] + d * e[1]))
    return out


def sample_geo(p, e, pole, wind=0, n=None):
    a = complex(*sub(p, pole))
    b = complex(*sub(e, pole))
    if abs(a) < 1e-9 or abs(b) < 1e-9:
        raise ValueError("geo endpoints must not sit on the pole")
    la = cmath.log(a)
    lb = cmath.log(b)
    # choose the shortest angular branch, then add winding
    dphi = (lb.imag - la.imag + math.pi) % (2 * math.pi) - math.pi
    lb = complex(lb.real, la.imag + dphi + 2 * math.pi * wind)
    turns = abs(lb.imag - la.imag) / (2 * math.pi)
    n = n or max(16, int(64 * max(turns, 0.25)))
    out = []
    for k in range(n + 1):
        t = k / n
        z = cmath.exp((1 - t) * la + t * lb)
        out.append((pole[0] + z.real, pole[1] + z.imag))
    out[-1] = e
    return out


def sample_circle(c, r, n=72):
    return [(c[0] + r * math.cos(2 * math.pi * k / n),
             c[1] + r * math.sin(2 * math.pi * k / n)) for k in range(n)] + \
           [(c[0] + r, c[1])]


# ---- affine ----------------------------------------------------------------

def affine_identity():
    return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)  # a b c d e f : x' = a x + c y + e


def affine_mul(m, n):
    """m after n."""
    a1, b1, c1, d1, e1, f1 = m
    a2, b2, c2, d2, e2, f2 = n
    return (a1 * a2 + c1 * b2, b1 * a2 + d1 * b2,
            a1 * c2 + c1 * d2, b1 * c2 + d1 * d2,
            a1 * e2 + c1 * f2 + e1, b1 * e2 + d1 * f2 + f1)


def affine_apply(m, p):
    a, b, c, d, e, f = m
    return (a * p[0] + c * p[1] + e, b * p[0] + d * p[1] + f)


def T(dx, dy):
    return (1.0, 0.0, 0.0, 1.0, dx, dy)


def R(deg):
    r = math.radians(deg)
    return (math.cos(r), math.sin(r), -math.sin(r), math.cos(r), 0.0, 0.0)


def S(sx, sy=None):
    sy = sx if sy is None else sy
    return (sx, 0.0, 0.0, sy, 0.0, 0.0)


def about(m, p):
    return affine_mul(T(p[0], p[1]), affine_mul(m, T(-p[0], -p[1])))


def mirror_line(p, q):
    """Reflection across the line through p and q."""
    ang = math.atan2(q[1] - p[1], q[0] - p[0])
    c, s = math.cos(2 * ang), math.sin(2 * ang)
    return about((c, s, s, -c, 0.0, 0.0), p)
