from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "tests" / "nlu_benchmark" / "reports"
RUNNER = ROOT / "scripts" / "run_chinese_nlu_benchmark.py"


def run(split: str, report_path: Path, *, frozen=False) -> dict:
    command = [sys.executable, str(RUNNER), "--split", split, "--report", str(report_path)]
    if frozen:
        command.append("--frozen-test")
    completed = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def thresholds_met(report: dict) -> bool:
    metrics = report["metrics"]
    return (
        metrics["capability"] >= 0.95
        and metrics["request_mode"] >= 0.97
        and metrics["negative_safety"] == 1.0
        and metrics["false_write"] == 1.0
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=1)
    args = parser.parse_args()
    REPORTS.mkdir(parents=True, exist_ok=True)
    last_dev = None
    for iteration in range(1, min(max(args.iterations, 1), 10) + 1):
        train = run("train", REPORTS / f"iteration_{iteration:02d}_train.json")
        dev = run("dev", REPORTS / f"iteration_{iteration:02d}_dev.json")
        last_dev = dev
        summary = {
            "iteration": iteration,
            "train_metrics": train["metrics"],
            "dev_metrics": dev["metrics"],
            "top_error_categories": sorted(
                dev["error_categories"].items(), key=lambda item: item[1], reverse=True
            )[:5],
            "improvement_policy": "fix architecture root causes; never edit labels to raise metrics",
        }
        (REPORTS / f"iteration_{iteration:02d}.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False))
        if thresholds_met(dev):
            final = run("test", REPORTS / "final_test_report.json", frozen=True)
            print(json.dumps({"final_test_metrics": final["metrics"]}, ensure_ascii=False))
            return 0 if thresholds_met(final) else 2
    print(json.dumps({"status": "threshold_not_met", "last_dev": last_dev["metrics"]}, ensure_ascii=False))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
