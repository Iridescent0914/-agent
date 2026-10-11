from __future__ import annotations

from app.core.database import SessionLocal
from app.services.assets import cleanup_orphan_teacher_assets
from app.services.recognition_workflow import purge_expired_recognition_images


def run_file_maintenance() -> dict[str, int]:
    """Run idempotent retention work in its own short database transaction."""
    with SessionLocal() as db:
        recognition_images = purge_expired_recognition_images(db)
        teacher_assets = cleanup_orphan_teacher_assets(db)
        db.commit()
    return {
        "recognition_images": recognition_images,
        "teacher_assets": teacher_assets,
    }
