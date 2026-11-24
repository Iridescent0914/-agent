"""Export existing tasks, annotate locally, report offline. Never call providers."""
import argparse
import json
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.services.stage6_evaluation import evaluate


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _write(path, value):
    # Refuse to overwrite a hand-annotated file or a previous frozen export.
    with path.open("x", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="只读导出已有任务和待标注清单")
    export.add_argument("--paper-instance-id", type=UUID, action="append", required=True)
    export.add_argument("--output-dir", type=Path, required=True)
    export.add_argument("--dataset-version", default="stage6-development-v1")
    report = commands.add_parser("report", help="纯离线核对，不连接数据库")
    report.add_argument("--evidence", required=True)
    report.add_argument("--labels", required=True)
    report.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            # Database imports remain inside export: report works without a DB.
            from app.core.database import SessionLocal
            from app.services.stage6_evaluation_export import export_evidence, label_template
            with SessionLocal() as db:
                db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
                evidence = export_evidence(db, args.paper_instance_id)
                db.rollback()
            labels = label_template(evidence, args.dataset_version)
            args.output_dir.mkdir(parents=True, exist_ok=False)
            _write(args.output_dir / "evidence.json", evidence)
            _write(args.output_dir / "labels.json", labels)
            print(json.dumps({"exported_tasks": len(evidence["samples"]), "output_dir": str(args.output_dir),
                              "labels": "pending", "provider_calls_started": 0}, ensure_ascii=False))
        else:
            value = evaluate(_read(args.evidence), _read(args.labels))
            _write(args.output, value)
            print(json.dumps({"counts": value["counts"], "acceptance_status": value["acceptance_status"],
                              "output": str(args.output)}, ensure_ascii=False))
        return 0
    except (ValueError, KeyError, TypeError, OSError, ValidationError, SQLAlchemyError) as exc:
        # Do not print Pydantic inputs or exception bodies containing answers.
        print(json.dumps({"error": type(exc).__name__, "message": "请检查路径、文件版本、标注字段及同源划分；已有输出不会覆盖"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
