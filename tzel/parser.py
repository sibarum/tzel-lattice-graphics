"""Tokenizer and parser for Tzel.

Every term marks its own role, so a move is an unordered bag of
(role, value) pairs. Glyph markers and word forms are interchangeable.
"""
import re
from .lattice import TzelError

KIND_WORDS = {"line", "arc", "quad", "geo"}
SHAPE_WORDS = {"circle", "rect", "text", "dot", "path", "point"}
STATEMENT_WORDS = {"lattice", "canvas", "origin", "mode", "group", "layer",
                   "assert", "del", "move", "turn", "scale", "mirror",
                   "repeat", "style", "with", "copy"}
# word-form markers and the roles they map to
WORD_MARKERS = {
    "to:": "end", "toward:": "dir", "angle:": "heading", "len:": "length",
    "sweep:": "sweep", "pole:": "pole",
}
GLYPH_MARKERS = [
    ("@", "end"), ("→", "dir"), ("->", "dir"), ("∠", "heading"),
    ("⟷", "length"), ("<->", "length"), ("⌒", "sweep"), ("~", "sweep"),
    ("⊙", "pole"),
]
STYLE_KEYS = {"stroke", "fill", "width", "layer", "opacity", "dash",
              "r", "w", "h", "size", "weight"}
TAKES_ARG = {"wind", "as", "from", "ctrl", "peak", "bulge", "at",
             "sitting-on", "below", "above", "left-of", "right-of", "gap"}


class Stmt:
    def __init__(self, line, text, kind, **kw):
        self.line = line
        self.text = text
        self.kind = kind
        self.__dict__.update(kw)

    def __repr__(self):
        return f"<Stmt L{self.line} {self.kind} {self.text!r}>"


def tokenize(s):
    """Split on whitespace, keeping quoted strings and balanced parens."""
    toks, cur, depth, q = [], "", 0, False
    for ch in s:
        if q:
            cur += ch
            if ch == '"':
                q = False
            continue
        if ch == '"':
            q = True
            cur += ch
        elif ch in "([{":
            depth += 1
            cur += ch
        elif ch in ")]}":
            depth -= 1
            cur += ch
        elif ch.isspace() and depth == 0:
            if cur:
                toks.append(cur)
            cur = ""
        elif ch == ";" and depth == 0:
            if cur:
                toks.append(cur)
            toks.append(";")
            cur = ""
        elif ch == "," and depth == 0:
            if cur:
                toks.append(cur)
            toks.append(",")
            cur = ""
        else:
            cur += ch
    if cur:
        toks.append(cur)
    return toks


def number(tok):
    if isinstance(tok, (int, float)):
        return float(tok)
    t = tok.replace("°", "").replace("−", "-").strip()
    try:
        return float(t)
    except ValueError:
        raise TzelError(f"expected a number, got '{tok}'")


# ---- point expressions ------------------------------------------------------

STEP_RE = re.compile(r"(?:→|->)([A-Za-z]+)(?:[×x*](-?[\d.]+))?")
LAT_RE = re.compile(r"^(?:([a-z]+))?\((-?[\d.]+),\s*(-?[\d.]+)\)")
ANCHOR_RE = re.compile(r"^([A-Za-z_][\w\-#]*)(?:\.([\w\-]+)(?:\((-?[\d.]+)\))?)?")
OFF_RE = re.compile(r"\+\((-?[\d.]+),\s*(-?[\d.]+)\)")


def parse_point(s):
    s = s.replace("−", "-")
    m = LAT_RE.match(s)
    if m:
        base = ("lat", m.group(1), float(m.group(2)), float(m.group(3)))
        rest = s[m.end():]
    else:
        m = ANCHOR_RE.match(s)
        if not m:
            raise TzelError(f"can't read '{s}' as a point")
        base = ("anchor", m.group(1), m.group(2), m.group(3) and float(m.group(3)))
        rest = s[m.end():]
    steps = []
    while rest:
        m = STEP_RE.match(rest)
        if m:
            steps.append(("dir", m.group(1), float(m.group(2)) if m.group(2) else 1.0))
            rest = rest[m.end():]
            continue
        m = OFF_RE.match(rest)
        if m:
            steps.append(("off", float(m.group(1)), float(m.group(2))))
            rest = rest[m.end():]
            continue
        raise TzelError(f"can't read '{rest}' at the end of point '{s}'")
    return (base, steps)


def parse_dir(s):
    m = re.match(r"^([A-Za-z]+)(?:[×x*](-?[\d.]+))?$", s)
    if not m:
        raise TzelError(f"can't read direction '{s}'")
    return (m.group(1), float(m.group(2)) if m.group(2) else 1.0)


# ---- term bags --------------------------------------------------------------

