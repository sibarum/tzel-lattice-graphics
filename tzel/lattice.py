"""Lattices: addressable points and direction vocabularies.

World space is y-up, measured in lattice units (neighbor spacing = 1).
"""
import math

SQ3 = math.sqrt(3.0)


class Lattice:
    name = "lattice"
    basis = ((1.0, 0.0), (0.0, 1.0))
    directions = {}  # name -> (di, dj) step in lattice coords

    def __init__(self, origin=(0.0, 0.0), scale=1.0, turn=0.0):
        self.origin = origin
        self.scale = scale
        self.turn = math.radians(turn)

    def _rot(self, v):
        c, s = math.cos(self.turn), math.sin(self.turn)
        return (v[0] * c - v[1] * s, v[0] * s + v[1] * c)

    def vec(self, i, j):
        """Lattice displacement (i, j) -> world displacement."""
        (ax, ay), (bx, by) = self.basis
        v = (i * ax + j * bx, i * ay + j * by)
        v = self._rot(v)
        return (v[0] * self.scale, v[1] * self.scale)

    def to_world(self, i, j):
        v = self.vec(i, j)
        return (self.origin[0] + v[0], self.origin[1] + v[1])

    def to_lattice(self, p):
        """World point -> fractional lattice coords."""
        x, y = p[0] - self.origin[0], p[1] - self.origin[1]
        c, s = math.cos(-self.turn), math.sin(-self.turn)
        x, y = (x * c - y * s) / self.scale, (x * s + y * c) / self.scale
        (ax, ay), (bx, by) = self.basis
        det = ax * by - ay * bx
        i = (x * by - y * bx) / det
        j = (ax * y - ay * x) / det
        return (i, j)

    def nearest(self, p, half=False):
        """Nearest lattice point (world coords) to world point p."""
        i, j = self.to_lattice(p)
        step = 0.5 if half else 1.0
        bi, bj = round(i / step), round(j / step)
        best, bd = None, float("inf")
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                q = self.to_world((bi + di) * step, (bj + dj) * step)
                d = math.dist(q, p)
                if d < bd:
                    best, bd = q, d
        return best

    def step(self, dname, n=1.0):
        key = dname.upper()
        if key not in self.directions:
            raise TzelError(
                f"'{dname}' is not a direction on the {self.name} lattice; "
                f"try one of {' '.join(self.directions)}")
        di, dj = self.directions[key]
        return self.vec(di * n, dj * n)

    def describe(self, p):
        i, j = self.to_lattice(p)
        return f"{self.name}({fmt(i)},{fmt(j)})"


class Grid(Lattice):
    name = "grid"
    basis = ((1.0, 0.0), (0.0, 1.0))
    directions = {
        "E": (1, 0), "N": (0, 1), "W": (-1, 0), "S": (0, -1),
        "NE": (1, 1), "NW": (-1, 1), "SW": (-1, -1), "SE": (1, -1),
    }


class Hex(Lattice):
    """Hex cell centers (a triangular point lattice): 6 neighbors at 60 deg."""
    name = "hex"
    basis = ((1.0, 0.0), (0.5, SQ3 / 2))
    directions = {
        "E": (1, 0), "NE": (0, 1), "NW": (-1, 1),
        "W": (-1, 0), "SW": (0, -1), "SE": (1, -1),
    }


LATTICES = {"grid": Grid, "hex": Hex}


class TzelError(Exception):
    pass


def fmt(v, nd=2):
    if abs(v - round(v)) < 1e-6:
        return str(int(round(v)))
    s = f"{v:.{nd}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "-0" if s == "-0" else s
