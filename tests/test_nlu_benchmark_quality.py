import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "tests" / "nlu_benchmark"


def rows(name):
    return [json.loads(line) for line in (BENCH / name).read_text(encoding="utf-8").splitlines() if line]


def test_required_dataset_scale_and_schema():
    assert len(rows("seed_cases.jsonl")) >= 600
    assert len(rows("generated_cases.jsonl")) >= 2400
    assert len(rows("adversarial_cases.jsonl")) >= 300
    assert len(rows("multi_turn_cases.jsonl")) >= 400
    required = set(json.loads((BENCH / "schemas/case_schema.json").read_text(encoding="utf-8"))["required"])
    for item in rows("test.jsonl"):
        assert required.issubset(item)


def test_semantic_families_do_not_cross_splits():
    owners = {}
    for split in ("train", "dev", "test"):
        for item in rows(f"{split}.jsonl"):
            family = item["semantic_family_id"]
            assert owners.setdefault(family, split) == split


def test_final_test_hash_is_frozen():
    expected = (BENCH / "final_test.sha256").read_text(encoding="ascii").split()[0]
    assert hashlib.sha256((BENCH / "test.jsonl").read_bytes()).hexdigest() == expected


def test_dataset_contains_no_private_project_data():
    content = "\n".join((BENCH / name).read_text(encoding="utf-8") for name in (
        "seed_cases.jsonl", "generated_cases.jsonl", "adversarial_cases.jsonl",
        "multi_turn_cases.jsonl", "train.jsonl", "dev.jsonl", "test.jsonl",
    ))
    assert "api_key" not in content.lower()
    assert "memory.json" not in content
    assert "data/private" not in content
