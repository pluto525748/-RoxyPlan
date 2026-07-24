from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_GITIGNORE_LINES = {
    ".venv/",
    "__pycache__/",
    "*.pyc",
    "memory.json",
    "config.json",
    "data/today_plan.json",
    "data/today_plan.json.tmp",
    "data/action_log.json",
    "data/action_log.json.tmp",
    "data/growth_log.json",
    "data/growth_log.json.tmp",
    "data/private/",
    "test_gemini.py",
    "data/knowledge/*",
    "!data/knowledge/.gitkeep",
    "!data/knowledge/README.md",
    ".env",
}

DISALLOWED_TRACKED_EXACT = {
    "memory.json",
    "config.json",
    "data/today_plan.json",
    "data/today_plan.json.tmp",
    "data/action_log.json",
    "data/action_log.json.tmp",
    "data/growth_log.json",
    "data/growth_log.json.tmp",
    "test_gemini.py",
    ".env",
    "apikey.txt",
    "api_key.txt",
    "secret.txt",
    "token.txt",
}

DISALLOWED_TRACKED_PATTERNS = (
    re.compile(r"(^|/)\.venv(/|$)", re.IGNORECASE),
    re.compile(r"(^|/)__pycache__(/|$)", re.IGNORECASE),
    re.compile(r"\.pyc$", re.IGNORECASE),
    re.compile(r"^data/knowledge/(?!README\.md$|\.gitkeep$).+", re.IGNORECASE),
    re.compile(r"^data/private(/|$)", re.IGNORECASE),
)

TEXT_DOC_PATTERNS = ("README.md", "docs/**/*.md", "frontend/README.md", "data/README.md")
PUBLIC_SOURCE_PATTERNS = (
    "*.bat",
    "frontend/**/*.py",
    "modules/**/*.py",
    "server/**/*.py",
    "server/web/*.js",
    "server/web/*.html",
)
PRIVATE_PATH_PATTERN = re.compile(r"C:\\Users\\[^\\\s]+", re.IGNORECASE)
GENERIC_PRIVATE_PATH_PATTERN = re.compile(
    r"[A-Za-z]:[\\/]Users[\\/][^\\/\s]+", re.IGNORECASE
)
SECRET_TEXT_PATTERN = re.compile(r"(api[_-]?key|secret|token)\s*[:=]\s*['\"][^'\"]{8,}", re.IGNORECASE)


def run_git_ls_files() -> list[str]:
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"[FAIL] unable to inspect git tracked files: {error}")
        return []
    return [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]


def check_gitignore() -> list[str]:
    issues = []
    gitignore_path = PROJECT_ROOT / ".gitignore"
    if not gitignore_path.exists():
        return ["missing .gitignore"]

    lines = {
        line.strip()
        for line in gitignore_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    missing = sorted(REQUIRED_GITIGNORE_LINES - lines)
    if missing:
        issues.append(f".gitignore missing entries: {', '.join(missing)}")
    return issues


def check_tracked_files(tracked_files: list[str]) -> list[str]:
    issues = []
    tracked_set = set(tracked_files)
    exposed_exact = sorted(DISALLOWED_TRACKED_EXACT & tracked_set)
    if exposed_exact:
        issues.append(f"sensitive files are tracked: {', '.join(exposed_exact)}")

    exposed_patterns = []
    for path in tracked_files:
        if any(pattern.search(path) for pattern in DISALLOWED_TRACKED_PATTERNS):
            exposed_patterns.append(path)
    if exposed_patterns:
        issues.append(f"ignored runtime/cache files are tracked: {', '.join(sorted(exposed_patterns))}")
    return issues


def check_memory_example() -> list[str]:
    issues = []
    example_path = PROJECT_ROOT / "memory.example.json"
    if not example_path.exists():
        return ["missing memory.example.json"]

    try:
        data = json.loads(example_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return [f"memory.example.json is invalid JSON: {error}"]

    if not isinstance(data, dict):
        return ["memory.example.json must contain a JSON object"]

    memories = data.get("memories")
    if memories != []:
        issues.append("memory.example.json should keep memories as an empty list")

    profile = data.get("profile", {})
    if not isinstance(profile, dict):
        issues.append("memory.example.json profile should be an object")
    elif str(profile.get("nickname", "")).strip():
        issues.append("memory.example.json should not include a real nickname")

    serialized = json.dumps(data, ensure_ascii=False)
    if SECRET_TEXT_PATTERN.search(serialized):
        issues.append("memory.example.json appears to contain a secret-like value")
    return issues


def iter_public_docs() -> list[Path]:
    files: list[Path] = []
    for pattern in TEXT_DOC_PATTERNS:
        files.extend(path for path in PROJECT_ROOT.glob(pattern) if path.is_file())
    return sorted(set(files))


def check_public_docs() -> list[str]:
    issues = []
    for path in iter_public_docs():
        text = path.read_text(encoding="utf-8", errors="ignore")
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        if PRIVATE_PATH_PATTERN.search(text):
            issues.append(f"private absolute Windows path found in {relative}")
        if SECRET_TEXT_PATTERN.search(text):
            issues.append(f"secret-like text found in {relative}")
    return issues


def check_public_sources() -> list[str]:
    issues = []
    files = []
    for pattern in PUBLIC_SOURCE_PATTERNS:
        files.extend(path for path in PROJECT_ROOT.glob(pattern) if path.is_file())
    for path in sorted(set(files)):
        text = path.read_text(encoding="utf-8", errors="ignore")
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        if GENERIC_PRIVATE_PATH_PATTERN.search(text):
            issues.append(f"private absolute path found in source: {relative}")
        if SECRET_TEXT_PATTERN.search(text):
            issues.append(f"secret-like value found in source: {relative}")
    return issues


def main() -> int:
    issues: list[str] = []
    tracked_files = run_git_ls_files()
    if not tracked_files:
        issues.append("git tracked file list is empty or unavailable")

    checks = [
        ("gitignore", check_gitignore()),
        ("tracked files", check_tracked_files(tracked_files)),
        ("memory example", check_memory_example()),
        ("public docs", check_public_docs()),
        ("public sources", check_public_sources()),
    ]

    for label, check_issues in checks:
        if check_issues:
            print(f"[WARN] {label}")
            for issue in check_issues:
                print(f"  - {issue}")
            issues.extend(check_issues)
        else:
            print(f"[OK] {label}")

    if issues:
        print(f"[FAIL] public repo check found {len(issues)} issue(s)")
        return 1

    print("[OK] public repo check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
