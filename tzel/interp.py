"""Tzel interpreter: a state machine of modes, shapes, groups and transforms.

Statements run in order. A revision rewrites an earlier statement's terms
and the whole drawing is replayed, so everything that depended on the
revised shape follows it.
"""
import copy
import math

from . import geom as G
from .lattice import LATTICES, TzelError, fmt
from .parser import parse, parse_point, parse_dir, number, tokenize

DEFAULT_MODES = {
    "snap": "off",          # off | lattice | half
    "pole": "origin",       # origin | <anchor>
    "fill-in": "smoothest", # smoothest | simplest
    "mismatch": "warn",     # warn | error | trust
    "feedback": "push",     # quiet | push | verbose
    "stroke": "#222222",
    "width": "0.08",
    "fill": "none",
    "show-lattice": "off",  # off | on
}
STYLE_ROLES = {"stroke", "fill", "width", "opacity", "dash", "layer"}
LAYER_NAMES = {"back": -1, "mid": 0, "front": 1}
COMPASS = ["E", "ENE", "NE", "NNE", "N", "NNW", "NW", "WNW",
           "W", "WSW", "SW", "SSW", "S", "SSE", "SE", "ESE"]


def compass(deg):
    return COMPASS[int(round((deg % 360) / 22.5)) % 16]


