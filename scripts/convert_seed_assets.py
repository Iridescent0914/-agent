from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CODE_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = CODE_ROOT / "data"
# Convert the complete catalog so pending questions are still reviewable.
# Automatic assignment eligibility is controlled separately by grading_readiness.
QUESTIONS_PATH = DATA_DIR / "interim" / "rational_all.json"
OUTPUT_DIR = DATA_DIR / "web" / "rational"
MANIFEST_PATH = OUTPUT_DIR / "asset_manifest.json"
POWERSHELL_CONVERTER = CODE_ROOT / "scripts" / "convert_wmf_to_png.ps1"
ASSET_PATTERN = re.compile(r"\[ASSET:([^\]]+)\]")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_previous_manifest() -> dict[str, dict[str, Any]]:
    if not MANIFEST_PATH.exists():
        return {}
    with MANIFEST_PATH.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    return {item["source_path"]: item for item in raw.get("assets", [])}


def collect_references(questions: list[dict[str, Any]]) -> list[str]:
    references: list[str] = []
    for question in questions:
        references.extend(ASSET_PATTERN.findall(question.get("question_text", "")))
        references.extend(
            asset["path"]
            for asset in question.get("assets", [])
            if isinstance(asset, dict) and asset.get("path")
        )
    return references


def convert_with_magick(source: Path, destination: Path) -> tuple[bool, str]:
    executable = shutil.which("magick")
    if not executable:
        return False, "ImageMagick not available"
    process = subprocess.run(
        [executable, "-density", "192", str(source), str(destination)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if process.returncode == 0 and destination.exists() and destination.stat().st_size > 0:
        return True, "magick"
    return False, (process.stderr or process.stdout or "ImageMagick conversion failed").strip()


def convert_with_powershell(source: Path, destination: Path) -> tuple[bool, str]:
    if os.name != "nt":
        return False, "Windows GDI+ converter is only available on Windows"
    executable = shutil.which("powershell") or shutil.which("powershell.exe")
    if not executable:
        return False, "PowerShell not available"
    process = subprocess.run(
        [
            executable,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(POWERSHELL_CONVERTER),
            "-Source",
            str(source),
            "-Destination",
            str(destination),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if process.returncode == 0 and destination.exists() and destination.stat().st_size > 0:
        return True, "windows_gdi_plus"
    return False, (process.stderr or process.stdout or "PowerShell conversion failed").strip()


def convert_asset(source: Path, destination: Path) -> tuple[bool, str]:
    suffix = source.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}:
        shutil.copy2(source, destination.with_suffix(suffix))
        return True, "copy"
    if suffix != ".wmf":
        return False, f"unsupported asset type: {suffix}"
    ok, detail = convert_with_magick(source, destination)
    if ok:
        return ok, detail
    return convert_with_powershell(source, destination)


def main() -> int:
    with QUESTIONS_PATH.open("r", encoding="utf-8") as handle:
        questions = json.load(handle)
    all_references = collect_references(questions)
    unresolved_references = [value for value in all_references if value == "unknown"]
    references = [value for value in all_references if value != "unknown"]
    unique_references = sorted(set(references))
    previous = load_previous_manifest()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    failures = 0

    for source_path in unique_references:
        source = DATA_DIR / Path(source_path)
        entry: dict[str, Any] = {"source_path": source_path}
        if not source.exists():
            entry.update(status="failed", error="source file does not exist")
            failures += 1
            entries.append(entry)
            continue

        source_hash = sha256(source)
        destination = OUTPUT_DIR / f"{source.stem}.png"
        old = previous.get(source_path, {})
        if (
            old.get("status") == "converted"
            and old.get("source_sha256") == source_hash
            and destination.exists()
            and destination.stat().st_size > 0
        ):
            entry.update(
                source_sha256=source_hash,
                web_path=f"rational/{destination.name}",
                format="png",
                status="converted",
                converter=old.get("converter", "cached"),
            )
            entries.append(entry)
            continue

        try:
            ok, detail = convert_asset(source, destination)
        except (OSError, subprocess.SubprocessError) as exc:
            ok, detail = False, str(exc)
        if ok:
            actual = destination
            if not actual.exists():
                copied = [path for path in OUTPUT_DIR.glob(f"{source.stem}.*") if path != source]
                actual = copied[0] if copied else destination
            entry.update(
                source_sha256=source_hash,
                web_path=f"rational/{actual.name}",
                format=actual.suffix.lstrip(".").lower(),
                status="converted",
                converter=detail,
            )
        else:
            entry.update(
                source_sha256=source_hash,
                status="failed",
                error=detail[:1000],
            )
            failures += 1
        entries.append(entry)

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "question_count": len(questions),
        "reference_count": len(references),
        "unique_asset_count": len(unique_references),
        "converted_count": len(entries) - failures,
        "failed_count": failures,
        "unresolved_reference_count": len(unresolved_references),
        "assets": entries,
    }
    with MANIFEST_PATH.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({key: value for key, value in manifest.items() if key != "assets"}, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