def parse_bag(toks):
    """Parse a list of tokens into a bag: list of (role, value)."""
    bag, k = [], 0
    while k < len(toks):
        t = toks[k]
        k += 1
        if t == ",":
            continue
        low = t.lower()
        if low in KIND_WORDS:
            bag.append(("kind", low))
            continue
        if low == "close":
            bag.append(("close", True))
            continue
        if low in ("cw", "ccw"):
            bag.append(("sense", -1 if low == "cw" else 1))
            continue
        if low == "symmetric":
            continue
        if low in TAKES_ARG:
            if k >= len(toks):
                raise TzelError(f"'{t}' needs a value after it")
            v = toks[k]
            k += 1
            if low in ("wind", "peak", "bulge", "gap"):
                bag.append((low, number(v)))
            elif low == "as":
                bag.append(("name", v))
            elif low in ("below", "above", "left-of", "right-of"):
                bag.append(("rel", (low, v)))
            else:  # from ctrl at sitting-on
                bag.append((low, parse_point(v)))
            continue
        matched = False
        for w, role in WORD_MARKERS.items():
            if low.startswith(w):
                bag.append(_role(role, t[len(w):]))
                matched = True
                break
        if matched:
            continue
        for g, role in GLYPH_MARKERS:
            if t.startswith(g):
                bag.append(_role(role, t[len(g):]))
                matched = True
                break
        if matched:
            continue
        if ":" in t and t.split(":", 1)[0].lower() in STYLE_KEYS:
            key, val = t.split(":", 1)
            key = key.lower()
            if key in ("r", "w", "h", "size", "width", "opacity"):
                bag.append((key, number(val)))
            else:
                bag.append((key, val))
            continue
        if t.startswith('"') and t.endswith('"'):
            bag.append(("string", t[1:-1]))
            continue
        raise TzelError(f"don't know what '{t}' is doing here")
    return bag


def _role(role, v):
    if role == "end":
        return ("end", parse_point(v))
    if role == "dir":
        return ("dir", parse_dir(v))
    if role == "pole":
        return ("pole", parse_point(v))
    return (role, number(v))


def split_moves(toks):
    moves, cur = [], []
    for t in toks:
        if t == ";":
            if cur:
                moves.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        moves.append(cur)
    return moves


# ---- statements -------------------------------------------------------------

NAME_RE = re.compile(r"^([A-Za-z_][\w\-]*):$")


def logical_lines(src):
    """Join indented continuation lines onto their statement with ';'."""
    out = []
    for n, raw in enumerate(src.splitlines(), 1):
        line = strip_comment(raw).rstrip()
        if not line.strip():
            continue
        if raw[:1] in (" ", "\t") and out and not line.strip().startswith("?"):
            ln, txt = out[-1]
            out[-1] = (ln, txt + " ; " + line.strip())
        else:
            out.append((n, line.strip()))
    return out


HEXCOLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")


def strip_comment(s):
    """'#' starts a comment unless it begins a hex color like #3b6e2a."""
    q = False
    for i, ch in enumerate(s):
        if ch == '"':
            q = not q
        elif ch == "#" and not q:
            if i > 0 and s[i - 1] in ":=" and HEXCOLOR_RE.match(s, i):
                continue
            return s[:i]
    return s


def parse_statement(line, text):
    toks = tokenize(text)
    if not toks:
        return None
    head = toks[0]
    low = head.lower()
    if head.startswith("?"):
        return Stmt(line, text, "query", q=head[1:].lower(), args=toks[1:])
    if low in STATEMENT_WORDS:
        return Stmt(line, text, low, args=toks[1:])
    name = None
    m = NAME_RE.match(head)
    if m:
        name = m.group(1)
        toks = toks[1:]
    elif len(toks) > 1 and toks[1] == ":" :
        name, toks = head, toks[2:]
    if not toks:
        raise TzelError("a name needs something after it")
    first = toks[0].lower()
    if name is None and first not in SHAPE_WORDS and first not in KIND_WORDS \
            and re.match(r"^[A-Za-z_][\w\-#]*(\.[\w\-]+)?$", toks[0]) \
            and first not in TAKES_ARG and first not in ("close", "cw", "ccw"):
        return Stmt(line, text, "revise", target=toks[0], bag=parse_bag(toks[1:]))
    if first in ("circle", "rect", "text", "dot", "point"):
        return Stmt(line, text, "shape", name=name, shape=first,
                    bag=parse_bag(toks[1:]))
    if first == "path":
        toks = toks[1:]
    moves = [parse_bag(m) for m in split_moves(toks)]
    return Stmt(line, text, "path", name=name, moves=moves)


def parse(src):
    stmts = []
    for line, text in logical_lines(src):
        try:
            s = parse_statement(line, text)
        except TzelError as e:
            s = Stmt(line, text, "error", msg=str(e))
        if s:
            stmts.append(s)
    return stmts
