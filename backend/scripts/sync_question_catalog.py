"""Preview/apply shared question reviews and source-pool corrections, not business data."""
import argparse
import json

from app.core.database import SessionLocal
from app.services.question_catalog_sync import synchronize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="无冲突时备份并提交；默认只读预览")
    parser.add_argument("--accept-shared-review", action="append", default=[], metavar="ORIGINAL_QID",
                        help="仅对明确列出的题号接受共享版本覆盖本机人工审核；可重复指定")
    parser.add_argument("--no-backup", action="store_true", help="不生成单独备份文件")
    parser.add_argument("--replace-local-review-history", action="store_true",
                        help="应用共享版本时丢弃旧本机审核历史，仅保留防止种子回退的同步标记")
    args = parser.parse_args()
    with SessionLocal() as db:
        report = synchronize(db, apply=args.apply, backup=not args.no_backup,
                             accepted_review_overrides=frozenset(args.accept_shared_review),
                             retain_local_review_history=not args.replace_local_review_history)
        if report["applied"]:
            db.commit()
        else:
            db.rollback()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["conflicts"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
