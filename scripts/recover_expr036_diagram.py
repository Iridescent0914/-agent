"""Export expr036's original Word canvas as SVG, without inferring the diagram.

Only the rectangle/text/group vocabulary used by canvas 265 is supported.
The SVG is a source-derived asset; run convert_seed_assets after catalog repair.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
QID = "q.math.g6.expr.school.036"
ASSET = f"assets/expr/{QID}_canvas265.svg"
NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "wpc": "http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "wpg": "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup",
}
SVG = "http://www.w3.org/2000/svg"
EMU_PER_PIXEL = 9525


def px(value: float) -> str:
    # SVG/CSS engines clamp enormous EMU-sized font values before scaling.
    return f"{value/EMU_PER_PIXEL:.6f}"


def render_canvas(anchor: ET.Element) -> str:
    canvas = anchor.find(".//wpc:wpc", NS)
    extent = anchor.find("wp:extent", NS)
    if canvas is None or extent is None:
        raise ValueError("Expected a native Word canvas with extent")
    width, height = int(extent.attrib["cx"]), int(extent.attrib["cy"])
    ET.register_namespace("", SVG)
    svg = ET.Element(f"{{{SVG}}}svg", {
        "viewBox": f"{px(-25000)} {px(-25000)} {px(width+50000)} {px(height+50000)}",
        "width": "960", "height": str(round(960*(height+50000)/(width+50000))),
        "role": "img", "aria-labelledby": "title",
    })
    ET.SubElement(svg, f"{{{SVG}}}title", {"id": "title"}).text = "原题第1至第4个正方形的顶点数字规律图"
    ET.SubElement(svg, f"{{{SVG}}}rect", {
        "x": px(-25000), "y": px(-25000), "width": px(width+50000), "height": px(height+50000), "fill": "white",
    })

    def draw(parent: ET.Element, sx=1.0, sy=1.0, dx=0.0, dy=0.0):
        for shape in parent:
            if shape.tag == f"{{{NS['wpg']}}}wgp":
                xf = shape.find("wpg:grpSpPr/a:xfrm", NS)
                off, ext, child_off, child_ext = [xf.find(f"a:{key}", NS).attrib for key in ("off", "ext", "chOff", "chExt")]
                gx, gy = int(ext["cx"])/int(child_ext["cx"]), int(ext["cy"])/int(child_ext["cy"])
                draw(shape, sx*gx, sy*gy, dx+sx*(int(off["x"])-gx*int(child_off["x"])), dy+sy*(int(off["y"])-gy*int(child_off["y"])))
            elif shape.tag == f"{{{NS['wps']}}}wsp":
                props = shape.find("wps:spPr", NS)
                xf = props.find("a:xfrm", NS)
                if xf.attrib:
                    raise ValueError("Unsupported rotated/flipped shape")
                off, ext = [xf.find(f"a:{key}", NS).attrib for key in ("off", "ext")]
                x, y = dx+sx*int(off["x"]), dy+sy*int(off["y"])
                w, h = sx*int(ext["cx"]), sy*int(ext["cy"])
                geometry = props.find("a:prstGeom", NS)
                if geometry is None or geometry.get("prst") != "rect" or props.find("a:noFill", NS) is None:
                    raise ValueError("Unsupported shape geometry/fill")
                texts = shape.findall(".//w:t", NS)
                if texts:
                    # Font sizes in Word are half-points, independent of group coordinates.
                    size = shape.find(".//w:sz", NS)
                    if size is None:
                        size = shape.find(".//w:szCs", NS)
                    half_points = int(size.get(f"{{{NS['w']}}}val")) if size is not None else 21
                    font_size = half_points*6350
                    ET.SubElement(svg, f"{{{SVG}}}text", {
                        "x": px(x+w/2), "y": px(y+font_size),
                        "font-size": px(font_size), "text-anchor": "middle", "fill": "black",
                        "font-family": "'Times New Roman','Noto Serif CJK SC','SimSun',serif",
                    }).text = "".join(text.text or "" for text in texts)
                else:
                    line = props.find("a:ln", NS)
                    color = line.find("a:solidFill/a:srgbClr", NS)
                    if color is None or line.find("a:prstDash", NS).get("val") != "solid":
                        raise ValueError("Unsupported line style")
                    ET.SubElement(svg, f"{{{SVG}}}rect", {
                        "x": px(x), "y": px(y), "width": px(w), "height": px(h),
                        "fill": "none", "stroke": "#"+color.get("val"), "stroke-width": px(int(line.get("w"))),
                    })
            elif shape.tag.split("}")[-1] not in {"bg", "whole", "cNvPr", "cNvGrpSpPr", "grpSpPr"}:
                raise ValueError("Unsupported canvas child: " + shape.tag)

    draw(canvas)
    return ET.tostring(svg, encoding="unicode") + "\n"


def main():
    record = next(row for row in json.loads((ROOT/"data/interim/expr_all.json").read_text(encoding="utf-8")) if row["original_qid"] == QID)
    source = ROOT/record["source_file"]
    with ZipFile(source) as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
    anchors = [anchor for anchor in document.findall(".//wp:anchor", NS)
               if anchor.find("wp:docPr", NS).get("id") == "265" and anchor.find(".//wpc:wpc", NS) is not None]
    if len(anchors) != 1:
        raise ValueError("Source canvas 265 must be unique")
    svg = render_canvas(anchors[0])
    target = ROOT/"data"/ASSET
    target.write_text(svg, encoding="utf-8", newline="\n")
    provenance = {
        "original_qid": QID, "source_file": record["source_file"],
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "source_canvas_id": "265", "asset": ASSET,
        "asset_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "method": "Word DrawingML rectangle/text/group coordinates to SVG; AlternateContent fallback excluded",
    }
    (ROOT/"data/audit/expr036_diagram_provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2)+"\n", encoding="utf-8", newline="\n")
    print(ASSET)


if __name__ == "__main__":
    main()
