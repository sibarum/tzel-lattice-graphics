"""Queries: questions the author can ask mid-drawing. Answers are text."""
import math
import os

from . import geom as G
from . import perceive as P
from .lattice import LATTICES, TzelError, fmt
from .parser import parse_point, parse_statement

OUT_DIR = "out"


def answer(sess, st):
    q, a = st.q, [t for t in st.args if t != ","]
    fn = HANDLERS.get(q)
    if not fn:
        raise TzelError(f"unknown query ?{q}; queries are "
                         f"{', '.join('?' + k for k in HANDLERS)}")
    fn(sess, a, st)


def q_here(sess, a, st):
    p = sess.pen
    from .interp import compass
    near = []
    for n in sess.order:
        pts = sess.world(sess.shapes[n])
        d = min(math.dist(p, q) for q in P.resample(pts, 60))
        if d < 3:
            near.append((d, n))
    near.sort()
    s = f"{sess.lattice.describe(p)}, heading {compass(sess.heading)} ({fmt(sess.heading % 360, 0)}°)"
    if near:
        s += "; " + ", ".join(f"on `{n}`" if d < 0.05 else f"`{n}` {fmt(d, 1)} away"
                              for d, n in near[:4])
    sess.note(s)


def q_names(sess, a, st):
    if not sess.order:
        sess.note("nothing named yet")
        return
    s = ", ".join(f"`{n}`" for n in sess.order)
    if sess.groups:
        s += "; groups: " + ", ".join(f"`{g}` ({len(v.members)})" for g, v in sess.groups.items())
    sess.note(s)


def q_describe(sess, a, st):
    names = a if a else [sess.last_changed]
    if a and a[0] in ("all", "*"):
        names = sess.order
    for n in names:
        if n is None:
            sess.note("nothing drawn yet")
            continue
        if n in sess.groups:
            members = [sess.shapes[m] for m in sess.groups[n].members]
            bb = G.bbox_union([sess.world_bbox(s) for s in members])
            sess.note(f"`{n}`: group of {len(members)}, spans "
                      f"{fmt(bb[2] - bb[0], 1)}×{fmt(bb[3] - bb[1], 1)} cells")
            continue
        sess.note(f"`{n}`: " + P.describe(sess, sess.shape(n)))


def q_size(sess, a, st):
    for n in a:
        sh = sess.shape(n)
        pts = sess.world(sh)
        bb = G.bbox(pts)
        s = f"`{n}`: {fmt(bb[2] - bb[0])}×{fmt(bb[3] - bb[1])} cells"
        if sh.closed:
            s += f", area {fmt(abs(G.area(pts[:-1])), 1)}"
        else:
            s += f", length {fmt(G.length(pts))}"
        sess.note(s)


def q_gap(sess, a, st):
    As, Bs = sess.targets(a[0]), sess.targets(a[1])
    best = float("inf")
    for A in As:
        for B in Bs:
            pa, pb = sess.world(A), sess.world(B)
            if G.intersections(pa, pb):
                sess.note(f"`{a[0]}` and `{a[1]}` cross (gap 0, at `{A.name}`/`{B.name}`)")
                return
            best = min(best, G.min_distance(P.resample(pa, 150), P.resample(pb, 150)))
    sess.note(f"{fmt(best)} cells between `{a[0]}` and `{a[1]}`")


def q_crosses(sess, a, st):
    sh = sess.shape(a[0]) if a else sess.shapes[sess.last_changed]
    pts = sess.world(sh)
    hits = []
    for n in sess.order:
        if n == sh.name:
            continue
        xs = G.intersections(pts, sess.world(sess.shapes[n]))
        if xs:
            hits.append(f"`{n}` near " + ", ".join(sess.lattice.describe(q) for q in xs[:3]))
    sess.note(f"`{sh.name}` crosses " + "; ".join(hits) if hits else f"`{sh.name}` crosses nothing")


def q_closed(sess, a, st):
    sh = sess.shape(a[0])
    pts = sess.world(sh)
    gap = math.dist(pts[0], pts[-1])
    if sh.closed:
        sess.note(f"`{sh.name}` is closed")
    else:
        sess.note(f"`{sh.name}` is open; its ends are {fmt(gap)} apart")


