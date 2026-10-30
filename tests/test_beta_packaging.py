"""Small synthetic package checks; no PyInstaller, Qt app or model calls."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("beta_release_check", ROOT / "scripts/check_beta_release.py")
beta = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(beta)


def test_reviewed_templates_allow_only_line_ending_conversion(tmp_path):
    path = tmp_path / "template.json"
    path.write_bytes(b'{"default": true}\r\n')
    expected = beta.sha256(path)
    path.write_bytes(b'{"default": true}\n')
    assert beta.matches_reviewed_template(path, expected)
    path.write_bytes(b'{"default": false}\n')
    assert not beta.matches_reviewed_template(path, expected)


def test_pe_architecture_is_independent_of_machine_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(beta.platform, "machine", lambda: "")
    path = tmp_path / "synthetic.exe"
    header = bytearray(134)
    header[:2] = b"MZ"
    header[60:64] = (128).to_bytes(4, "little")
    header[128:134] = b"PE\0\0\x64\x86"
    path.write_bytes(header)
    assert beta.interpreter_is_amd64(path)
    header[132:134] = b"\x4c\x01"
    path.write_bytes(header)
    assert not beta.interpreter_is_amd64(path)


def test_pem_parser_marker_is_not_a_private_key(tmp_path):
    path = tmp_path / "synthetic.dll"
    path.write_bytes(b"-----BEGIN RSA PRIVATE KEY-----\0")
    beta.scan_credentials(path)
    path.write_bytes(b"-----BEGIN RSA PRIVATE KEY-----\n" + b"A" * 64 + b"\n")
    with pytest.raises(beta.ReleaseError, match="Credential"):
        beta.scan_credentials(path)
    source = tmp_path / "synthetic.py"
    source.write_text("value = " + repr(path.read_text()) + "\n", encoding="utf-8")
    with pytest.raises(beta.ReleaseError, match="Credential literal"):
        beta.scan_credentials(source)


@pytest.fixture
def synthetic_bundle(tmp_path):
    bundle = tmp_path / "external/RoxyPlan"
    names = ["RoxyPlan.exe", "_internal/python310.dll", "_internal/_ssl.pyd",
             "_internal/libssl-3-x64.dll", "_internal/libcrypto-3-x64.dll",
             "_internal/shiboken6/Shiboken.pyd", "_internal/shiboken6/shiboken6.abi3.dll",
             *("_internal/PySide6/" + name + ".pyd" for name in ("QtCore", "QtGui", "QtWidgets")),
             *("_internal/PySide6/Qt6" + name + ".dll" for name in ("Core", "Gui", "Widgets")),
             *("_internal/" + name for name in beta.QT_PLUGINS)]
    for name in names:
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic binary -- never executed")
    for name in beta.RESOURCE_PATHS:
        path = bundle / "_internal" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        # Only reviewed public resources; never read actual settings/user stores.
        shutil.copyfile(ROOT / name, path)
    with zipfile.ZipFile(bundle / "_internal/base_library.zip", "w") as archive:
        archive.writestr("encodings/__init__.pyc", b"synthetic bytecode")
    rows = beta.inventory(bundle)
    return bundle, rows


def test_resource_policy_matches_references_and_complete_persona():
    rows = beta.resource_inventory(ROOT)
    assert len(rows) == 38
    assert {"assets/pet/roxy_sprite_atlas.webp", "assets/pet/roxy_sprite_layout.json"} <= {row["path"] for row in rows}
    assert "data/personas/roxy/evaluation/persona_cases.jsonl" in {row["path"] for row in rows}
    assert not any("source/" in row["path"] or row["path"].endswith("preview.gif") for row in rows)
    assert len(beta.build_datas(ROOT)) == 38


def test_external_bundle_and_complete_zip(synthetic_bundle, tmp_path):
    bundle, rows = synthetic_bundle
    assert beta.validate_bundle(bundle, rows, ["libssl-3-x64.dll", "libcrypto-3-x64.dll"]) == rows
    archive_path = tmp_path / "whole.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for row in rows:
            archive.write(bundle / row["path"], "RoxyPlan/" + row["path"])
    beta.verify_zip(archive_path, rows)
    # A physically copied checker runs from outside the source repository.
    standalone = tmp_path / "standalone_check.py"
    shutil.copyfile(ROOT / "scripts/check_beta_release.py", standalone)
    manifest = tmp_path / "manifest.json"
    beta.save_json(manifest, {"status": "built_package_checked_runtime_unverified", "files": rows,
                   "ssl_dlls": ["libssl-3-x64.dll", "libcrypto-3-x64.dll"],
                   "zip": {"sha256": beta.sha256(archive_path)}})
    result = subprocess.run([sys.executable, "-B", str(standalone), "--check", str(bundle),
                             "--manifest", str(manifest), "--zip", str(archive_path)],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "package_checked_runtime_unverified"


@pytest.mark.parametrize("name", [".env", "_internal/data/private/chat.json", "_internal/data/pet_config.json",
                                 "_internal/assets/pet/source/material.zip", "scripts/preview_chat.py"])
def test_forbidden_files_cannot_be_approved_by_manifest(synthetic_bundle, name):
    bundle, rows = synthetic_bundle
    path = bundle / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(beta.ReleaseError, match="whitelist|Forbidden release directory"):
        beta.validate_bundle(bundle, rows + [{"path": name, "sha256": beta.sha256(path)}])


@pytest.mark.parametrize("name", ["data/personas/roxy/evaluation/persona_cases.jsonl", "libssl-3-x64.dll"])
def test_missing_evaluation_and_ssl_are_fatal(synthetic_bundle, name):
    bundle, rows = synthetic_bundle
    (bundle / "_internal" / name).unlink()
    with pytest.raises(beta.ReleaseError, match="Missing"):
        beta.validate_bundle(bundle, rows, ["libssl-3-x64.dll", "libcrypto-3-x64.dll"])


def test_unreviewed_default_and_credentials_are_fatal(synthetic_bundle):
    bundle, rows = synthetic_bundle
    (bundle / "_internal/data/roxy_personality.json").write_text('{"name":"personalized"}', encoding="utf-8")
    with pytest.raises(beta.ReleaseError, match="default changed"):
        beta.validate_bundle(bundle, rows)
    (bundle / "_internal/data/roxy_personality.json").write_text('{"api_key":"synthetic-token"}', encoding="utf-8")
    with pytest.raises(beta.ReleaseError, match="credential"):
        beta.validate_bundle(bundle, rows)


def test_partial_zip_and_modified_file_are_fatal(synthetic_bundle, tmp_path):
    bundle, rows = synthetic_bundle
    archive_path = tmp_path / "partial.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.write(bundle / "RoxyPlan.exe", "RoxyPlan/RoxyPlan.exe")
    with pytest.raises(beta.ReleaseError, match="entire"):
        beta.verify_zip(archive_path, rows)
    (bundle / "RoxyPlan.exe").write_bytes(b"changed")
    with pytest.raises(beta.ReleaseError, match="hash mismatch"):
        beta.validate_bundle(bundle, rows)


def test_missing_entry_fails_before_dependency_probe(tmp_path):
    result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/check_beta_release.py"),
                             "--prepare", "--root", str(tmp_path), "--run-root", str(tmp_path),
                             "--version", "0.9.0-beta.1"], capture_output=True, text=True)
    assert result.returncode == 1
    assert "Beta entry missing: roxy_main.py" in result.stderr
    assert not list(tmp_path.glob("*.zip"))


def test_source_change_blocks_zip_creation(tmp_path, monkeypatch):
    beta.save_json(tmp_path / "source-manifest.json", {"source": {"content_sha256": "before"}})
    monkeypatch.setattr(beta, "source_snapshot", lambda root: {"content_sha256": "after"})
    with pytest.raises(beta.ReleaseError, match="changed during build"):
        beta.finalize(ROOT, tmp_path)
    assert not list(tmp_path.glob("*.zip"))


def test_analysis_does_not_allow_whole_data_tree(tmp_path):
    leaked = tmp_path / "settings.json"
    leaked.write_text("{}", encoding="utf-8")
    analysis = SimpleNamespace(binaries=[], datas=[("data/pet_config.json", str(leaked), "DATA")], pure=[], scripts=[])
    with pytest.raises(beta.ReleaseError, match="outside release whitelist"):
        beta.write_analysis(ROOT, tmp_path, analysis, set())
