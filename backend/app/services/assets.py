from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import get_settings


@lru_cache(maxsize=1)
def _manifest() -> dict[str, dict[str, Any]]:
    path = get_settings().web_asset_dir / "asset_manifest.json"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    entries = raw.get("assets", raw) if isinstance(raw, dict) else raw
    return {item["source_path"]: item for item in entries}


def resolve_assets(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    settings = get_settings()
    manifest = _manifest()
    result: list[dict[str, Any]] = []
    for asset in assets:
        source_path = asset.get("path", "")
        entry = manifest.get(source_path)
        web_url = None
        status = "missing"
        if entry is not None:
            status = entry.get("status", "missing")
            web_path = entry.get("web_path")
            if status == "converted" and web_path:
                web_url = f"{settings.public_asset_base_url.rstrip('/')}/{Path(web_path).name}"
        result.append(
            {"path": source_path, "web_url": web_url, "conversion_status": status}
        )
    return result