def q_where(sess, a, st):
    for t in a:
        p = sess.point(parse_point(t))
        sess.note(f"{t} is at {sess.lattice.describe(p)}")


def q_on(sess, a, st):
    lname, n = a[0], a[1]
    if lname not in LATTICES:
        raise TzelError(f"unknown lattice '{lname}'")
    lat = LATTICES[lname](origin=sess.lattice.origin, scale=sess.lattice.scale,
                          turn=math.degrees(sess.lattice.turn))
    sh = sess.shape(n)
    m = sess.matrix(sh)
    pts = [G.affine_apply(m, p) for p in ([sh.local[0]] + sh.ends if sh.ends else
                                          sh.local[:-1] if sh.closed else sh.local)]
    if sh.kind in ("circle", "dot"):
        c = sess.anchor(n, "center", None)
        sess.note(f"`{n}` center at {lat.describe(c)}")
        return
    desc = []
    for p in pts[:16]:
        q = lat.nearest(p)
        tag = "" if math.dist(p, q) < 0.02 else "~"
        desc.append(tag + lat.describe(p))
    sess.note(f"`{n}` key points on {lname}: " + ", ".join(desc)
              + ("  (~ = between lattice points)" if any(d.startswith("~") for d in desc) else ""))


def q_balance(sess, a, st):
    sess.note(P.balance(sess))


def q_symmetry(sess, a, st):
    sh = sess.shape(a[0])
    pts = sess.world(sh)
    bb = G.bbox(pts)
    c = ((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2)
    ev, eh = P.mirror_error(pts, 90, c), P.mirror_error(pts, 0, c)
    sess.note(f"`{sh.name}` mirror error: vertical axis {fmt(ev * 100, 1)}%, "
              f"horizontal axis {fmt(eh * 100, 1)}% of its size")


def q_try(sess, a, st):
    text = st.text.split(None, 1)[1] if len(st.text.split(None, 1)) > 1 else ""
    trial = parse_statement(st.line, text)
    f = sess.fork()
    notes = f.add(trial)
    nm = f.last_changed
    msg = "(trial) " + ("; ".join(notes) if notes else "no issues")
    if nm and nm in f.shapes:
        msg += f"; `{nm}` would be: " + P.describe(f, f.shapes[nm])
    sess.note(msg)


def q_glance(sess, a, st):
    from .render import glance
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"glance-L{st.line}.png")
    labels, big = glance(sess, path, landmark=a[0] if a else None)
    sess.note(f"glance saved to {path} (4× copy: {big}); " + " | ".join(labels))


def q_render(sess, a, st):
    from .render import to_svg, svg_to_png
    os.makedirs(OUT_DIR, exist_ok=True)
    path = a[0] if a else os.path.join(OUT_DIR, f"render-L{st.line}.png")
    width = 512
    with open(path, "wb") as fh:
        fh.write(svg_to_png(to_svg(sess), width=width))
    sess.note(f"render saved to {path} ({width}px wide)")


def q_overview(sess, a, st):
    W, H = sess.canvas
    sess.note(f"canvas {fmt(W)}×{fmt(H)} on {sess.lattice.name}, "
              f"{len(sess.order)} shapes, {len(sess.groups)} groups; " + P.balance(sess))
    for n in sess.order:
        sh = sess.shapes[n]
        c = sess.anchor(n, "center", None)
        sess.note(f"  `{n}` ({sh.kind}) at {sess.lattice.describe(c)}, in {P.region(c, sess.canvas)}")


def q_modes(sess, a, st):
    sess.note(", ".join(f"{k} {v}" for k, v in sess.modes.items()))


def q_help(sess, a, st):
    sess.note("queries: " + " ".join("?" + k for k in HANDLERS))


HANDLERS = {
    "here": q_here, "names": q_names, "describe": q_describe, "size": q_size,
    "gap": q_gap, "crosses": q_crosses, "closed": q_closed, "where": q_where,
    "on": q_on, "balance": q_balance, "symmetry": q_symmetry, "try": q_try,
    "glance": q_glance, "render": q_render, "overview": q_overview,
    "modes": q_modes, "help": q_help,
}
