"""Create temporary white-background contact sheets for visual asset inspection."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import sys

from PIL import Image, ImageDraw


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chapter", choices=["rational", "expr"])
    parser.add_argument("question_numbers", type=int, nargs="+")
    parser.add_argument("--answers-only", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    questions = json.loads((root / f"data/interim/{args.chapter}_all.json").read_text(encoding="utf-8"))
    entries = json.loads((root / f"data/web/{args.chapter}/asset_manifest.json").read_text(encoding="utf-8"))["assets"]
    manifest = {entry["source_path"]: entry for entry in entries}
    previews = []
    for question in questions:
        if int(question["original_qid"].split(".")[-1]) not in args.question_numbers:
            continue
        for asset in question["assets"]:
            if args.answers_only and asset["path"] not in question["reference_answer_raw"]:
                continue
            entry = manifest[asset["path"]]
            if Path(entry["web_path"]).suffix.lower() == ".svg":
                print(f"SVG requires browser inspection: {entry['web_path']}", file=sys.stderr)
                continue
            with Image.open(root / "data/web" / entry["web_path"]) as source:
                rgba = source.convert("RGBA")
                background = Image.new("RGBA", rgba.size, "white")
                background.alpha_composite(rgba)
                # Crop white margins; the source files are never modified.
                from PIL import ImageChops
                content = ImageChops.difference(background.convert("RGB"), Image.new("RGB", rgba.size, "white")).getbbox()
                tile = background.convert("RGB").crop(content) if content else background.convert("RGB")
                tile.thumbnail((470, 240))
                previews.append((asset["path"].split("/")[-1].replace("q.math.g6.", ""), tile.copy()))
    directory = args.output_dir or Path(tempfile.gettempdir()) / "math-question-pool-inspection"
    directory.mkdir(parents=True, exist_ok=True)
    for offset in range(0, len(previews), 18):
        group = previews[offset:offset + 18]
        sheet = Image.new("RGB", (1500, ((len(group) + 2) // 3) * 285), "white")
        draw = ImageDraw.Draw(sheet)
        for index, (label, tile) in enumerate(group):
            x, y = (index % 3) * 500, (index // 3) * 285
            draw.text((x + 8, y + 5), label, fill="black")
            sheet.paste(tile, (x + 8, y + 32))
        path = directory / f"{args.chapter}-{'-'.join(map(str, args.question_numbers))}-{offset // 18}.png"
        sheet.save(path)
        print(path)


if __name__ == "__main__":
    main()
