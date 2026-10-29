"""
pipeline.py — Async pipeline: planner → tester → coder → reviewer.
Sends events via async callback matching the WebSocket message contract.
"""

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Callable, Dict, List, Optional

from models import (
    CODER_PROMPT, COMMON_RULES, FALLBACK_MODEL, FILE_FORMAT,
    MAX_INSTRUCTION_CHARS, MAX_LOG_CHARS, PLANNER_PROMPT,
    REVIEW_ROUNDS, REVIEWER_PROMPT, TESTER_PROMPT, ROLE_LABEL,
)
from llm import call_role
from utils import (
    check_syntax, code_of, format_project, make_zip, parse_files,
    parse_models, short, tail, tests_of, verify, write_project,
)

logger = logging.getLogger(__name__)
SendEvent = Callable[[dict], "asyncio.coroutines"]


async def _log(send_event: SendEvent, role: str, message: str) -> None:
    await send_event({"type": "log", "role": role, "message": message})


async def _progress(send_event: SendEvent, stage: str, step: int,
                    total: int, status: str = "running") -> None:
    await send_event({"type": "progress", "stage": stage, "step": step,
                      "total": total, "status": status})


def _resolve_models(role_str: str, defaults: List[str]) -> List[str]:
    specific = parse_models(role_str)
    return specific if specific else (defaults if defaults else [FALLBACK_MODEL])


