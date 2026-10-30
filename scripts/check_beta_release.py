"""Standalone Windows onedir policy/checker; stdlib only, no app imports.

Copy this script and the external build-manifest.json beside an extracted ZIP
on a separate machine; --check needs neither Git nor the source repository.
The manifest is evidence, not a signature: obtain it from the build coordinator.
No check in this file proves GUI/HTTPS operation on a machine without Python.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
import platform
from pathlib import Path, PurePosixPath
import re
import struct
import subprocess
import sys
import zipfile


RESOURCE_PATHS = (
    "assets/chat/chat_texture.png", "assets/chat/lantern_footer.png",
    "assets/chat/roxy_avatar.png", "assets/chat/roxy_cat_header.png",
    "assets/growth/roxy_growth.png",
    *("assets/ui/settings/" + name + ".png" for name in (
        "header_emblem", "section_companion", "section_model", "section_pet",
        "section_privacy", "spin_down", "spin_down@2x", "spin_up", "spin_up@2x", "title_star")),
    "assets/pet/roxy_pet_transparent.png",
    "assets/pet/roxy_sprite_atlas.webp", "assets/pet/roxy_sprite_layout.json",
    *(f"assets/pet/dance/dance_{number:03d}.png" for number in range(8)),
    "data/roxy_personality.json", "data/pet_tips.json",
    *("data/personas/roxy/" + name for name in (
        "manifest.json", "identity.md", "behavior.md", "speaking_style.md",
        "truth_and_boundaries.md", "relationships.json", "lore/worldview.md",
        "examples/dialogue_examples.jsonl", "evaluation/persona_cases.jsonl", "assets.json")),
)
# Manually read on 2026-10-13: generic default rules/tips and distilled persona
# guidance/examples only; no nickname, settings, credentials or user records.
# Pin reviewed text; only Git's LF/CRLF conversion is accepted as equivalent.
# Output manifests still record the actual raw-byte hash of every file.
CLEAN_DATA_SHA256 = {
    "data/roxy_personality.json": "105ac7320b4b008958247edb7b2ef5a03cfe7ba2549dc7cf1604d190113c81eb",
    "data/pet_tips.json": "058abc3e6f9d1be4459c9b6d7ddaa91d53f7609d13c3b361271b40cd2e7adae8",
    "data/personas/roxy/assets.json": "e0529d15430dabed580ca9d8752f15a795cc9b9955bdd1b15ea5a1e7b3d37231",
    "data/personas/roxy/behavior.md": "e6ecd5c331b9562e13328524ef3e021e0538cc8f80cf0800aa013e69c45ce93b",
    "data/personas/roxy/identity.md": "abe6af3728eab905e5f9283f3f200f07b26353ef26b6a4162aa284c4090759d6",
    "data/personas/roxy/manifest.json": "3bd0d4309e62bb39d6d5b45f6d8b21568bc0372a5e55a27fbbeef30291e32ae7",
    "data/personas/roxy/relationships.json": "95af61591ec0a52f5cc3cac6d3708e1441ff93c783c5549b4622306969543f64",
    "data/personas/roxy/speaking_style.md": "9a78391be47cf9ca749c480c06c4fc268c80e3a4edd010c2ac71a392d4dce98a",
    "data/personas/roxy/truth_and_boundaries.md": "724ffa34a98b3959a124325f2e35993b8aebd65370f57d3dc3d90cd37df2a5f6",
    "data/personas/roxy/lore/worldview.md": "60218ec52e6dbe94336fe4de51ea184645ee3004be620f3b63f1d8b2b220d1b7",
    "data/personas/roxy/examples/dialogue_examples.jsonl": "c93164e6bb850fa3a79cdf0800a5f4225db3332123e470f7ae30e027e7e318af",
    "data/personas/roxy/evaluation/persona_cases.jsonl": "66edf8cbf5ad3d3d0cd1092dc6776b4314057b2c9c2644039add8ae5b3c7cee2",
}
QT_PLUGINS = frozenset("PySide6/plugins/" + path for path in (
    "platforms/qwindows.dll", "imageformats/qjpeg.dll", "imageformats/qwebp.dll",
    "imageformats/qsvg.dll"))
SECRET_PATTERN = re.compile(
    rb"(?:sk-[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[ \t]*\r?\n[A-Za-z0-9+/]{32,}={0,2}"
    rb"|(?i:bearer)\s+[A-Za-z0-9_.-]{24,})")
SECRET_FIELDS = {"api_key", "access_token", "refresh_token", "client_secret", "secret_key", "password"}
FORBIDDEN_PARTS = {".git", ".venv", "venv", "__pycache__", "test_workspace", "tests",
                   "tools", "scripts", "knowledge", "private", "logs", "reports", "source"}
FORBIDDEN_NAMES = {"memory.json", "pet_config.json", "llm_config.json", "settings.json"}


class ReleaseError(ValueError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def matches_reviewed_template(path: Path, expected: str) -> bool:
    content = path.read_bytes()
    lf = content.replace(b"\r\n", b"\n")
    return expected in {
        hashlib.sha256(content).hexdigest(),
        hashlib.sha256(lf).hexdigest(),
        hashlib.sha256(lf.replace(b"\n", b"\r\n")).hexdigest(),
    }


def save_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def safe_relative(name: str) -> str:
    path = PurePosixPath(name)
    if (not name or "\\" in name or ":" in name or path.is_absolute()
            or any(part in {".", ".."} for part in name.split("/"))
            or str(path) != name):
        raise ReleaseError("Unsafe relative path: " + name)
    return name


def assert_plain_path(path: Path, root: Path) -> None:
    if not path.resolve().is_relative_to(root.resolve()):
        raise ReleaseError("Path escapes root: " + str(path))
    for item in (path, *path.parents):
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise ReleaseError("Links/reparse points are not distributable: " + str(item))
        if item == root:
            break


def scan_credentials(path: Path) -> None:
    # Chunk overlap handles a token crossing a read boundary; never print its value.
    tail = b""
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            if SECRET_PATTERN.search(tail + block):
                raise ReleaseError("Credential marker detected in " + path.name)
            tail = block[-4096:]
    if path.suffix in {".json", ".jsonl"}:
        text = path.read_text(encoding="utf-8-sig")
        records = [json.loads(line) for line in text.splitlines() if line.strip()] if path.suffix == ".jsonl" else [json.loads(text)]

        def walk(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if str(key).lower() in SECRET_FIELDS and item not in ("", None):
                        raise ReleaseError("Nonempty credential field in " + path.name)
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
        for record in records:
            walk(record)
    if path.suffix == ".py":
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=path.name)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and SECRET_PATTERN.search(node.value.encode("utf-8"))):
                raise ReleaseError("Credential literal detected in " + path.name)
            pairs = []
            if isinstance(node, ast.Dict):
                pairs = [(key.value, value) for key, value in zip(node.keys, node.values)
                         if isinstance(key, ast.Constant) and isinstance(key.value, str)]
            elif isinstance(node, ast.Assign):
                pairs = [(target.id, node.value) for target in node.targets if isinstance(target, ast.Name)]
            for key, value in pairs:
                if (key.lower() in SECRET_FIELDS and isinstance(value, ast.Constant)
                        and isinstance(value.value, str) and value.value):
                    raise ReleaseError("Literal credential field in " + path.name)


def resource_inventory(root: Path) -> list[dict]:
    rows = []
    for name in RESOURCE_PATHS:
        path = root / name
        assert_plain_path(path, root)
        if not path.is_file():
            raise ReleaseError("Missing runtime resource: " + name)
        scan_credentials(path)
        digest = sha256(path)
        if name in CLEAN_DATA_SHA256 and not matches_reviewed_template(path, CLEAN_DATA_SHA256[name]):
            raise ReleaseError("Clean default changed; review required: " + name)
        rows.append({"path": name, "size": path.stat().st_size, "sha256": digest})
    persona = root / "data/personas/roxy"
    actual = {path.relative_to(root).as_posix() for path in persona.rglob("*") if path.is_file()}
    expected = {name for name in RESOURCE_PATHS if name.startswith("data/personas/roxy/")}
    if actual != expected:
        raise ReleaseError("Persona content differs from reviewed complete pack")
    mapped = json.loads((persona / "manifest.json").read_text(encoding="utf-8"))["files"].values()
    if {"data/personas/roxy/" + safe_relative(name) for name in mapped} != expected - {"data/personas/roxy/manifest.json"}:
        raise ReleaseError("Persona manifest mappings differ from whitelist")
    for folder in ("chat", "growth"):
        manifest = root / "assets" / folder / "assets_manifest.json"
        for row in json.loads(manifest.read_text(encoding="utf-8"))["assets"]:
            path = "assets/" + folder + "/" + safe_relative(row["file"])
            if path not in RESOURCE_PATHS or sha256(root / path) != row["sha256"]:
                raise ReleaseError("Artwork manifest mismatch: " + path)
    dance = {path.relative_to(root).as_posix() for path in (root / "assets/pet/dance").glob("dance_*.png")}
    if dance != {name for name in RESOURCE_PATHS if "/dance/" in name}:
        raise ReleaseError("Dance frame references changed; update explicit whitelist")
    return rows


def build_datas(root: Path) -> list[tuple[str, str]]:
    return [(str(root / row["path"]), str(PurePosixPath(row["path"]).parent))
            for row in resource_inventory(root)]


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, encoding="utf-8").strip()


def source_snapshot(root: Path) -> dict:
    paths = {root / "roxy_main.py", root / "requirements.txt"}
    if (root / "roxy_study_main.py").is_file():
        paths.add(root / "roxy_study_main.py")
    for directory in ("frontend", "modules", "backend"):
        paths.update(path for path in (root / directory).rglob("*.py") if "__pycache__" not in path.parts)
    paths.update(root / name for name in (
        "packaging/RoxyPlan.spec", "packaging/requirements-build.txt", "scripts/build_beta.ps1",
        "scripts/check_beta_release.py", "scripts/run_isolated_tests.ps1", "tests/test_beta_packaging.py"))
    rows = []
    for path in sorted(paths):
        assert_plain_path(path, root)
        if not path.is_file():
            raise ReleaseError("Required build source missing: " + path.name)
        scan_credentials(path)
        rows.append({"path": path.relative_to(root).as_posix(), "sha256": sha256(path)})
    resources = resource_inventory(root)
    identity = {"sources": rows, "resources": resources}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    # Git status includes ignored resources only through the inventory, not Git.
    return {"head": git(root, "rev-parse", "HEAD"), "dirty": bool(git(root, "status", "--porcelain")),
            "content_sha256": digest, **identity}


def interpreter_is_amd64(path: Path) -> bool:
    """Read PE architecture directly; machine() may be empty in hosted shells."""
    try:
        with path.open("rb") as stream:
            if stream.read(2) != b"MZ":
                return False
            stream.seek(60)
            offset = stream.read(4)
            if len(offset) != 4:
                return False
            stream.seek(int.from_bytes(offset, "little"))
            return stream.read(4) == b"PE\0\0" and stream.read(2) == b"\x64\x86"
    except OSError:
        return False


def environment(root: Path) -> dict:
    if (sys.platform != "win32" or struct.calcsize("P") != 8
            or not interpreter_is_amd64(Path(sys.executable))
            or sys.version_info[:2] != (3, 10) or sys.version_info[:3] == (3, 10, 0)):
        raise ReleaseError("Build requires Windows x64 Python 3.10.1+")
    if sys.prefix == sys.base_prefix or Path(sys.prefix).resolve() == (root / ".venv").resolve():
        raise ReleaseError("Use a dedicated build venv; do not build in the application's .venv")
    versions = {}
    for line in (root / "packaging/requirements-build.txt").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        package, required = line.split("==")
        try:
            installed = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError as error:
            raise ReleaseError("Missing build dependency: " + line) from error
        if installed != required:
            raise ReleaseError("Build dependency mismatch: " + package + " expected " + required)
        versions[package] = installed
    from PySide6.QtCore import qVersion
    return {"python": sys.version, "architecture": "x64", "qt": qVersion(), "packages": versions}


def permitted_output(name: str) -> bool:
    safe_relative(name)
    parts = PurePosixPath(name).parts
    lower = [part.lower() for part in parts]
    if any(part in FORBIDDEN_PARTS or part.startswith(".env") for part in lower) or lower[-1] in FORBIDDEN_NAMES:
        return False
    if name == "RoxyPlan.exe":
        return True
    if parts[0] != "_internal":
        return False
    inner = "/".join(parts[1:])
    if inner in RESOURCE_PATHS or inner == "base_library.zip":
        return True
    if inner in QT_PLUGINS:
        return True
    if "/plugins/" in inner:
        return False
    if len(parts) == 2 and PurePosixPath(inner).suffix.lower() in {".dll", ".pyd"}:
        return True
    if parts[1] in {"PySide6", "shiboken6", "PIL", "pillow.libs"} and PurePosixPath(inner).suffix.lower() in {".dll", ".pyd"}:
        return True
    return inner.startswith("PySide6/translations/qt") and inner.endswith(".qm")


def write_analysis(root: Path, run_root: Path, analysis, ssl_names: set[str]) -> None:
    files = {"RoxyPlan.exe": {"path": "RoxyPlan.exe"}}
    for dest, source, kind in [*analysis.binaries, *analysis.datas]:
        name = "_internal/" + dest.replace("\\", "/")
        if not permitted_output(name) or kind == "SYMLINK":
            raise ReleaseError("Analysis output outside release whitelist: " + name)
        path = Path(source)
        inner = name[len("_internal/"):]
        if inner in RESOURCE_PATHS:
            if path.resolve() != (root / inner).resolve():
                raise ReleaseError("Resource did not come from this source tree: " + name)
        elif inner == "base_library.zip":
            if not path.resolve().is_relative_to(run_root):
                raise ReleaseError("Python library ZIP did not come from the dedicated build")
        else:
            # Only dependency analysis may add native libraries/Qt translations;
            # never authorize employee files as implicit data or binaries.
            dependency_roots = [Path(sys.prefix), Path(sys.base_prefix)]
            if os.environ.get("SystemRoot"):
                dependency_roots.append(Path(os.environ["SystemRoot"]))
            if not any(path.resolve().is_relative_to(prefix.resolve()) for prefix in dependency_roots):
                raise ReleaseError("Dependency outside selected Python/system roots: " + name)
        if name in files:
            raise ReleaseError("Duplicate analysis destination: " + name)
        scan_credentials(path)
        files[name] = {"path": name, "sha256": sha256(path)}
    sources = []
    for module, source, kind in [*analysis.pure, *analysis.scripts]:
        path = Path(source)
        if path.is_file() and path.resolve().is_relative_to(root):
            scan_credentials(path)
            sources.append({"module": module, "path": path.relative_to(root).as_posix(), "sha256": sha256(path)})
    save_json(run_root / "analysis-manifest.json", {"files": sorted(files.values(), key=lambda row: row["path"]),
              "analyzed_local_sources": sources, "ssl_dlls": sorted(ssl_names),
              "module_names": sorted(row[0] for row in analysis.pure)})


def inventory(bundle: Path) -> list[dict]:
    if not bundle.is_dir():
        raise ReleaseError("Onedir directory missing")
    rows = []
    # Inspect directories too, so empty private/venv/log trees cannot sneak in.
    for path in sorted(bundle.rglob("*")):
        assert_plain_path(path, bundle)
        name = path.relative_to(bundle).as_posix()
        if path.is_dir():
            if any(part.lower() in FORBIDDEN_PARTS or part.lower().startswith(".env") for part in path.relative_to(bundle).parts):
                raise ReleaseError("Forbidden release directory: " + name)
            continue
        if not permitted_output(name):
            raise ReleaseError("File outside release whitelist: " + name)
        scan_credentials(path)
        if name.startswith("_internal/data/"):
            inner = name[len("_internal/"):]
            if not matches_reviewed_template(path, CLEAN_DATA_SHA256[inner]):
                raise ReleaseError("Release clean default changed: " + name)
        if name == "_internal/base_library.zip":
            with zipfile.ZipFile(path) as archive:
                for item in archive.infolist():
                    safe_relative(item.filename)
                    if not item.filename.endswith(".pyc"):
                        raise ReleaseError("Unexpected member in base_library.zip")
                    if SECRET_PATTERN.search(archive.read(item)):
                        raise ReleaseError("Credential marker in base_library.zip")
        rows.append({"path": name, "size": path.stat().st_size, "sha256": sha256(path)})
    return rows


def validate_bundle(bundle: Path, expected: list[dict], ssl_names=()) -> list[dict]:
    rows = inventory(bundle)
    actual = {row["path"]: row for row in rows}
    approved = {safe_relative(row["path"]): row for row in expected}
    if len(approved) != len(expected) or len({name.lower() for name in approved}) != len(expected):
        raise ReleaseError("Duplicate/case-colliding manifest paths")
    required = {"RoxyPlan.exe", "_internal/base_library.zip", "_internal/python310.dll",
                "_internal/PySide6/QtCore.pyd", "_internal/PySide6/QtGui.pyd", "_internal/PySide6/QtWidgets.pyd",
                "_internal/PySide6/Qt6Core.dll", "_internal/PySide6/Qt6Gui.dll", "_internal/PySide6/Qt6Widgets.dll",
                "_internal/shiboken6/shiboken6.abi3.dll",
                *("_internal/" + name for name in RESOURCE_PATHS),
                *("_internal/" + name for name in QT_PLUGINS)}
    if not required.issubset(actual):
        raise ReleaseError("Missing required files: " + ", ".join(sorted(required - actual.keys())))
    if not any("shiboken6" in name.lower() and name.endswith(".pyd") for name in actual):
        raise ReleaseError("Missing shiboken6 extension")
    if not any(PurePosixPath(name).name.startswith("_ssl") and name.endswith(".pyd") for name in actual):
        raise ReleaseError("Missing _ssl extension")
    for name in ssl_names:
        if "_internal/" + name not in actual:
            raise ReleaseError("Missing linked SSL DLL: " + name)
    if actual.keys() != approved.keys():
        raise ReleaseError("Output differs from approved manifest: extra=" + str(sorted(actual.keys() - approved.keys()))
                           + " missing=" + str(sorted(approved.keys() - actual.keys())))
    for name, row in approved.items():
        if "sha256" in row and row["sha256"] != actual[name]["sha256"]:
            raise ReleaseError("File hash mismatch: " + name)
    return rows


def verify_zip(zip_path: Path, rows: list[dict]) -> None:
    expected = {"RoxyPlan/" + row["path"]: row["sha256"] for row in rows}
    with zipfile.ZipFile(zip_path) as archive:
        members = archive.infolist()
        if len(members) != len(expected) or {item.filename for item in members} != expected.keys():
            raise ReleaseError("ZIP must contain exactly the entire RoxyPlan onedir")
        for item in members:
            safe_relative(item.filename)
            if ((item.external_attr >> 16) & 0o170000) == 0o120000:
                raise ReleaseError("ZIP contains a symlink")
            if hashlib.sha256(archive.read(item)).hexdigest() != expected[item.filename]:
                raise ReleaseError("ZIP member hash mismatch: " + item.filename)


def finalize(root: Path, run_root: Path) -> None:
    before = json.loads((run_root / "source-manifest.json").read_text(encoding="utf-8"))
    after = source_snapshot(root)
    if before["source"] != after:
        raise ReleaseError("Source/resources/Git changed during build; no distributable ZIP")
    analysis = json.loads((run_root / "analysis-manifest.json").read_text(encoding="utf-8"))
    bundle = run_root / "dist/RoxyPlan"
    rows = validate_bundle(bundle, analysis["files"], analysis["ssl_dlls"])
    zip_path = run_root / ("RoxyPlan-" + before["product_version"] + "-windows-x64.zip")
    if zip_path.exists():
        raise ReleaseError("Refusing to overwrite existing ZIP")
    partial_zip = zip_path.with_suffix(".zip.partial")
    with zipfile.ZipFile(partial_zip, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for row in rows:
            # Stable archive metadata, entire onedir. Runtime still needs clean-machine QA.
            info = zipfile.ZipInfo("RoxyPlan/" + row["path"], (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (bundle / row["path"]).read_bytes())
    verify_zip(partial_zip, rows)
    save_json(run_root / "build-manifest.json", {**before, **analysis, "files": rows,
              "status": "built_package_checked_runtime_unverified",
              "zip": {"name": zip_path.name, "sha256": sha256(partial_zip)},
              "verification_limits": "No GUI, HTTPS or clean machine without Python acceptance implied."})
    # Incomplete/failed ZIP work remains .partial, never named as a Beta ZIP.
    partial_zip.rename(zip_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--resources", action="store_true")
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--finalize", action="store_true")
    mode.add_argument("--check", type=Path, metavar="ONEDIR")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--version")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--zip", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.check:
            if not args.manifest:
                raise ReleaseError("--check requires external --manifest")
            manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
            if manifest.get("status") != "built_package_checked_runtime_unverified":
                raise ReleaseError("Manifest does not describe a successfully checked package")
            rows = validate_bundle(args.check.resolve(), manifest["files"], manifest["ssl_dlls"])
            if args.zip:
                verify_zip(args.zip, rows)
                if sha256(args.zip) != manifest["zip"]["sha256"]:
                    raise ReleaseError("ZIP SHA256 mismatch")
            result = {"status": "package_checked_runtime_unverified", "files": len(rows)}
        else:
            if not args.root:
                raise ReleaseError("--root required for source preparation")
            root = args.root.resolve()
            if args.resources:
                result = {"resources": resource_inventory(root)}
            else:
                if not args.run_root or not args.run_root.is_dir():
                    raise ReleaseError("Existing dedicated --run-root required")
                if args.prepare:
                    if not (root / "roxy_main.py").is_file():
                        raise ReleaseError("Beta entry missing: roxy_main.py; integrate the approved entry first")
                    if not args.version or not re.fullmatch(r"[0-9][A-Za-z0-9.+-]{0,63}", args.version):
                        raise ReleaseError("Explicit safe product --version required")
                    profile = os.environ.get("ROXY_BETA_PROFILE", "beta")
                    if profile not in {"beta", "study"}:
                        raise ReleaseError("Unknown build profile")
                    if profile == "study" and not (root / "roxy_study_main.py").is_file():
                        raise ReleaseError("Study entry missing")
                    result = {"product_version": args.version, "profile": profile, "created_utc": datetime.now(timezone.utc).isoformat(),
                              "tools": environment(root), "source": source_snapshot(root)}
                    target = args.run_root / "source-manifest.json"
                    if target.exists():
                        raise ReleaseError("Refusing to overwrite source manifest")
                    save_json(target, result)
                else:
                    finalize(root, args.run_root)
                    result = {"status": "built_package_checked_runtime_unverified"}
        if args.output:
            save_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ReleaseError, OSError, ValueError, KeyError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        print("Beta check failed: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
