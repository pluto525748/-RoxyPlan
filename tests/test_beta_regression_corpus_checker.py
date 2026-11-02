"""Check that source drift cannot silently weaken frozen corpus protection."""

import hashlib
import json

import pytest

from scripts import check_beta_regression_corpus as checker


def setup_index(tmp_path, monkeypatch, *, frozen=True, data=b"", original=b"original",
                integration=None, suffix=".txt"):
    source = tmp_path / ("source" + suffix)
    source.write_bytes(original)
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_bytes(data)
    index = tmp_path / "index.json"
    inventory_entry = {"path": source.name, "frozen": frozen,
                       "sha256": hashlib.sha256(original).hexdigest()}
    if integration is not None:
        inventory_entry["integration_sha256"] = hashlib.sha256(integration).hexdigest()
    index.write_text(json.dumps({
        "source_inventory": [inventory_entry],
        "corpus": {"path": "corpus.jsonl", "sha256": hashlib.sha256(data).hexdigest(),
                   "counts": {"single_turn": 0, "multi_turn": 0}},
    }), encoding="utf-8")
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    monkeypatch.setattr(checker, "CORPUS", corpus)
    monkeypatch.setattr(checker, "INDEX", index)
    return source, corpus


def test_changed_frozen_source_is_an_error(tmp_path, monkeypatch):
    source, _ = setup_index(tmp_path, monkeypatch)
    source.write_bytes(b"changed")
    report = checker.Report()
    checker.check_index(report, [])
    assert not report.ok()
    assert any("来源快照已变化" in error for error in report.errors)


def test_changed_mutable_source_is_visible_as_warning(tmp_path, monkeypatch):
    source, _ = setup_index(tmp_path, monkeypatch, frozen=False)
    source.write_bytes(b"changed")
    report = checker.Report()
    checker.check_index(report, [])
    assert report.ok()
    assert len(report.warnings) == 1
    assert "逐项核对当前引用与预期" in report.warnings[0]


@pytest.mark.parametrize("changed, passes", [(b"\r\n", True), (b"changed\n", False)])
def test_corpus_line_endings_are_distinct_from_content_change(tmp_path, monkeypatch, changed, passes):
    _, corpus = setup_index(tmp_path, monkeypatch, data=b"\n")
    corpus.write_bytes(changed)
    report = checker.Report()
    checker.check_index(report, [])
    assert report.ok() is passes


@pytest.mark.parametrize("frozen", [True, False])
@pytest.mark.parametrize("current", [b"frozen\nbody\n", b"frozen\r\nbody\r\n"])
def test_text_source_keeps_snapshot_and_accepts_only_crlf_equivalence(tmp_path, monkeypatch, frozen, current):
    original = b"frozen\nbody\n"
    source, _ = setup_index(tmp_path, monkeypatch, frozen=frozen, original=original)
    index_before = checker.INDEX.read_bytes()
    source.write_bytes(current)
    report = checker.Report()
    checker.check_index(report, [])
    assert report.ok() and report.warnings == []
    assert source.read_bytes() == current
    assert checker.INDEX.read_bytes() == index_before
    entry = json.loads(index_before)["source_inventory"][0]
    assert entry["frozen"] is frozen and entry["sha256"] == hashlib.sha256(original).hexdigest()


@pytest.mark.parametrize("changed", [
    b"frozen\nbodX\n",                  # same line endings, one changed character
    b"frozen\r\nbodX\r\n",            # line-ending conversion plus real drift
    b"frozen\r\nbody \r\n",           # spaces must not be normalized away
    b"\xef\xbb\xbffrozen\r\nbody\r\n", # adding a UTF-8 BOM is a content change
    b"frozen\rbody\r",                  # lone CR is not CRLF
])
def test_frozen_text_content_drift_still_fails(tmp_path, monkeypatch, changed):
    source, _ = setup_index(tmp_path, monkeypatch, original=b"frozen\nbody\n")
    index_before = checker.INDEX.read_bytes()
    source.write_bytes(changed)
    report = checker.Report()
    checker.check_index(report, [])
    assert not report.ok() and report.warnings == []
    assert any("来源快照已变化" in error for error in report.errors)
    assert checker.INDEX.read_bytes() == index_before and source.read_bytes() == changed


def test_integration_hash_remains_authoritative_and_unmodified(tmp_path, monkeypatch):
    original, reviewed = b"contributor\nold\n", b"reviewed\nbody\n"
    source, _ = setup_index(tmp_path, monkeypatch, original=original, integration=reviewed)
    index_before = checker.INDEX.read_bytes()
    source.write_bytes(reviewed.replace(b"\n", b"\r\n"))
    report = checker.Report()
    checker.check_index(report, [])
    assert report.ok() and report.warnings == []
    # Matching the old contributor hash cannot bypass an integration override.
    source.write_bytes(original)
    drift = checker.Report()
    checker.check_index(drift, [])
    assert not drift.ok() and any("来源快照已变化" in error for error in drift.errors)
    assert checker.INDEX.read_bytes() == index_before
    entry = json.loads(index_before)["source_inventory"][0]
    assert entry["sha256"] == hashlib.sha256(original).hexdigest()
    assert entry["integration_sha256"] == hashlib.sha256(reviewed).hexdigest()
    assert entry["frozen"] is True


@pytest.mark.parametrize("original,suffix", [
    (b"opaque\nbody\n", ".bin"),
    (b"opaque\xff\nbody\n", ".txt"),
    (b"opaque\x00\nbody\n", ".txt"),
])
def test_non_text_sources_keep_raw_byte_comparison(tmp_path, monkeypatch, original, suffix):
    source, _ = setup_index(tmp_path, monkeypatch, original=original, suffix=suffix)
    source.write_bytes(original.replace(b"\n", b"\r\n"))
    report = checker.Report()
    checker.check_index(report, [])
    assert not report.ok() and any("来源快照已变化" in error for error in report.errors)
