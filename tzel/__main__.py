"""Command line: run a .tz file, or draw interactively.

  python -m tzel run drawing.tz            # feedback + drawing.svg
  python -m tzel run drawing.tz --png      # also drawing.png
  python -m tzel run drawing.tz --from 40  # only show feedback from line 40 on
  python -m tzel repl                      # type statements, get answers
"""
import argparse
import os
import sys

from .interp import Session
from .parser import parse_statement, logical_lines
from .render import to_svg, svg_to_png


def show(line, notes, stream=sys.stdout):
    for n in notes:
        print(f"« L{line} {n}", file=stream)


def cmd_run(args):
    with open(args.file, encoding="utf-8") as fh:
        src = fh.read()
    sess = Session()
    base = os.path.splitext(args.file)[0]
    from . import queries
    queries.OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(args.file)), "out")
    for line, notes in sess.run_source(src):
        if line >= args.from_line:
            show(line, notes)
    svg = to_svg(sess)
    out_svg = args.svg or base + ".svg"
    with open(out_svg, "w", encoding="utf-8") as fh:
        fh.write(svg)
    print(f"wrote {out_svg}")
    if args.png:
        out_png = base + ".png"
        with open(out_png, "wb") as fh:
            fh.write(svg_to_png(svg, width=args.width))
        print(f"wrote {out_png}")


def cmd_repl(args):
    sess = Session()
    print("tzel repl - end a line with \\ to continue it; :svg FILE to save; :q to quit")
    buf, n = "", 0
    while True:
        try:
            line = input("... " if buf else "tz> ")
        except EOFError:
            break
        n += 1
        if line.strip() in (":q", ":quit"):
            break
        if line.startswith(":svg"):
            path = line.split(None, 1)[1] if " " in line else "repl.svg"
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(to_svg(sess))
            print(f"wrote {path}")
            continue
        if line.endswith("\\"):
            buf += line[:-1] + " ; "
            continue
        text, buf = buf + line, ""
        for ln, t in logical_lines(text):
            st = parse_statement(n, t)
            if st:
                show(n, sess.add(st))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tzel")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("file")
    r.add_argument("--svg")
    r.add_argument("--png", action="store_true")
    r.add_argument("--width", type=int, default=800)
    r.add_argument("--from", dest="from_line", type=int, default=0)
    r.set_defaults(fn=cmd_run)
    p = sub.add_parser("repl")
    p.set_defaults(fn=cmd_repl)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
