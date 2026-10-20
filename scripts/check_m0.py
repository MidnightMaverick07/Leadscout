"""Quick M0 health check. Run: python scripts/check_m0.py"""
import importlib
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


# 1. Required files exist
required = [
    "AGENTS.md", "README.md", ".gitignore", ".env.example", "pyproject.toml",
    "leadagent/__init__.py", "leadagent/config.py", "leadagent/models.py",
    "leadagent/errors.py", "leadagent/state.py", "leadagent/budget.py",
    "leadagent/cli.py", "tests/conftest.py",
]
missing = [f for f in required if not (ROOT / f).exists()]
check("required files exist", not missing, f"missing: {missing}" if missing else "")

# 2. No later-milestone folders
later = ["leadagent/providers", "leadagent/nodes", "leadagent/tools", "leadagent/storage"]
present = [f for f in later if (ROOT / f).exists()]
check("no later-milestone folders", not present, f"found: {present}" if present else "")

# 3. No LangChain dependency
pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
check("no langchain dependency", "langchain" not in pyproject.lower())

# 4. Required packages import
for mod in ["pydantic", "pydantic_settings", "langgraph", "langgraph.checkpoint.sqlite"]:
    try:
        importlib.import_module(mod)
        check(f"import {mod}", True)
    except ImportError as exc:
        check(f"import {mod}", False, str(exc))

# 5. Our modules import
for mod in ["leadagent.config", "leadagent.models", "leadagent.errors",
            "leadagent.state", "leadagent.budget", "leadagent.cli"]:
    try:
        importlib.import_module(mod)
        check(f"import {mod}", True)
    except Exception as exc:  # noqa: BLE001 - report any import failure
        check(f"import {mod}", False, repr(exc))

# 6. Test suite passes
proc = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=ROOT,
                      capture_output=True, text=True)
last_line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
check("pytest passes", proc.returncode == 0, last_line)

# 7. CLI works
proc = subprocess.run([sys.executable, "-m", "leadagent.cli", "--help"], cwd=ROOT,
                      capture_output=True, text=True)
check("cli --help runs", proc.returncode == 0 and "run" in proc.stdout and "doctor" in proc.stdout)

# 8. No API key patterns in project files
hits = []
for path in ROOT.rglob("*"):
    if (not path.is_file() or ".venv" in path.parts or ".git" in path.parts or "__pycache__" in path.parts or path.name == "check_m0.py"):
        continue
    if path.suffix in {".pyc", ".whl"}:
        continue
    try:
        if "AIza" in path.read_text(encoding="utf-8"):
            hits.append(str(path.relative_to(ROOT)))
    except (UnicodeDecodeError, OSError):
        continue
check("no Google-style API key in files", not hits, f"found in: {hits}" if hits else "")

# Report
width = max(len(n) for n, _, _ in results)
for name, ok, detail in results:
    status = "PASS" if ok else "FAIL"
    line = f"[{status}] {name.ljust(width)}"
    if detail and not ok:
        line += f"  -> {detail}"
    print(line)

failed = [n for n, ok, _ in results if not ok]
print()
print("M0 STATUS:", "COMPLETE" if not failed else f"INCOMPLETE ({len(failed)} failing)")
sys.exit(0 if not failed else 1)