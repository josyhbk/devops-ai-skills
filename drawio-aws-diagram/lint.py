#!/usr/bin/env python3
"""Structural lint for .drawio files. Stdlib only.

Checks: duplicate ids, dangling parent/source/target, unknown AWS/Kubernetes
shape names (vs shapes.txt), icons whose footprint (icon + label) overlaps
another icon, and shapes that sit inside a container without being parented
to it (they render fine but get left behind when the container is moved).

Usage: python3 lint.py FILE.drawio|FILE.drawio.png [...]   exit 1 on any error
       python3 lint.py --xml FILE.drawio.png               print the embedded XML
       python3 lint.py --selftest
"""
import base64
import re
import struct
import sys
import zlib
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

SHAPES = set(Path(__file__).with_name("shapes.txt").read_text().split())
NAME_RE = re.compile(r"(mxgraph\.(?:aws4|kubernetes)\.[A-Za-z0-9_]+|prIcon=[a-z0-9_]+)")
LINE_H, CHAR_W = 15, 6.5  # rough label metrics at fontSize 12


def read_drawio(path):
    """XML text of a .drawio file, or the copy embedded in a .drawio.png (zTXt chunk)."""
    data = Path(path).read_bytes()
    if not data.startswith(b"\x89PNG"):
        return data.decode()
    i = 8
    while i < len(data):
        size, kind = struct.unpack(">I4s", data[i:i + 8])
        body = data[i + 8:i + 8 + size]
        if kind == b"zTXt" and body.startswith(b"mxGraphModel\0"):
            return unquote(zlib.decompress(body[len(b"mxGraphModel\0") + 1:], -15).decode())
        i += 12 + size
    sys.exit(f"{path}: no embedded diagram (export with --embed-diagram)")


def models(xml_text):
    """Yield (page name, mxGraphModel) for each page, inflating compressed pages."""
    for d in ET.fromstring(xml_text).iter("diagram"):
        m = d.find("mxGraphModel")
        if m is None and (d.text or "").strip():
            raw = zlib.decompress(base64.b64decode(d.text), -15).decode()
            m = ET.fromstring(unquote(raw))
        if m is not None:
            yield d.get("name", "?"), m


def lint_model(model):
    errs = []
    cells = list(model.iter("mxCell"))
    by_id = {}
    for c in cells:
        cid = c.get("id")
        if cid in by_id:
            errs.append(f"{cid}: duplicate id")
        by_id[cid] = c
    for c in cells:
        cid = c.get("id")
        for attr in ("parent", "source", "target"):
            ref = c.get(attr)
            if ref and ref not in by_id:
                errs.append(f"{cid}: {attr}={ref} does not exist")
        for name in NAME_RE.findall(c.get("style", "")):
            if name not in SHAPES:
                errs.append(f"{cid}: unknown shape name {name} (grep shapes.txt)")

    def is_vertex(c):
        p = by_id.get(c.get("parent"))
        # edge labels are vertices parented to an edge, with relative geometry
        return c.get("vertex") == "1" and c.find("mxGeometry") is not None and not (p is not None and p.get("edge") == "1")

    def box(c):
        """Absolute x, y, w, h (child geometry is relative to a vertex parent)."""
        g = c.find("mxGeometry")
        x, y = float(g.get("x", 0)), float(g.get("y", 0))
        p = by_id.get(c.get("parent"))
        if p is not None and is_vertex(p):
            px, py, _, _ = box(p)
            x, y = x + px, y + py
        return x, y, float(g.get("width", 0)), float(g.get("height", 0))

    def ancestors(c):
        seen = []
        while (c := by_id.get(c.get("parent"))) is not None:
            seen.append(c.get("id"))
        return seen

    verts = [c for c in cells if is_vertex(c)]
    containers = [c for c in verts if "container=1" in c.get("style", "")]
    icons = [c for c in verts if c not in containers and "edgeLabel" not in c.get("style", "") and "text;" not in c.get("style", "")]

    def footprint(c):
        x, y, w, h = box(c)
        label = re.split(r"\n|<br\s*/?>", c.get("value") or "")
        if "verticalLabelPosition=bottom" in c.get("style", "") and any(label):
            lw = max(w, max(len(s) for s in label) * CHAR_W)
            return x + w / 2 - lw / 2, y, lw, h + 4 + LINE_H * len(label)
        return x, y, w, h

    def inside(a, b):
        return b[0] <= a[0] and b[1] <= a[1] and a[0] + a[2] <= b[0] + b[2] and a[1] + a[3] <= b[1] + b[3]

    fps = [(c.get("id"), footprint(c)) for c in icons]
    for i, (a, (ax, ay, aw, ah)) in enumerate(fps):
        for b, (bx, by, bw, bh) in fps[i + 1:]:
            if ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah:
                errs.append(f"{a}: icon or label overlaps {b}")

    # ponytail: O(n*m) scan, fine for diagrams with a few hundred cells
    for c in icons + containers:
        b = box(c)
        holders = [k for k in containers if k is not c and inside(b, box(k))]
        if holders:
            tightest = min(holders, key=lambda k: box(k)[2] * box(k)[3])
            if tightest.get("id") not in ancestors(c):
                errs.append(f"{c.get('id')}: sits inside {tightest.get('id')} but parent is {c.get('parent')}")
    return errs


def lint_file(path):
    return [f"{path} [{page}] {e}" for page, m in models(read_drawio(path)) for e in lint_model(m)]


def selftest():
    icon = 'shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.{};verticalLabelPosition=bottom;'
    bad = f"""<mxfile><diagram name="t"><mxGraphModel><root>
      <mxCell id="0"/><mxCell id="1" parent="0"/>
      <mxCell id="vpc" style="container=1;" vertex="1" parent="1"><mxGeometry x="0" y="0" width="400" height="300" as="geometry"/></mxCell>
      <mxCell id="a" value="EC2" style="{icon.format('ec2')}" vertex="1" parent="vpc"><mxGeometry x="20" y="20" width="78" height="78" as="geometry"/></mxCell>
      <mxCell id="b" value="Queue" style="{icon.format('simple_queue_service')}" vertex="1" parent="1"><mxGeometry x="60" y="40" width="78" height="78" as="geometry"/></mxCell>
      <mxCell id="a" style="" vertex="1" parent="1"><mxGeometry x="900" y="900" width="10" height="10" as="geometry"/></mxCell>
      <mxCell id="e" edge="1" parent="1" source="a" target="ghost"><mxGeometry relative="1" as="geometry"/></mxCell>
    </root></mxGraphModel></diagram></mxfile>"""
    errs = " | ".join(e for _, m in models(bad) for e in lint_model(m))
    for want in ("duplicate id", "target=ghost", "simple_queue_service", "overlaps", "sits inside vpc but parent is 1"):
        assert want in errs, f"missing '{want}' in: {errs}"
    print("selftest ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
        sys.exit(0)
    if sys.argv[1:2] == ["--xml"] and len(sys.argv) == 3:
        print(read_drawio(sys.argv[2]))
        sys.exit(0)
    if not sys.argv[1:]:
        sys.exit(__doc__)
    errors = [e for f in sys.argv[1:] for e in lint_file(f)]
    print("\n".join(errors) or "ok")
    sys.exit(1 if errors else 0)
