"""SVG output, PNG rasterizing, and glance tiles."""
import io
import math

from . import geom as G

PX = 40  # pixels per lattice unit in full renders


def _xy(p):
    return f"{p[0]:.3f},{-p[1]:.3f}"


def lattice_dots(sess, view):
    x0, y0, x1, y1 = view
    lat = sess.lattice
    out = []
    corners = [lat.to_lattice(p) for p in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
    imin = int(math.floor(min(c[0] for c in corners))) - 1
    imax = int(math.ceil(max(c[0] for c in corners))) + 1
    jmin = int(math.floor(min(c[1] for c in corners))) - 1
    jmax = int(math.ceil(max(c[1] for c in corners))) + 1
    if (imax - imin) * (jmax - jmin) > 20000:
        return ""
    for i in range(imin, imax + 1):
        for j in range(jmin, jmax + 1):
            p = lat.to_world(i, j)
            if x0 - 0.01 <= p[0] <= x1 + 0.01 and y0 - 0.01 <= p[1] <= y1 + 0.01:
                r = 0.07 if (i, j) == (0, 0) else 0.035
                out.append(f'<circle cx="{p[0]:.3f}" cy="{-p[1]:.3f}" r="{r}" fill="#9aa4b2"/>')
    return "\n".join(out)


def to_svg(sess, view=None, px=PX, lattice=None, background="#ffffff"):
    W, H = sess.canvas
    view = view or (0.0, 0.0, W, H)
    x0, y0, x1, y1 = view
    vw, vh = x1 - x0, y1 - y0
    show = sess.modes.get("show-lattice") == "on" if lattice is None else lattice
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{vw * px:.0f}" '
             f'height="{vh * px:.0f}" viewBox="{x0:.3f} {-y1:.3f} {vw:.3f} {vh:.3f}">',
             f'<rect x="{x0}" y="{-y1}" width="{vw}" height="{vh}" fill="{background}"/>']
    if show:
        parts.append(lattice_dots(sess, view))
    items = sorted(enumerate(sess.order), key=lambda t: (sess.shapes[t[1]].layer, t[0]))
    for _, n in items:
        sh = sess.shapes[n]
        pts = sess.world(sh)
        st = sh.style
        if st.get("hidden"):
            continue
        stroke = st.get("stroke", sess.modes["stroke"])
        width = st.get("width", sess.modes["width"])
        fill = st.get("fill", sess.modes["fill"] if sh.closed else "none")
        op = st.get("opacity")
        extra = f' opacity="{op}"' if op is not None else ""
        if st.get("dash"):
            extra += f' stroke-dasharray="{st["dash"]}"'
        if sh.kind == "text":
            lc = G.centroid(sh.local[:-1])
            a, b, c, d, e, f = sess.matrix(sh)
            color = st.get("fill") if st.get("fill") not in (None, "none") else stroke
            txt = (sh.text or "").replace("&", "&amp;").replace("<", "&lt;")
            parts.append(f'<text x="{lc[0]:.3f}" y="{-lc[1]:.3f}" font-size="{sh.size * 0.9:.3f}" '
                         f'font-family="Georgia, serif" text-anchor="middle" '
                         f'dominant-baseline="central" fill="{color}" '
                         f'transform="matrix({a:.5f} {-b:.5f} {-c:.5f} {d:.5f} {e:.4f} {-f:.4f})"'
                         f'{extra}>{txt}</text>')
            continue
        d = "M" + " L".join(_xy(p) for p in pts) + (" Z" if sh.closed else "")
        parts.append(f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{width}" '
                     f'stroke-linecap="round" stroke-linejoin="round"{extra}/>')
    parts.append("</svg>")
    return "\n".join(parts)


def svg_to_png(svg, width=None):
    try:
        import cairosvg
        return cairosvg.svg2png(bytestring=svg.encode(), output_width=width)
    except (ImportError, OSError):
        pass
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("PNG output needs cairosvg or playwright "
                           "(pip install cairosvg)")
    import re
    m = re.search(r'width="([\d.]+)" height="([\d.]+)"', svg)
    w, h = float(m.group(1)), float(m.group(2))
    scale = (width / w) if width else 1
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": int(w * scale), "height": int(h * scale)})
        pg.set_content(f'<html><body style="margin:0">'
                       f'<div style="transform:scale({scale});transform-origin:0 0">{svg}</div></body></html>')
        data = pg.screenshot()
        b.close()
    return data


def _square(c, half):
    return (c[0] - half, c[1] - half, c[0] + half, c[1] + half)


def glance_views(sess, landmark=None):
    """Pick four views: overview, latest, worry, landmark."""
    W, H = sess.canvas
    s = max(W, H)
    views = [("overview", ((W - s) / 2, (H - s) / 2, (W + s) / 2, (H + s) / 2))]

    def view_of(sh, pad=1.6):
        bb = sess.world_bbox(sh) if not hasattr(sh, "members") else G.bbox_union(
            [sess.world_bbox(sess.shapes[m]) for m in sh.members if m in sess.shapes])
        c = ((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2)
        half = max(bb[2] - bb[0], bb[3] - bb[1], 1.0) / 2 * pad
        return _square(c, half)

    latest = (sess.shapes.get(sess.last_changed) or sess.groups.get(sess.last_changed)) \
        if sess.last_changed else None
    if latest is None and sess.order:
        latest = sess.shapes[sess.order[-1]]
    if latest:
        views.append((f"latest: {latest.name}", view_of(latest)))
    if sess.worry:
        views.append(("worry", _square(sess.worry, 1.5)))
    if landmark is None and sess.order:
        def weight(n):
            sh = sess.shapes[n]
            bb = sess.world_bbox(sh)
            return (bb[2] - bb[0]) * (bb[3] - bb[1])
        cands = [n for n in sess.order if not latest or n != latest.name] or sess.order
        landmark = max(cands, key=weight)
    if landmark:
        views.append((f"landmark: {landmark}", view_of(sess.shapes[landmark], 1.3)))
    while len(views) < 4:
        views.append(("(empty)", None))
    return views[:4]


def glance(sess, path, landmark=None, cell=32):
    from PIL import Image
    views = glance_views(sess, landmark)
    gut = 2
    tile = Image.new("RGB", (cell * 2 + gut, cell * 2 + gut), (128, 128, 128))
    labels = []
    for k, (label, v) in enumerate(views):
        x, y = (k % 2) * (cell + gut), (k // 2) * (cell + gut)
        if v is None:
            img = Image.new("RGB", (cell, cell), (235, 235, 235))
        else:
            svg = to_svg(sess, view=v, px=cell / (v[2] - v[0]), lattice=False)
            img = Image.open(io.BytesIO(svg_to_png(svg, width=cell))).convert("RGB")
            img = img.resize((cell, cell))
        tile.paste(img, (x, y))
        if v is None:
            labels.append(f"view {k + 1}: {label}")
        else:
            zoom = max(sess.canvas) / (v[2] - v[0])
            ctr = sess.lattice.describe(((v[0] + v[2]) / 2, (v[1] + v[3]) / 2))
            labels.append(f"view {k + 1} {label}: centered {ctr}, "
                          f"{v[2] - v[0]:.1f} cells wide, {zoom:.1f}×")
    tile.save(path)
    big = path.rsplit(".", 1)[0] + "-x4.png"
    tile.resize((tile.width * 4, tile.height * 4), Image.NEAREST).save(big)
    return labels, big
