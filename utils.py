"""
utils.py — Utility functions (sync, no API calls) for the AI Code Agent.
Handles file parsing, project formatting, syntax checking, test running, and packaging.
"""

import ast
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ─────────────────────────── Regex Patterns ─────────────────────────────────

SAFE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.py")

FILE_PATTERN = re.compile(
    r"###\s*FILE:\s*([^\s`]+)\s*\n```[\w+\-]*\n(.*?)```", re.DOTALL
)


# ─────────────────────────── File Parsing ───────────────────────────────────

def parse_files(text: str) -> Tuple[Dict[str, str], List[str]]:
    """
    Parse LLM output for file blocks.

    Returns:
        (files_dict, rejected_list) — accepted files keyed by name, and a list
        of rejected filenames (unsafe names).
    """
    files: Dict[str, str] = {}
    rejected: List[str] = []

    for name, code in FILE_PATTERN.findall(text or ""):
        if SAFE_NAME.fullmatch(name):
            files[name] = code.strip("\n") + "\n"
        else:
            rejected.append(name)

    return files, rejected


# ─────────────────────────── Project Formatting ─────────────────────────────

def format_project(files: Dict[str, str]) -> str:
    """Format a dict of files into a single string for LLM context."""
    parts = [
        f"### FILE: {name}\n```python\n{code.rstrip()}\n```"
        for name, code in sorted(files.items())
    ]
    return "\n\n".join(parts)


def tests_of(files: Dict[str, str]) -> Dict[str, str]:
    """Return only test files from the project."""
    return {n: c for n, c in files.items() if n.startswith("test_")}


def code_of(files: Dict[str, str]) -> Dict[str, str]:
    """Return only non-test files from the project."""
    return {n: c for n, c in files.items() if not n.startswith("test_")}


# ─────────────────────────── Model Helpers ──────────────────────────────────

def parse_models(text: str) -> List[str]:
    """Parse a comma/newline separated model list."""
    return [m.strip() for m in re.split(r"[,\n]", text or "") if m.strip()]


def short(model: str) -> str:
    """Return a short display name for a model identifier."""
    return model.split("/")[-1]


# ─────────────────────────── Syntax Check ───────────────────────────────────

def check_syntax(files: Dict[str, str]) -> Optional[str]:
    """
    Check Python syntax of all files. Returns error message or None.
    """
    for name, source in sorted(files.items()):
        try:
            ast.parse(source, filename=name)
        except SyntaxError as e:
            lines = source.splitlines()
            bad = lines[e.lineno - 1] if e.lineno and e.lineno <= len(lines) else ""
            return (
                f"LỖI CÚ PHÁP trong {name}, dòng {e.lineno}: {e.msg}\n"
                f"Dòng lỗi: {bad[:300]}\n"
                "Lưu ý: mỗi câu lệnh phải nằm trên một dòng riêng, đúng thụt lề."
            )
    return None


# ─────────────────────────── File I/O ───────────────────────────────────────

def write_project(workdir: str, files: Dict[str, str]) -> None:
    """Write project files to workdir, removing stale .py files."""
    wd = Path(workdir)
    wd.mkdir(parents=True, exist_ok=True)
    for path in wd.glob("*.py"):
        if path.name not in files:
            path.unlink()
    for name, code in files.items():
        (wd / name).write_text(code, encoding="utf-8")


# ─────────────────────────── Test Running ───────────────────────────────────

def run_tests(workdir: str, timeout: int = 30) -> Tuple[bool, str]:
    """
    Run pytest in workdir with a clean environment (no API keys leaked).
    """
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "TEMP": os.environ.get("TEMP", tempfile.gettempdir()),
        "TMP": os.environ.get("TMP", tempfile.gettempdir()),
        "HOME": str(workdir),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for key in ("USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        val = os.environ.get(key)
        if val:
            env[key] = val

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-x", "-q", "--no-header",
             "-p", "no:cacheprovider"],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return False, f"TIMEOUT: test chạy quá {timeout}s (có thể bị vòng lặp vô hạn)."
    log = (proc.stdout + "\n" + proc.stderr).strip()
    return proc.returncode == 0, log


def verify(workdir: str, files: Dict[str, str], timeout: int = 30) -> Tuple[bool, str]:
    """Write files and run tests. Returns (ok, log)."""
    err = check_syntax(files)
    if err:
        return False, err
    write_project(workdir, files)
    return run_tests(workdir, timeout)


# ─────────────────────────── Packaging ──────────────────────────────────────

def make_zip(
    files: Dict[str, str],
    path: str,
    extra: Optional[Dict[str, str]] = None,
) -> str:
    """Create a zip archive of files at path."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, code in files.items():
            z.writestr(name, code)
        for name, text in (extra or {}).items():
            z.writestr(name, text)
    return str(path)


# ─────────────────────────── Text Helpers ───────────────────────────────────

def tail(text: str, limit: int = 4000) -> str:
    """Return the last limit characters of text."""
    if len(text) <= limit:
        return text
    return "...(đã cắt bớt)...\n" + text[-limit:]