async def run_pipeline(
    command: str, project_text: str,
    default_models_str: str, planner_models_str: str,
    tester_models_str: str, coder_models_str: str,
    reviewer_models_str: str,
    use_planner: bool, use_reviewer: bool,
    max_rounds: int, api_key: str,
    session_id: str, send_event: SendEvent,
    timeout: int = 30,
) -> Dict[str, str]:
    """Run the full multi-agent coding pipeline. Returns final files dict."""

    default_models = parse_models(default_models_str) or [FALLBACK_MODEL]
    planner_m = _resolve_models(planner_models_str, default_models)
    tester_m = _resolve_models(tester_models_str, default_models)
    coder_m = _resolve_models(coder_models_str, default_models)
    reviewer_m = _resolve_models(reviewer_models_str, default_models)

    project: Dict[str, str] = {}
    if project_text:
        parsed, _ = parse_files(project_text)
        if parsed:
            project = parsed
    work = dict(project)

    total_stages = 2 + (1 if use_planner else 0) + (1 if use_reviewer else 0)
    cur = 0
    workdir = tempfile.mkdtemp(prefix=f"agent_{session_id}_")
    L = ROLE_LABEL

    # Thống kê lượng Token thời gian thực
    token_stats = {
        "prompt": 0,
        "completion": 0,
        "total": 0,
        "by_role": {
            "planner": {"prompt": 0, "completion": 0, "total": 0, "calls": 0, "model": ""},
            "tester": {"prompt": 0, "completion": 0, "total": 0, "calls": 0, "model": ""},
            "coder": {"prompt": 0, "completion": 0, "total": 0, "calls": 0, "model": ""},
            "reviewer": {"prompt": 0, "completion": 0, "total": 0, "calls": 0, "model": ""}
        }
    }

    async def _track_tokens(role: str, model: str, usage: dict):
        p = usage.get("prompt_tokens", 0) or 0
        c = usage.get("completion_tokens", 0) or 0
        t = usage.get("total_tokens", 0) or (p + c)

        token_stats["prompt"] += p
        token_stats["completion"] += c
        token_stats["total"] += t

        if role in token_stats["by_role"]:
            r = token_stats["by_role"][role]
            r["prompt"] += p
            r["completion"] += c
            r["total"] += t
            r["calls"] += 1
            r["model"] = short(model)

        await send_event({
            "type": "tokens",
            "role": role,
            "model": short(model),
            "step_usage": {"prompt": p, "completion": c, "total": t},
            "stats": token_stats
        })

    try:
        # ────────── 1. Planner ──────────
        spec = command
        if use_planner:
            cur += 1
            await _progress(send_event, "planner", cur, total_stages)
            await _log(send_event, "planner", f"[{L['planner']}] Đang lập đặc tả...")

            user = f"Yêu cầu của người dùng:\n{command}"
            if project:
                user = f"Dự án hiện tại:\n{format_project(project)}\n\n" + user

            reply, model, notes, usage = await call_role(
                planner_m, 0,
                [{"role": "system", "content": PLANNER_PROMPT},
                 {"role": "user", "content": user}], api_key)
            await _track_tokens("planner", model, usage)
            for n in notes:
                await _log(send_event, "planner", f"  ⚠ {n}")
            spec = reply.strip() or command
            await _log(send_event, "planner",
                       f"  ✓ {short(model)} đã lập xong đặc tả. (Dùng {usage.get('total_tokens', 0):,} tokens)")
            await send_event({"type": "spec", "content": spec})
            await _progress(send_event, "planner", cur, total_stages, "done")
        else:
            await _log(send_event, "system",
                       "Bỏ qua bước lập kế hoạch (dùng thẳng yêu cầu làm đặc tả).")
            await send_event({"type": "spec", "content": spec})

        # ────────── 2. Tester ──────────
        cur += 1
        await _progress(send_event, "tester", cur, total_stages)
        await _log(send_event, "tester",
                   f"[{L['tester']}] Đang viết test độc lập từ đặc tả...")

        user = f"Đặc tả:\n{spec}\n"
        old_tests = tests_of(work)
        if old_tests:
            user += ("\nCác file test hiện có (cập nhật và bổ sung cho khớp đặc tả, "
                     "giữ lại test cũ còn đúng):\n" + format_project(old_tests) + "\n")

        tester_msgs = [{"role": "system", "content": TESTER_PROMPT},
                       {"role": "user", "content": user}]
        new_tests = {}
        for attempt in range(2):
            reply, model, notes, usage = await call_role(tester_m, 0, tester_msgs, api_key)
            await _track_tokens("tester", model, usage)
            for n in notes:
                await _log(send_event, "tester", f"  ⚠ {n}")
            new_tests = tests_of(parse_files(reply)[0])
            if new_tests:
                break
            tester_msgs.append({"role": "assistant", "content": reply})
            tester_msgs.append({"role": "user", "content":
                                "Chưa có file test_*.py hợp lệ. Hãy gửi lại đúng định dạng."})

        if not new_tests:
            raise RuntimeError("Kiểm thử viên không trả về file test_*.py hợp lệ.")

        work.update(new_tests)
        write_project(workdir, work)
        await _log(send_event, "tester",
                   f"  ✓ {short(model)} đã viết: {', '.join(sorted(new_tests))} (Dùng {usage.get('total_tokens', 0):,} tokens)")
        await send_event({"type": "code", "files": work})
        await _progress(send_event, "tester", cur, total_stages, "done")

        # ────────── 3. Coder (debug loop) ──────────
        cur += 1
        await _progress(send_event, "coder", cur, total_stages)

        user = (f"Đặc tả:\n{spec}\n\nCác file test (chuẩn, không được sửa):\n"
                f"{format_project(tests_of(work))}\n")
        existing_code = code_of(project)
        if existing_code:
            user += f"\nCode hiện tại:\n{format_project(existing_code)}\n"
        user += "\nHãy viết hoặc sửa các file code để toàn bộ test qua."
        messages = [{"role": "system", "content": CODER_PROMPT},
                    {"role": "user", "content": user}]

        ok = False
        fail_count = 0
        for round_no in range(1, max_rounds + 1):
            await _log(send_event, "coder",
                       f"[{L['coder']}] Vòng {round_no}/{max_rounds}: viết/sửa code...")
            await _progress(send_event, "coder", round_no, max_rounds)

            reply, model, notes, usage = await call_role(
                coder_m, fail_count, messages, api_key)
            await _track_tokens("coder", model, usage)
            for n in notes:
                await _log(send_event, "coder", f"  ⚠ {n}")
            messages.append({"role": "assistant", "content": reply})

            files_parsed, rejected = parse_files(reply)
            if rejected:
                await _log(send_event, "coder",
                           f"  ⚠ Bỏ qua tên file không hợp lệ: {', '.join(rejected)}")
            code_files = code_of(files_parsed)
            if len(files_parsed) > len(code_files):
                await _log(send_event, "coder",
                           "  ⚠ Bỏ qua file test do lập trình viên gửi (test là chuẩn).")
            if not code_files:
                fail_count += 1
                await _log(send_event, "coder",
                           "  ✗ Không có file code hợp lệ, yêu cầu gửi lại.")
                messages.append({"role": "user", "content":
                    "Không đọc được file code nào. Hãy gửi lại theo đúng định dạng "
                    "'### FILE: ten_file.py' rồi khối ```python."})
                continue

            work.update(code_files)
            write_project(workdir, work)
            await send_event({"type": "code", "files": work})
            await _log(send_event, "coder",
                       f"  Nhận code từ {short(model)}, đang chạy test... (Dùng {usage.get('total_tokens', 0):,} tokens)")

            ok, log = verify(workdir, work, timeout)
            if ok:
                await _log(send_event, "coder", f"  ✓ Test đạt ở vòng {round_no}.")
                break

            fail_count += 1
            await _log(send_event, "coder", "  ✗ Test chưa đạt:\n" + tail(log, 600))
            feedback = ("Test bị lỗi. Log:\n```\n" + tail(log) + "\n```\n"
                        "Hãy sửa code (không sửa test) và gửi lại các file cần thay đổi.")

            # Trọng tài: ở vòng chẵn, nhờ kiểm thử viên xem test có sai không
            if round_no % 2 == 0 and round_no < max_rounds:
                await _log(send_event, "tester",
                           f"[{L['tester']}] Kiểm tra xem test có đúng đặc tả không...")
                arb = (f"Đặc tả:\n{spec}\n\nTest hiện tại:\n{format_project(tests_of(work))}\n\n"
                       f"Code hiện tại:\n{format_project(code_of(work))}\n\n"
                       f"Log lỗi:\n```\n{tail(log)}\n```\n\n"
                       "Hãy xác định test có đúng đặc tả không. Nếu test SAI hoặc mâu thuẫn "
                       "đặc tả: trả lại file test đã sửa (đầy đủ, đúng định dạng). Nếu test ĐÚNG "
                       "và lỗi nằm ở code: chỉ trả lời đúng một từ TEST_OK.")
                reply2, model2, notes2, usage2 = await call_role(
                    tester_m, 0,
                    [{"role": "system", "content": TESTER_PROMPT},
                     {"role": "user", "content": arb}], api_key)
                await _track_tokens("tester", model2, usage2)
                for n in notes2:
                    await _log(send_event, "tester", f"  ⚠ {n}")
                fixed_tests = tests_of(parse_files(reply2)[0])
                if fixed_tests:
                    work.update(fixed_tests)
                    write_project(workdir, work)
                    await _log(send_event, "tester",
                               f"  Test đã được sửa: {', '.join(sorted(fixed_tests))}")
                    feedback += ("\n\nLưu ý: file test đã được cập nhật cho đúng đặc tả:\n"
                                 + format_project(fixed_tests))
                else:
                    await _log(send_event, "tester",
                               "  Test đúng đặc tả, lỗi nằm ở code.")

            messages.append({"role": "user", "content": feedback})

        await _progress(send_event, "coder", cur, total_stages,
                        "done" if ok else "error")

        # ────────── 4. Reviewer ──────────
        if ok and use_reviewer:
            cur += 1
            await _progress(send_event, "reviewer", cur, total_stages)
            good = dict(work)

            for rv in range(1, REVIEW_ROUNDS + 1):
                await _log(send_event, "reviewer",
                           f"[{L['reviewer']}] Đang review ({rv}/{REVIEW_ROUNDS})...")
                reply, model, notes, usage = await call_role(
                    reviewer_m, 0,
                    [{"role": "system", "content": REVIEWER_PROMPT},
                     {"role": "user", "content":
                      f"Đặc tả:\n{spec}\n\nCode và test hiện tại:\n{format_project(work)}"}],
                    api_key)
                await _track_tokens("reviewer", model, usage)
                for n in notes:
                    await _log(send_event, "reviewer", f"  ⚠ {n}")
                verdict = reply.strip()
                if verdict.upper().startswith("LGTM"):
                    await _log(send_event, "reviewer",
                               f"  ✓ {short(model)}: không phát hiện lỗi thực sự.")
                    break
                await _log(send_event, "reviewer",
                           f"  Phát hiện vấn đề ({short(model)}):\n" + tail(verdict, 1200))

                messages.append({"role": "user", "content":
                    "Reviewer phát hiện các vấn đề sau. Hãy sửa trong code "
                    "(không sửa test), tất cả test vẫn phải qua:\n" + verdict})
                reply, model, notes, usage = await call_role(coder_m, 0, messages, api_key)
                await _track_tokens("coder", model, usage)
                for n in notes:
                    await _log(send_event, "coder", f"  ⚠ {n}")
                messages.append({"role": "assistant", "content": reply})
                fixed = code_of(parse_files(reply)[0])
                if not fixed:
                    await _log(send_event, "coder",
                               "  Lập trình viên không đưa ra bản sửa, giữ bản hiện tại.")
                    break
                work.update(fixed)
                write_project(workdir, work)
                ok2, _ = verify(workdir, work, timeout)
                if ok2:
                    good = dict(work)
                    await send_event({"type": "code", "files": work})
                    await _log(send_event, "coder",
                               f"  ✓ {short(model)} đã sửa theo review, test vẫn đạt.")
                else:
                    work = dict(good)
                    write_project(workdir, work)
                    await _log(send_event, "coder",
                               "  ✗ Bản sửa làm test fail, giữ bản trước review.")
                    break

            await _progress(send_event, "reviewer", cur, total_stages, "done")

        # ────────── Finalize ──────────
        final = work if ok else dict(project)
        write_project(workdir, final)
        zip_path = str(Path(workdir) / "project.zip")
        extras = {"SPEC.md": spec} if spec and spec != command else None
        make_zip(final, zip_path, extra=extras)

        await send_event({
            "type": "done", "ok": ok, "files": final,
            "zip_url": f"/api/download/{session_id}",
            "tokens": token_stats,
        })
        return final

    except asyncio.CancelledError:
        await _log(send_event, "system", "⏹️ Pipeline đã bị hủy.")
        raise
    except Exception as exc:
        logger.exception("Pipeline error")
        await send_event({"type": "error", "message": f"Lỗi pipeline: {exc}"})
        raise
