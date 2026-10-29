from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
import unicodedata

from app.core.config import get_settings
from app.services.recognition_workflow import normalize_image
from app.services.vision_service import PROMPT_VERSION, get_vision_service, parse_vision_output


def _normal(value: object) -> str:
    text = unicodedata.normalize("NFKC", "" if value is None else str(value))
    return "".join(text.replace("−", "-").replace("—", "-").split()).upper()


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate a real vision provider")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--require-thresholds",
        action="store_true",
        help="fail unless the manifest defines and passes acceptance_thresholds",
    )
    args = parser.parse_args()
    manifest_path = Path(args.manifest).resolve()
    config = json.loads(manifest_path.read_text(encoding="utf-8"))
    settings = get_settings()
    if not settings.vision_enabled or settings.vision_provider.strip().lower() == "fake":
        print(json.dumps({
            "error": "real_vision_provider_required",
            "message": "真实图片评估禁止使用 disabled/fake 视觉适配器",
        }, ensure_ascii=False, indent=2))
        return 2
    samples_config = config.get("samples", [])
    if not isinstance(samples_config, list) or not samples_config:
        print(json.dumps({
            "error": "evaluation_samples_required",
            "message": "评估清单至少需要一个真实、去身份化图片样本",
        }, ensure_ascii=False, indent=2))
        return 2
    thresholds = config.get("acceptance_thresholds")
    if args.require_thresholds and not isinstance(thresholds, dict):
        print(json.dumps({
            "error": "acceptance_thresholds_required",
            "message": "严格评估要求 manifest 定义 acceptance_thresholds",
        }, ensure_ascii=False, indent=2))
        return 2
    service = get_vision_service()
    total = correct = missed = changed = failures = 0
    elapsed: list[float] = []
    review_seconds: list[float] = []
    samples = []
    for sample in samples_config:
        if sample.get("teacher_review_seconds") is not None:
            review_seconds.append(float(sample["teacher_review_seconds"]))
        image_path = (manifest_path.parent / sample["image_path"]).resolve()
        image, content_type, _, _ = normalize_image(image_path.read_bytes())
        expected_items = sample["expected_items"]
        started = time.perf_counter()
        try:
            payload = service.extract_answers(
                image_bytes=image,
                content_type=content_type,
                expected_items=expected_items,
            )
            values = parse_vision_output(payload, expected_items)
            duration = time.perf_counter() - started
            elapsed.append(duration)
            row_results = []
            for truth in sample["ground_truth"]:
                total += 1
                actual = values[truth["order_no"]]
                is_correct = (
                    actual["recognition_status"] == truth["recognition_status"]
                    and _normal(actual["answer"]) == _normal(truth.get("answer"))
                )
                correct += int(is_correct)
                missed += int(
                    truth["recognition_status"] == "readable"
                    and actual["recognition_status"] in {"missing", "illegible"}
                )
                changed += int(not is_correct)
                row_results.append({"truth": truth, "actual": actual, "correct": is_correct})
            samples.append({"sample_id": sample["sample_id"], "seconds": duration, "items": row_results})
        except Exception as exc:
            failures += 1
            failed_truth = sample.get("ground_truth", [])
            total += len(failed_truth)
            changed += len(failed_truth)
            missed += sum(
                truth.get("recognition_status") == "readable"
                for truth in failed_truth
            )
            samples.append({"sample_id": sample["sample_id"], "error": type(exc).__name__})
    exact_accuracy = correct / total if total else None
    miss_rate = missed / total if total else None
    modification_rate = changed / total if total else None
    acceptance_errors: list[str] = []
    if isinstance(thresholds, dict):
        checks = (
            ("min_exact_transcription_accuracy", exact_accuracy, "min"),
            ("max_miss_rate", miss_rate, "max"),
            ("max_modification_rate", modification_rate, "max"),
            ("max_provider_failures", failures, "max"),
        )
        for name, actual, direction in checks:
            if name not in thresholds or actual is None:
                continue
            expected = float(thresholds[name])
            failed = actual < expected if direction == "min" else actual > expected
            if failed:
                acceptance_errors.append(f"{name}: actual={actual}, threshold={expected}")
    output = {
        "schema_version": "m6-evaluation-v1",
        "provider": settings.vision_provider,
        "model": settings.vision_model,
        "prompt_version": PROMPT_VERSION,
        "sample_count": len(samples_config),
        "answer_slot_count": total,
        "exact_transcription_accuracy": exact_accuracy,
        "miss_rate": miss_rate,
        "estimated_modification_rate": modification_rate,
        "provider_failure_count": failures,
        "median_provider_latency_seconds": sorted(elapsed)[len(elapsed) // 2] if elapsed else None,
        "average_teacher_review_seconds": (
            sum(review_seconds) / len(review_seconds) if review_seconds else None
        ),
        "acceptance_thresholds": thresholds,
        "acceptance_passed": None if not isinstance(thresholds, dict) else not acceptance_errors,
        "acceptance_errors": acceptance_errors,
        "samples": samples,
    }
    Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in output.items() if key != "samples"}, ensure_ascii=False, indent=2))
    return 1 if failures or acceptance_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
