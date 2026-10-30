"""
utils.py — Utility functions (sync, no API calls) for the AI Code Agent.
Handles file parsing, project formatting, syntax checking, test running, and packaging.

Everything here is blocking. Call the heavy ones (verify, run_tests, write_project)
through ``asyncio.to_thread`` from async code.
"""

import ast
import io
import os
import re
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ─────────────────────────── Regex Patterns ─────────────────────────────────

SAFE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.py")

# "### FILE: name.py" (tolerates ##/####, optional backticks around the name)
FILE_HEADER = re.compile(r"^#{2,4}[ \t]*FILE:[ \t]*`?([^\s`]+)`?[ \t]*$", re.MULTILINE)


# ─────────────────────────── File Parsing ───────────────────────────────────

def _extract_fenced_block(chunk: str) -> Optional[str]:
    """
    Return the body of the first fenced code block in *chunk*.

    Fence depth is tracked, so a file that itself contains fenced blocks
    (e.g. a docstring with ```python ... ```) is not cut short.
    Returns None if there is no opening fence or the block is never closed
    (a truncated reply must not be accepted as a complete file).
    """
    lines = chunk.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            start = i
            break
    if start is None:
        return None

    depth = 1
    for j in range(start + 1, len(lines)):
        stripped = lines[j].strip()
        if stripped == "```":
            depth -= 1
            if depth == 0:
                return "\n".join(lines[start + 1:j])
        elif stripped.startswith("```"):
            depth += 1
    return None


def parse_files(text: str) -> Tuple[Dict[str, str], List[str]]:
    """
    Parse LLM output for file blocks.

    Returns:
        (files_dict, rejected_list) — accepted files keyed by name, and a list
        of rejected filenames (unsafe names).
    """
    files: Dict[str, str] = {}
    rejected: List[str] = []
    text = text or ""

    headers = list(FILE_HEADER.finditer(text))
    for idx, m in enumerate(headers):
        name = m.group(1)
        end = headers[idx + 1].start() if idx + 1 < len(headers) else len(text)
        body = _extract_fenced_block(text[m.end():end])
        if body is None:
            continue
        if SAFE_NAME.fullmatch(name):
            files[name] = body.strip("\n") + "\n"
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


def count_test_metrics(files: Dict[str, str]) -> Tuple[int, int]:
    """(number of test functions, number of assertions) in the given test files."""
    n_tests = n_asserts = 0
    for source in files.values():
        try:
            tree = ast.parse(source)
        except SyntaxError:
            n_tests += len(re.findall(r"^\s*(?:async\s+)?def\s+test", source, re.MULTILINE))
            n_asserts += len(re.findall(r"^\s*assert\b|pytest\.raises", source, re.MULTILINE))
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                n_tests += 1
            elif isinstance(node, ast.Assert):
                n_asserts += 1
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and node.func.attr in ("raises", "warns")):
                n_asserts += 1
    return n_tests, n_asserts


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

PYTEST_ARGS = ["-m", "pytest", "-x", "-q", "--no-header", "-p", "no:cacheprovider"]
MEM_LIMIT_KB = 3 * 1024 * 1024      # 3 GB virtual memory (POSIX only)
FILE_LIMIT_BLOCKS = 20480           # ~10 MB per written file (POSIX only)
DOCKER_IMAGE = os.environ.get("SANDBOX_IMAGE", "agent-sandbox")


def _clean_env(workdir: str) -> Dict[str, str]:
    """Minimal environment: no API keys or other secrets leak into test code."""
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
    return env


def _run_tests_docker(workdir: str, timeout: int) -> Tuple[bool, str]:
    """Run pytest inside a throw-away container with no network."""
    name = f"agent-sbx-{uuid.uuid4().hex[:10]}"
    cmd = [
        "docker", "run", "--rm", "--name", name,
        "--network", "none",
        "--memory", "512m", "--cpus", "1", "--pids-limit", "128",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--tmpfs", "/tmp",
        "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "HOME=/tmp",
        "-v", f"{Path(workdir).resolve()}:/work", "-w", "/work",
    ]
    if hasattr(os, "getuid"):   # Linux/macOS: same uid as the host so the mounted dir is writable
        cmd += ["--user", f"{os.getuid()}:{os.getgid()}"]
    cmd += [DOCKER_IMAGE, "python", *PYTEST_ARGS]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 15)
    except FileNotFoundError:
        return False, "SANDBOX=docker nhưng không tìm thấy lệnh 'docker' trên máy."
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", name], capture_output=True)
        return False, f"TIMEOUT: test chạy quá {timeout}s (có thể bị vòng lặp vô hạn)."
    log = (proc.stdout + "\n" + proc.stderr).strip()
    return proc.returncode == 0, log


def run_tests(workdir: str, timeout: int = 30) -> Tuple[bool, str]:
    """
    Run pytest in workdir.

    - SANDBOX=docker  -> inside a container (no network, memory/CPU/pid limits).
    - otherwise       -> local subprocess with a clean environment; on Linux/macOS
                         CPU time, virtual memory and file size are also capped.
                         (Windows has no equivalent: only the clean env + timeout.)
    """
    if os.environ.get("SANDBOX", "").strip().lower() == "docker":
        return _run_tests_docker(workdir, timeout)

    cmd = [sys.executable, *PYTEST_ARGS]
    if os.name == "posix":
        script = (
            f"ulimit -t {timeout + 10} 2>/dev/null; "
            f"ulimit -v {MEM_LIMIT_KB} 2>/dev/null; "
            f"ulimit -f {FILE_LIMIT_BLOCKS} 2>/dev/null; "
            'exec "$@"'
        )
        cmd = ["/bin/sh", "-c", script, "sh", *cmd]

    try:
        proc = subprocess.run(
            cmd,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_clean_env(workdir),
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


def make_zip_bytes(files: Dict[str, str], extra: Optional[Dict[str, str]] = None) -> bytes:
    """Same as make_zip but in memory (downloads are built on demand from the DB)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, code in files.items():
            z.writestr(name, code)
        for name, text in (extra or {}).items():
            z.writestr(name, text)
    return buf.getvalue()


# ─────────────────────────── Text Helpers ───────────────────────────────────

def tail(text: str, limit: int = 4000) -> str:
    """Return the last limit characters of text."""
    if len(text) <= limit:
        return text
    return "...(đã cắt bớt)...\n" + text[-limit:]