class Shape:
    def __init__(self, name, kind, stmt):
        self.name = name
        self.kind = kind
        self.stmt = stmt
        self.local = []        # polyline in local (as-drawn) coords
        self.closed = False
        self.segments = {}     # seg name -> (i0, i1) indices into local
        self.ends = []         # move endpoints (local)
        self.style = {}
        self.layer = 0
        self.text = None
        self.size = 1.0
        # own-anchor transforms (commute; restating replaces)
        self.t_scale = (1.0, 1.0)
        self.t_turn = 0.0
        self.t_move = (0.0, 0.0)
        self.t_mirror = None   # None | 'v' | 'h' | (p, q) line
        self.pivots = []       # order-sensitive pivoted ops (matrices)
        self.attached = []     # world points this shape was anchored at
        self.deleted = False

    def base(self):
        m = G.affine_identity()
        for p in self.pivots:
            m = G.affine_mul(p, m)
        return m

    def own_center(self):
        base = self.base()
        bb = G.bbox([G.affine_apply(base, p) for p in self.local])
        return ((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2)

    def matrix(self):
        return G.affine_mul(own_matrix(self, self.own_center()), self.base())


def own_matrix(o, c):
    """Own-anchor transforms: they commute, so restating one replaces it."""
    m = G.affine_identity()
    if o.t_mirror == "v":
        m = G.affine_mul(G.about(G.S(-1, 1), c), m)
    elif o.t_mirror == "h":
        m = G.affine_mul(G.about(G.S(1, -1), c), m)
    m = G.affine_mul(G.about(G.S(*o.t_scale), c), m)
    m = G.affine_mul(G.about(G.R(o.t_turn), c), m)
    m = G.affine_mul(G.T(*o.t_move), m)
    return m


def add_pivot(o, op, center):
    """An outside-pivot op is order-sensitive: bake what came before it."""
    own = own_matrix(o, center)
    if own != G.affine_identity():
        o.pivots.append(own)
    o.t_scale, o.t_turn, o.t_move, o.t_mirror = (1.0, 1.0), 0.0, (0.0, 0.0), None
    o.pivots.append(op)


class Group:
    def __init__(self, name, members):
        self.name = name
        self.members = list(members)
        self.t_scale = (1.0, 1.0)
        self.t_turn = 0.0
        self.t_move = (0.0, 0.0)
        self.t_mirror = None
        self.pivots = []
        self.center = None


class Session:
    def __init__(self):
        self.reset()
        self.statements = []

    # ---------------------------------------------------------------- state
    def reset(self):
        self.lattice = LATTICES["grid"](origin=(10.0, 7.0))
        self.canvas = (20.0, 14.0)
        self.origin_set = False
        self.modes = dict(DEFAULT_MODES)
        self.shapes = {}
        self.order = []
        self.groups = {}
        self.pen = self.lattice.origin
        self.heading = 0.0
        self.notes = []
        self.last_changed = None
        self.worry = None
        self.anon = 0

    # -------------------------------------------------------------- running
    def run_source(self, src, report=None):
        """Run a whole program; returns [(line, [notes])]."""
        out = []
        for st in parse(src):
            out.append((st.line, self.add(st)))
        return out

    def add(self, st):
        self.notes = []
        if st.kind == "revise" and self._revisable(st.target):
            try:
                self._apply_revision(st)
            except TzelError as e:
                self.notes.append(f"error: {e}")
                return self.notes
            self.statements.append(st)
            self.rebuild()
            self.notes = []
            self.last_changed = st.target.split(".")[0]
            self._push_checks(self.shapes.get(self.last_changed))
            self.note(f"revised `{st.target}`")
            return self.notes
        self.statements.append(st)
        try:
            self.execute(st, live=True)
        except TzelError as e:
            self.statements.pop()
            self.notes.append(f"error: {e}")
        except Exception as e:  # keep the session alive on internal bugs
            self.statements.pop()
            self.notes.append(f"internal error ({type(e).__name__}): {e}")
        return self.notes

    def rebuild(self):
        stmts = self.statements
        self.reset()
        self.statements = stmts
        for st in stmts:
            if st.kind in ("query", "revise", "error"):
                continue
            try:
                self.execute(st, live=False)
            except TzelError:
                pass

    def fork(self):
        s = Session()
        s.statements = list(self.statements)
        s.statements = [copy.deepcopy(x) for x in self.statements]
        s.rebuild()
        return s

    def note(self, msg):
        self.notes.append(msg)

    # ------------------------------------------------------------- dispatch
    def execute(self, st, live):
        k = st.kind
        if k == "error":
            raise TzelError(st.msg)
        if k == "query":
            if live:
                from . import queries
                queries.answer(self, st)
            return
        if k == "revise":
            raise TzelError(f"`{st.target}` isn't defined yet, and I can't "
                             f"read this line as anything else")
        getattr(self, "do_" + k.replace("-", "_"))(st, live)

    # ------------------------------------------------------------ settings
    def do_lattice(self, st, live):
        a = st.args
        if not a or a[0].lower() not in LATTICES:
            raise TzelError(f"lattice must be one of {', '.join(LATTICES)}")
        kw = self._kv(a[1:], {"scale": 1.0, "turn": 0.0})
        self.lattice = LATTICES[a[0].lower()](origin=self.lattice.origin,
                                              scale=number(kw["scale"]),
                                              turn=number(kw["turn"]))
        if self.pen == self.lattice.origin or not self.order:
            self.pen = self.lattice.origin

    def do_canvas(self, st, live):
        a = "".join(st.args).lower().replace("×", "x")
        try:
            w, h = (float(v) for v in a.split("x"))
        except ValueError:
            raise TzelError("canvas wants a size like 20x14")
        self.canvas = (w, h)
        if not self.origin_set:
            self.lattice.origin = (w / 2, h / 2)
            self.pen = self.lattice.origin

    def do_origin(self, st, live):
        p = self.point(parse_point(st.args[0]))
        self.lattice.origin = p
        self.origin_set = True
        self.pen = p

    def do_mode(self, st, live):
        a = [t for t in st.args if t != ","]
        if len(a) % 2:
            raise TzelError("mode wants pairs: mode snap lattice, feedback verbose")
        for k in range(0, len(a), 2):
            key, val = a[k].lower(), a[k + 1]
            if key not in DEFAULT_MODES:
                raise TzelError(f"unknown mode '{key}'; modes are "
                                 f"{', '.join(DEFAULT_MODES)}")
            self.modes[key] = val

    def do_style(self, st, live):
        # style X stroke:red fill:...
        targets = self.targets(st.args[0])
        from .parser import parse_bag
        bag = parse_bag(st.args[1:])
        for s in targets:
            self._apply_style(s, bag)

    def do_layer(self, st, live):
        a = [t for t in st.args if t != ","]
        if not a:
            raise TzelError("layer wants a name: layer front: tree, label")
        lname = a[0].rstrip(":")
        lv = LAYER_NAMES.get(lname)
        if lv is None:
            lv = number(lname)
        for t in a[1:]:
            if t == ":":
                continue
            for s in self.targets(t):
                s.layer = lv

    # --------------------------------------------------------------- shapes
    def do_path(self, st, live):
        name = st.name or self._anon("path")
        if name in self.shapes and live:
            self.note(f"`{name}` was already defined; this replaces it")
        sh = Shape(name, "path", st)
        start = None
        for bag in st.moves:
            for r, v in bag:
                if r == "from":
                    start = self.point(v)
                    sh.attached.append(start)
        p = start if start is not None else self.pen
        h = self.heading
        pts = [p]
        for idx, bag in enumerate(st.moves):
            if all(r in ("from",) for r, _ in bag):
                continue
            seg = self.eval_move(p, h, bag, idx > 0 or start is None, live)
            i0 = len(pts) - 1
            pts.extend(seg[1:])
            segname = None
            for r, v in bag:
                if r == "name":
                    segname = v.split(".")[-1]
            sh.segments[str(idx + 1)] = (i0, len(pts) - 1)
            if segname:
                sh.segments[segname] = (i0, len(pts) - 1)
            p = pts[-1]
            h = G.end_heading(seg)
            sh.ends.append(p)
            for r, v in bag:
                if r == "close":
                    if math.dist(p, pts[0]) > 1e-6:
                        pts.append(pts[0])
                    sh.closed = True
            self._apply_style(sh, bag)
        if len(pts) > 2 and math.dist(pts[0], pts[-1]) < 1e-6:
            sh.closed = True
        sh.local = pts
        self._store(sh)
        self.pen, self.heading = p, h
        if live:
            self._push_checks(sh)

    def eval_move(self, p, h, bag, continuing, live):
        d = {}
        for r, v in bag:
            d[r] = v
        kind = d.get("kind")
        if not kind:
            if "sweep" in d:
                kind = "arc"
            elif "pole" in d or "wind" in d:
                kind = "geo"
            elif "ctrl" in d or "peak" in d:
                kind = "quad"
            else:
                kind = "line"
        end = None
        if "end" in d:
            end = self.point(d["end"])
        elif "dir" in d:
            dn, n = d["dir"]
            end = G.add(p, self.lattice.step(dn, n))
        heading = d.get("heading")
        length = d.get("length")
        sense = d.get("sense", 1)
        snapped = False

        if kind == "line":
            if end is None:
                hd = heading if heading is not None else h
                L = length if length is not None else 1.0
                end = G.add(p, G.mul(G.unit_at(hd), L))
                end, snapped = self._snap(end)
            else:
                act_h = G.heading_of(G.sub(end, p))
                if heading is not None and abs(G.norm_deg(act_h - heading)) > 1.0:
                    self._mismatch(live, f"heading ∠{fmt(heading)}° but the endpoint "
                                   f"is at ∠{fmt(act_h, 1)}°; following the endpoint")
                if length is not None and abs(math.dist(p, end) - length) > 0.02:
                    self._mismatch(live, f"length ⟷{fmt(length)} but the endpoint is "
                                   f"{fmt(math.dist(p, end))} away; following the endpoint")
            pts = [p, end]

        elif kind == "arc":
            sweep = d.get("sweep")
            if end is not None:
                chord = math.dist(p, end)
                ch = G.heading_of(G.sub(end, p))
                if sweep is None and length is not None:
                    sweep = sense * G.solve_sweep_for_length(chord, length)
                elif sweep is None:
                    start_h = heading if heading is not None else (
                        h if (continuing and self.modes["fill-in"] == "smoothest") else None)
                    if start_h is None:
                        sweep = 90.0 * sense
                    else:
                        sweep = 2 * G.norm_deg(ch - start_h)
                elif length is not None:
                    th = math.radians(abs(sweep))
                    s_act = chord * (th / 2) / math.sin(th / 2) if th > 1e-9 else chord
                    if abs(s_act - length) > 0.05:
                        self._mismatch(live, f"arclength ⟷{fmt(length)} doesn't fit a "
                                       f"⌒{fmt(sweep)}° arc to that endpoint "
                                       f"(it would be {fmt(s_act)}); following the sweep")
                pts = G.sample_arc(p, end, sweep)
            else:
                sweep = 90.0 * sense if sweep is None else sweep
                hd = heading if heading is not None else h
                if "r" in d:
                    s = d["r"] * math.radians(abs(sweep))
                else:
                    s = length if length is not None else 1.0
                pts = G.arc_by_heading(p, hd, sweep, s)
                e2, snapped = self._snap(pts[-1])
                if snapped:
                    pts = G.sample_arc(p, e2, sweep)

        elif kind == "quad":
            if end is None:
                raise TzelError("a quad needs an endpoint (@ or →)")
            if "ctrl" in d:
                c = self.point(d["ctrl"])
            elif "peak" in d:
                m = G.mul(G.add(p, end), 0.5)
                v = G.sub(end, p)
                L = math.hypot(*v)
                left = (-v[1] / L, v[0] / L)
                c = G.add(m, G.mul(left, 2 * d["peak"]))
            else:
                hd = heading if heading is not None else h
                c = G.add(p, G.mul(G.unit_at(hd), math.dist(p, end) / 2))
            pts = G.sample_quad(p, c, end)

        elif kind == "geo":
            if end is None:
                raise TzelError("a geo move needs an endpoint (@ or →)")
            if "pole" in d:
                pole = self.point(d["pole"])
            elif self.modes["pole"] == "origin":
                pole = self.lattice.origin
            else:
                pole = self.point(parse_point(self.modes["pole"]))
            try:
                pts = G.sample_geo(p, end, pole, int(d.get("wind", 0)))
            except ValueError as e:
                raise TzelError(str(e))
        else:
            raise TzelError(f"unknown move kind '{kind}'")

        if snapped and live and self.modes["feedback"] != "quiet":
            self.note(f"snapped end to {self.lattice.describe(pts[-1])}")
        return pts

    def do_shape(self, st, live):
        name = st.name or self._anon(st.shape)
        sh = Shape(name, st.shape, st)
        d = {}
        rels = []
        for r, v in st.bag:
            if r == "rel":
                rels.append(v)
            else:
                d[r] = v
        if st.shape == "point":
            local = [(0.0, 0.0)]
            sh.style["hidden"] = True
        elif st.shape in ("circle", "dot"):
            r = d.get("r", 0.12 if st.shape == "dot" else 1.0)
            local = G.sample_circle((0.0, 0.0), r)
            sh.closed = True
            if st.shape == "dot" and "fill" not in d:
                sh.style["fill"] = self.modes["stroke"]
        elif st.shape == "rect":
            w, hh = d.get("w", 2.0), d.get("h", 1.0)
            local = [(-w / 2, -hh / 2), (w / 2, -hh / 2), (w / 2, hh / 2),
                     (-w / 2, hh / 2), (-w / 2, -hh / 2)]
            sh.closed = True
        else:  # text
            sh.text = d.get("string", "")
            sh.size = d.get("size", 1.0)
            w = 0.55 * sh.size * max(1, len(sh.text))
            hh = 0.75 * sh.size
            local = [(-w / 2, -hh / 2), (w / 2, -hh / 2), (w / 2, hh / 2),
                     (-w / 2, hh / 2), (-w / 2, -hh / 2)]
            sh.closed = True
        # placement
        c = self.pen
        if "at" in d:
            c = self.point(d["at"])
            sh.attached.append(c)
        bb = G.bbox(local)
        if "sitting-on" in d:
            q = self.point(d["sitting-on"])
            c = (q[0], q[1] - bb[1])
            sh.attached.append(q)
        gap = d.get("gap", 0.0)
        for rel, other in rels:
            ob = self.world_bbox(self.shape(other))
            ocx, ocy = (ob[0] + ob[2]) / 2, (ob[1] + ob[3]) / 2
            if rel == "below":
                c = (ocx, ob[1] - gap - bb[3])
            elif rel == "above":
                c = (ocx, ob[3] + gap - bb[1])
            elif rel == "left-of":
                c = (ob[0] - gap - bb[2], ocy)
            elif rel == "right-of":
                c = (ob[2] + gap - bb[0], ocy)
        sh.local = [G.add(p, c) for p in local]
        self._apply_style(sh, st.bag)
        self._store(sh)
        if live:
            self._push_checks(sh)

    # ------------------------------------------------------------ groups
    def do_group(self, st, live):
        a = [t for t in st.args if t not in (",",)]
        if not a:
            raise TzelError("group wants a name: group face: eyes, nose")
        gname = a[0].rstrip(":")
        rest = [t for t in a[1:] if t != ":"]
        members = []
        k = 0
        while k < len(rest):
            t = rest[k]
            if t == "last":
                spec = rest[k + 1] if k + 1 < len(rest) else "1"
                members += [s.name for s in self._last(spec)]
                k += 2
                continue
            members += [s.name for s in self.targets(t)]
            k += 1
        g = Group(gname, members)
        g.center = self._center([self.shapes[m] for m in members], with_groups=False)
        self.groups[gname] = g

    def do_del(self, st, live):
        for t in st.args:
            for s in self.targets(t):
                s.deleted = True
                self.order.remove(s.name)
                del self.shapes[s.name]

    # -------------------------------------------------------- transforms
    def _tx_target(self, st):
        a = [t for t in st.args if t != ","]
        if not a:
            raise TzelError(f"{st.kind} needs something to act on")
        if a[0] == "last":
            spec = a[1] if len(a) > 1 and a[1][0].isdigit() else "1"
            rest = a[2:] if len(a) > 1 and a[1][0].isdigit() else a[1:]
            objs = self._last(spec)
            if len(objs) > 1:
                g = Group(f"last{spec}", [o.name for o in objs])
                g.center = self._center(objs)
                return [g], rest
            return objs, rest
        if a[0] in self.groups:
            return [self.groups[a[0]]], a[1:]
        return [self.shape(a[0])], a[1:]

    def _around(self, rest):
        if "around" in rest:
            k = rest.index("around")
            return self.point(parse_point(rest[k + 1])), rest[:k] + rest[k + 2:]
        return None, rest

    def do_move(self, st, live):
        objs, rest = self._tx_target(st)
        for o in objs:
            c = G.add(self._obj_center(o), o.t_move)
            delta = (0.0, 0.0)
            for t in rest:
                if t.startswith(("→", "->")):
                    dn, n = parse_dir(t.lstrip("→").lstrip("->"))
                    delta = G.add(delta, self.lattice.step(dn, n))
                elif t.startswith("@") or t.startswith("to:"):
                    q = self.point(parse_point(t.split("@", 1)[-1].split("to:", 1)[-1]))
                    base = G.sub(c, o.t_move)
                    delta = G.sub(q, base)
                    o.t_move = (0.0, 0.0)
                elif t.startswith("+("):
                    i, j = (float(v) for v in t[2:-1].split(","))
                    delta = G.add(delta, self.lattice.vec(i, j))
                elif t == "by":
                    continue
                else:
                    raise TzelError(f"move doesn't understand '{t}'")
            o.t_move = delta
            self.last_changed = o.name
        if live:
            for o in objs:
                if isinstance(o, Shape):
                    self._push_checks(o)

    def do_turn(self, st, live):
        objs, rest = self._tx_target(st)
        piv, rest = self._around(rest)
        deg = number(rest[0]) if rest else 90.0
        for o in objs:
            if piv is None:
                o.t_turn = deg
            else:
                add_pivot(o, G.about(G.R(deg), piv), self._obj_center(o))
        self._touch(objs, live)

    def do_scale(self, st, live):
        objs, rest = self._tx_target(st)
        piv, rest = self._around(rest)
        if not rest:
            raise TzelError("scale needs a factor: scale face 1.5 or 2,0.5")
        f = "".join(rest).split(",")
        sx = number(f[0])
        sy = number(f[1]) if len(f) > 1 else sx
        for o in objs:
            if piv is None:
                o.t_scale = (sx, sy)
            else:
                add_pivot(o, G.about(G.S(sx, sy), piv), self._obj_center(o))
        self._touch(objs, live)

    def do_mirror(self, st, live):
        objs, rest = self._tx_target(st)
        mode = "v"
        if rest and rest[0] == "across":
            ref = rest[1]
            if ref in ("vertical", "v"):
                mode = "v"
            elif ref in ("horizontal", "h"):
                mode = "h"
            else:
                other = self.shape(ref)
                pts = self.world(other)
                mode = (pts[0], pts[-1])
        elif rest and rest[0] in ("horizontal", "h"):
            mode = "h"
        for o in objs:
            if isinstance(mode, tuple):
                add_pivot(o, G.mirror_line(*mode), self._obj_center(o))
            else:
                o.t_mirror = mode
        self._touch(objs, live)

    def do_repeat(self, st, live):
        objs, rest = self._tx_target(st)
        if not rest:
            raise TzelError("repeat needs a count: repeat petal 6 around bud.center")
        n = int(number(rest[0]))
        piv, rest2 = self._around(rest[1:])
        gname = None
        if "as" in rest2:
            gname = rest2[rest2.index("as") + 1]
        src = objs[0]
        if not isinstance(src, Shape):
            raise TzelError("repeat works on one shape for now")
        if piv is None:
            piv = self._center([src])
        names = [src.name]
        for k in range(1, n):
            cp = copy.deepcopy(src)
            cp.name = f"{src.name}#{k + 1}"
            add_pivot(cp, G.about(G.R(360.0 * k / n), piv), cp.own_center())
            self._store(cp)
            names.append(cp.name)
        gname = gname or f"{src.name}s"
        g = Group(gname, names)
        g.center = self._center([self.shapes[m] for m in names], with_groups=False)
        self.groups[gname] = g
        if live:
            self.note(f"made {n - 1} copies around {self.lattice.describe(piv)}; "
                      f"group `{gname}` holds all {n}")

    def do_copy(self, st, live):
        # copy X as Y  (the copy keeps X's transforms; style terms may follow)
        a = [t for t in st.args if t != ","]
        if "as" not in a:
            raise TzelError("copy needs a new name: copy leaf as leaf2")
        k = a.index("as")
        src = self.shape(a[0])
        new = a[k + 1]
        cp = copy.deepcopy(src)
        cp.name = new
        cp.stmt = src.stmt
        from .parser import parse_bag
        self._apply_style(cp, parse_bag(a[k + 2:]))
        self._store(cp)
        if live:
            self.note(f"`{new}` is a copy of `{src.name}`, in the same place")

    def _obj_center(self, o):
        return o.own_center() if isinstance(o, Shape) else self._group_own_center(o)

    def _touch(self, objs, live):
        for o in objs:
            self.last_changed = o.name
            if live and isinstance(o, Shape):
                self._push_checks(o)

    # ------------------------------------------------------------- asserts
    def do_assert(self, st, live):
        a = [t for t in st.args if t != ","]
        ok, why = True, ""
        if a[:2] == ["inside", "canvas"]:
            for t in a[2:]:
                for s in self.targets(t):
                    bb = self.world_bbox(s)
                    if bb[0] < 0 or bb[1] < 0 or bb[2] > self.canvas[0] or bb[3] > self.canvas[1]:
                        ok, why = False, f"`{s.name}` leaves the canvas"
        elif a[0] == "no-overlap":
            A, B = self.shape(a[1]), self.shape(a[2])
            if G.intersections(self.world(A), self.world(B)):
                ok, why = False, f"`{A.name}` and `{B.name}` cross"
        elif a[0] == "gap":
            A, B = self.shape(a[1]), self.shape(a[2])
            want = number(a[-1])
            got = G.min_distance(self.world(A), self.world(B))
            if got < want:
                ok, why = False, f"gap is {fmt(got)}, wanted at least {fmt(want)}"
        else:
            raise TzelError("assert knows: inside canvas X, no-overlap A B, gap A B >= n")
        if live:
            self.note("PASS" if ok else f"FAIL: {why}")

    # ------------------------------------------------------------ revision
    def _revisable(self, target):
        base = target.split(".")[0]
        return base in self.shapes

    def _apply_revision(self, rv):
        base, _, seg = rv.target.partition(".")
        sh = self.shapes[base]
        st = sh.stmt
        if st.kind == "path":
            if seg:
                idx = None
                for k, bag in enumerate(st.moves):
                    if str(k + 1) == seg or any(r == "name" and v.split(".")[-1] == seg
                                                for r, v in bag):
                        idx = k
                if idx is None:
                    raise TzelError(f"`{base}` has no segment `{seg}`")
            elif all(r in STYLE_ROLES for r, _ in rv.bag):
                idx = len(st.moves) - 1
            else:
                if len(st.moves) != 1:
                    raise TzelError(f"`{base}` has {len(st.moves)} moves; "
                                     f"name one, like {base}.1")
                idx = 0
            st.moves[idx] = merge_bag(st.moves[idx], rv.bag)
        else:
            st.bag = merge_bag(st.bag, rv.bag)

    # ------------------------------------------------------------- helpers
    def _store(self, sh):
        if sh.name in self.shapes:
            self.order.remove(sh.name)
        self.shapes[sh.name] = sh
        self.order.append(sh.name)
        self.last_changed = sh.name

    def _anon(self, kind):
        self.anon += 1
        return f"{kind}{self.anon}"

    def _apply_style(self, sh, bag):
        for r, v in bag:
            if r in ("stroke", "fill", "dash"):
                sh.style[r] = v
            elif r in ("width", "opacity"):
                sh.style[r] = v
            elif r == "layer":
                sh.layer = LAYER_NAMES.get(v, None) if v in LAYER_NAMES else number(v)

    def _snap(self, p):
        m = self.modes["snap"]
        if m == "off":
            return p, False
        q = self.lattice.nearest(p, half=(m == "half"))
        return q, math.dist(p, q) > 1e-6

    def _mismatch(self, live, msg):
        m = self.modes["mismatch"]
        if m == "error":
            raise TzelError(msg)
        if m == "warn" and live:
            self.note("mismatch: " + msg)

    def _kv(self, toks, defaults):
        d = dict(defaults)
        for k in range(0, len(toks) - 1, 2):
            d[toks[k].lower()] = toks[k + 1]
        return d

    def _last(self, spec):
        """'2' -> [two ago]; '3..1' -> last three; default newest."""
        live = [self.shapes[n] for n in self.order]
        if ".." in spec:
            a, b = (int(v) for v in spec.split(".."))
            hi, lo = max(a, b), min(a, b)
            return live[len(live) - hi: len(live) - lo + 1]
        k = int(spec)
        if k > len(live):
            raise TzelError(f"there are only {len(live)} shapes")
        return [live[-k]]

    def targets(self, t):
        t = t.rstrip(",")
        if t in self.groups:
            return [self.shapes[m] for m in self.groups[t].members if m in self.shapes]
        if t == "last":
            return self._last("1")
        return [self.shape(t)]

    def shape(self, name):
        base = name.split(".")[0]
        if base not in self.shapes:
            near = [n for n in self.order if n.startswith(base[:2])]
            hint = f" (did you mean {', '.join(near[:3])}?)" if near else ""
            raise TzelError(f"`{base}` isn't defined{hint}")
        return self.shapes[base]

    # ------------------------------------------------------ world geometry
    def groups_of(self, sh):
        return [g for g in self.groups.values() if sh.name in g.members]

    def matrix(self, sh, with_groups=True):
        m = sh.matrix()
        if with_groups:
            for g in self.groups_of(sh):
                m = G.affine_mul(self._group_matrix(g), m)
        return m

    def _group_base(self, g):
        m = G.affine_identity()
        for p in g.pivots:
            m = G.affine_mul(p, m)
        return m

    def _group_own_center(self, g):
        return G.affine_apply(self._group_base(g), g.center or (0.0, 0.0))

    def _group_matrix(self, g):
        return G.affine_mul(own_matrix(g, self._group_own_center(g)), self._group_base(g))

    def _group_center(self, g):
        return G.affine_apply(self._group_matrix(g), g.center)

    def world(self, sh, with_groups=True):
        m = self.matrix(sh, with_groups)
        return [G.affine_apply(m, p) for p in sh.local]

    def anchor_world(self, sh, p):
        return G.affine_apply(self.matrix(sh), p)

    def world_bbox(self, sh):
        return G.bbox(self.world(sh))

    def _center(self, objs, with_groups=True):
        bbs = []
        for o in objs:
            if isinstance(o, Group):
                bbs += [G.bbox(self.world(self.shapes[m])) for m in o.members]
            else:
                bbs.append(G.bbox(self.world(o, with_groups)))
        bb = G.bbox_union(bbs)
        return ((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2)

    # ---------------------------------------------------------- points
    def point(self, expr):
        base, steps = expr
        if base[0] == "lat":
            _, lname, i, j = base
            lat = self.lattice
            if lname and lname != lat.name:
                if lname not in LATTICES:
                    raise TzelError(f"unknown lattice '{lname}'")
                lat = LATTICES[lname](origin=lat.origin, scale=lat.scale,
                                      turn=math.degrees(lat.turn))
            p = lat.to_world(i, j)
        else:
            _, name, anchor, arg = base
            p = self.anchor(name, anchor, arg)
        for s in steps:
            if s[0] == "dir":
                p = G.add(p, self.lattice.step(s[1], s[2]))
            else:
                p = G.add(p, self.lattice.vec(s[1], s[2]))
        return p

    def anchor(self, name, anchor, arg):
        if name == "here":
            return self.pen
        if name == "origin":
            return self.lattice.origin
        if name == "canvas":
            w, h = self.canvas
            bb = (0.0, 0.0, w, h)
            return box_anchor(bb, anchor or "center")
        sh = self.shape(name)
        pts = self.world(sh)
        if anchor is None:
            anchor = "center"
        if anchor in ("start",):
            return pts[0]
        if anchor in ("end",):
            return pts[-1]
        if anchor in ("at", "edge"):
            return G.point_at(pts, arg if arg is not None else 0.5)
        if anchor in sh.segments:
            i0, i1 = sh.segments[anchor]
            sub = pts[i0:i1 + 1]
            return G.point_at(sub, arg if arg is not None else 0.5)
        if anchor == "centroid":
            return G.centroid(pts[:-1] if sh.closed else pts)
        bb = G.bbox(pts)
        try:
            return box_anchor(bb, anchor)
        except KeyError:
            raise TzelError(f"`{name}` has no anchor `.{anchor}`; try start, end, "
                             f"at(0.5), center, top, bottom, left, right, top-left...")

    # -------------------------------------------------------- push notes
    def _push_checks(self, sh):
        if sh is None or self.modes["feedback"] == "quiet":
            return
        pts = self.world(sh)
        W, H = self.canvas
        bb = G.bbox(pts)
        over = []
        if bb[0] < -1e-6: over.append(f"left by {fmt(-bb[0])}")
        if bb[2] > W + 1e-6: over.append(f"right by {fmt(bb[2] - W)}")
        if bb[1] < -1e-6: over.append(f"bottom by {fmt(-bb[1])}")
        if bb[3] > H + 1e-6: over.append(f"top by {fmt(bb[3] - H)}")
        if over:
            self.note(f"`{sh.name}` leaves the canvas: " + ", ".join(over))
            self.worry = ((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2)
        att = [G.affine_apply(self.matrix(sh), a) for a in sh.attached] + [pts[0], pts[-1]]
        tol = 0.08
        if sh.kind in ("circle", "dot"):
            bbl = G.bbox(sh.local)
            tol += (bbl[2] - bbl[0]) / 2 * max(abs(v) for v in sh.t_scale)
        for n in self.order:
            if n == sh.name:
                continue
            o = self.shapes[n]
            hits = G.intersections(pts, self.world(o))
            hits = [q for q in hits if all(math.dist(q, a) > tol for a in att)]
            if hits:
                where = ", ".join(self.lattice.describe(q) for q in hits[:3])
                more = f" (+{len(hits) - 3} more)" if len(hits) > 3 else ""
                self.note(f"`{sh.name}` crosses `{n}` near {where}{more}")
                self.worry = hits[0]
        if self.modes["feedback"] == "verbose":
            self.note(f"`{sh.name}` ends {self.lattice.describe(pts[-1])}, "
                      f"heading {compass(G.end_heading(pts))}")


def box_anchor(bb, a):
    x0, y0, x1, y1 = bb
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    table = {
        "center": (cx, cy), "top": (cx, y1), "bottom": (cx, y0),
        "left": (x0, cy), "right": (x1, cy),
        "top-left": (x0, y1), "top-right": (x1, y1),
        "bottom-left": (x0, y0), "bottom-right": (x1, y0),
    }
    return table[a]


def merge_bag(old, new):
    """New terms replace old terms with the same role."""
    excl = {"end": {"end", "dir"}, "dir": {"end", "dir"}}
    roles = set()
    for r, _ in new:
        roles |= excl.get(r, {r})
    implied = {"sweep": "arc", "pole": "geo", "wind": "geo", "ctrl": "quad", "peak": "quad"}
    kinds = {implied[r] for r, _ in new if r in implied}
    out = [(r, v) for r, v in old if r not in roles]
    if kinds and not any(r == "kind" for r, _ in new):
        out = [(r, v) for r, v in out if r != "kind"]
    return out + list(new)
