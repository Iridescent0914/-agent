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
    parser = argparse.ArgumentParser(description="Evaluate a real M6 vision provider")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    manifest_path = Path(args.manifest).resolve()
    config = json.loads(manifest_path.read_text(encoding="utf-8"))
    settings = get_settings()
    service = get_vision_service()
    total = correct = missed = changed = failures = 0
    elapsed: list[float] = []
    review_seconds: list[float] = []
    samples = []
    for sample in config.get("samples", []):
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
    output = {
        "schema_version": "m6-evaluation-v1",
        "provider": settings.vision_provider,
        "model": settings.vision_model,
        "prompt_version": PROMPT_VERSION,
        "sample_count": len(config.get("samples", [])),
        "answer_slot_count": total,
        "exact_transcription_accuracy": correct / total if total else None,
        "miss_rate": missed / total if total else None,
        "estimated_modification_rate": changed / total if total else None,
        "provider_failure_count": failures,
        "median_provider_latency_seconds": sorted(elapsed)[len(elapsed) // 2] if elapsed else None,
        "average_teacher_review_seconds": (
            sum(review_seconds) / len(review_seconds) if review_seconds else None
        ),
        "samples": samples,
    }
    Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in output.items() if key != "samples"}, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
