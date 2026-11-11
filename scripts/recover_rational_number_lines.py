"""Recover two number-line canvases directly from the original Word DrawingML."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "wpc": "http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
}
SVG = "http://www.w3.org/2000/svg"
EMU_PER_PIXEL = 9525
TARGETS = {
    "q.math.g6.rational.school.010": ("49", "-6 -5 -4 -3 -2 -1 0 1 2 3 4 5 6", 14),
    "q.math.g6.rational.school.023": ("83", "c a 0 b", 5),
}


def px(value: int | float) -> str:
    return f"{value / EMU_PER_PIXEL:.4f}"


def render_canvas(anchor: ET.Element, expected_labels: str, expected_lines: int) -> str:
    canvas = anchor.find(".//wpc:wpc", NS)
    extent = anchor.find("wp:extent", NS)
    if canvas is None or extent is None:
        raise ValueError("Expected Word canvas and extent")
    width, height = int(extent.get("cx")), int(extent.get("cy"))
    pad = 50000
    ET.register_namespace("", SVG)
    svg = ET.Element(f"{{{SVG}}}svg", {
        "viewBox": f"{px(-pad)} {px(-pad)} {px(width + 2 * pad)} {px(height + 2 * pad)}",
        "width": "960", "height": str(round(960 * (height + 2 * pad) / (width + 2 * pad))),
        "role": "img", "aria-label": "原题 Word 数轴图",
    })
    ET.SubElement(svg, f"{{{SVG}}}rect", {
        "x": px(-pad), "y": px(-pad), "width": px(width + 2 * pad),
        "height": px(height + 2 * pad), "fill": "white",
    })
    defs = ET.SubElement(svg, f"{{{SVG}}}defs")
    marker = ET.SubElement(defs, f"{{{SVG}}}marker", {
        "id": "arrow", "markerWidth": "9", "markerHeight": "9", "refX": "8",
        "refY": "4.5", "orient": "auto", "markerUnits": "strokeWidth",
    })
    ET.SubElement(marker, f"{{{SVG}}}path", {"d": "M0,0 L9,4.5 L0,9 Z", "fill": "black"})
    labels = []
    for shape in canvas.findall("wps:wsp", NS):
        props = shape.find("wps:spPr", NS)
        transform = props.find("a:xfrm", NS)
        if transform is None or transform.attrib:
            raise ValueError("Unsupported rotated or flipped shape")
        offset, size = transform.find("a:off", NS), transform.find("a:ext", NS)
        x, y = int(offset.get("x")), int(offset.get("y"))
        w, h = int(size.get("cx")), int(size.get("cy"))
        geometry = props.find("a:prstGeom", NS)
        label = "".join(node.text or "" for node in shape.findall(".//w:t", NS))
        if label:
            if geometry is None or geometry.get("prst") != "rect":
                raise ValueError("Unsupported text-box geometry")
            labels.append((x, label.replace("−", "-").replace("－", "-")))
            font = shape.find(".//w:sz", NS)
            if font is None:
                font = shape.find(".//w:szCs", NS)
            half_points = int(font.get(f"{{{NS['w']}}}val")) if font is not None else 21
            font_size = half_points * 6350
            ET.SubElement(svg, f"{{{SVG}}}text", {
                "x": px(x + w / 2), "y": px(y + font_size),
                "font-size": px(font_size), "text-anchor": "middle",
                "font-family": "'Times New Roman','Noto Serif CJK SC','SimSun',serif",
                "fill": "black",
            }).text = label
        elif geometry is not None and geometry.get("prst") == "line":
            line = props.find("a:ln", NS)
            if line is None:
                raise ValueError("Line has no stroke")
            attributes = {
                "x1": px(x), "y1": px(y), "x2": px(x + w), "y2": px(y + h),
                "stroke": "black", "stroke-width": px(int(line.get("w", "9525"))),
            }
            if line.find("a:tailEnd", NS) is not None:
                attributes["marker-end"] = "url(#arrow)"
            ET.SubElement(svg, f"{{{SVG}}}line", attributes)
        else:
            raise ValueError("Unsupported non-text shape")
    actual_labels = " ".join(label for _, label in sorted(labels))
    if actual_labels != expected_labels:
        raise ValueError(f"Unexpected source labels: {actual_labels!r}")
    if len(svg.findall(f"{{{SVG}}}line")) != expected_lines:
        raise ValueError("Unexpected source line count")
    return ET.tostring(svg, encoding="unicode") + "\n"


def main() -> None:
    records = {row["original_qid"]: row for row in json.loads((ROOT / "data/interim/rational_all.json").read_text(encoding="utf-8"))}
    provenance = []
    for qid, (canvas_id, expected_labels, expected_lines) in TARGETS.items():
        source = ROOT / records[qid]["source_file"]
        with ZipFile(source) as archive:
            document = ET.fromstring(archive.read("word/document.xml"))
        anchors = [node for node in document.findall(".//wp:anchor", NS)
                   if node.find("wp:docPr", NS) is not None
                   and node.find("wp:docPr", NS).get("id") == canvas_id
                   and node.find(".//wpc:wpc", NS) is not None]
        if len(anchors) != 1:
            raise ValueError(f"Source canvas {canvas_id} must be unique")
        asset = f"assets/rational/{qid}_canvas{canvas_id}.svg"
        target = ROOT / "data" / asset
        target.write_text(render_canvas(anchors[0], expected_labels, expected_lines), encoding="utf-8", newline="\n")
        provenance.append({
            "original_qid": qid, "source_file": records[qid]["source_file"],
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "source_canvas_id": canvas_id, "asset": asset,
            "asset_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "method": "Word DrawingML line and text coordinates to SVG",
        })
        print(asset)
    (ROOT / "data/audit/rational_number_lines_provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
