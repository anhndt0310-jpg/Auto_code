"""
pipeline.py — Resumable async pipeline: planner → tester → coder → reviewer.

The whole run lives in one JSON-serialisable ``state`` dict. After every meaningful step
the pipeline calls ``checkpoint(state)``; calling ``run_pipeline`` again with a saved state
continues from the stored stage/round instead of starting over:

    stage "planner"  -> "tester" -> "coder" (round N of M) -> "reviewer" (pass K) -> "done"

Events are sent through an async ``send_event`` callback (same contract as before).
No API key is ever stored in ``state``.
"""

import asyncio
import copy
import logging
from typing import Awaitable, Callable, Dict, List, Tuple

from models import (
    CODER_PROMPT, FALLBACK_MODEL, MIN_TEST_KEEP_RATIO, PLANNER_PROMPT,
    REVIEW_ROUNDS, REVIEWER_PROMPT, ROLE_LABEL, TESTER_PROMPT,
    TOKEN_BUDGET_DEFAULT,
)
from llm import call_role
from utils import (
    code_of, count_test_metrics, format_project, parse_files, parse_models,
    short, tail, tests_of, verify, write_project,
)

logger = logging.getLogger(__name__)

SendEvent = Callable[[dict], Awaitable[None]]
Checkpoint = Callable[[dict], Awaitable[None]]

CONFIG_KEYS = (
    "default_models", "planner_models", "tester_models", "coder_models",
    "reviewer_models", "use_planner", "use_reviewer", "max_rounds",
    "token_budget", "hard_budget",
)


class BudgetExceeded(Exception):
    """Raised before an LLM call when the token budget is used up (run is paused, not lost)."""


# ─────────────────────────── State ──────────────────────────────────────────

def _empty_tokens(token_budget: int) -> dict:
    def role() -> dict:
        return {"prompt": 0, "completion": 0, "total": 0, "calls": 0,
                "model": "", "pct": 0.0, "share_pct": 0.0}
    return {
        "prompt": 0, "completion": 0, "total": 0,
        "token_budget": token_budget, "percent_used": 0.0,
        "by_role": {r: role() for r in ("planner", "tester", "coder", "reviewer")},
    }


def new_state(run_id: str, command: str, project_text: str, config: dict) -> dict:
    """Fresh state for a new run. *config* holds only the CONFIG_KEYS (no API key)."""
    project: Dict[str, str] = {}
    if project_text:
        parsed, _ = parse_files(project_text)
        project = parsed
    cfg = {k: config.get(k) for k in CONFIG_KEYS}
    cfg["token_budget"] = int(cfg.get("token_budget") or TOKEN_BUDGET_DEFAULT)
    return {
        "version": 1,
        "run_id": run_id,
        "command": command,
        "config": cfg,
        "project": project,          # files the user started with
        "work": dict(project),       # current files (tests + code)
        "spec": None,
        "stage": "planner",
        "ok": False,
        "paused": None,              # "budget" when stopped by the token budget
        "coder": {"round": 0, "fail_count": 0, "feedback": ""},
        "review": {"done": 0, "good": None},
        "tokens": _empty_tokens(cfg["token_budget"]),
        "last": {"progress": None, "tokens": None},   # replayed to reconnecting clients
    }


def _resolve_models(role_str: str, defaults: List[str]) -> List[str]:
    specific = parse_models(role_str)
    return specific if specific else (defaults if defaults else [FALLBACK_MODEL])


async def _log(send_event: SendEvent, role: str, message: str) -> None:
    await send_event({"type": "log", "role": role, "message": message})


def _accept_test_update(old: Dict[str, str], new: Dict[str, str]) -> bool:
    """Reject arbiter 'fixes' that gut the tests (drop too many test functions/asserts)."""
    shared_old = {n: old[n] for n in new if n in old}
    if not shared_old:
        return True
    shared_new = {n: new[n] for n in shared_old}
    ot, oa = count_test_metrics(shared_old)
    nt, na = count_test_metrics(shared_new)
    return nt >= ot * MIN_TEST_KEEP_RATIO and na >= oa * MIN_TEST_KEEP_RATIO


# ─────────────────────────── Pipeline ───────────────────────────────────────

async def run_pipeline(
    state: dict,
    api_key: str,
    send_event: SendEvent,
    checkpoint: Checkpoint,
    workdir: str,
    timeout: int = 30,
) -> Dict[str, str]:
    """
    Run (or continue) the multi-agent coding pipeline described by *state*.
    Mutates *state* in place and returns the final files dict.
    """
    cfg = state["config"]
    L = ROLE_LABEL
    run_id = state["run_id"]
    command = state["command"]
    project = state["project"]
    work = state["work"]            # mutated in place everywhere (never rebound)
    tokens = state["tokens"]
    coder_st = state["coder"]

    default_models = parse_models(cfg.get("default_models") or "") or [FALLBACK_MODEL]
    planner_m = _resolve_models(cfg.get("planner_models") or "", default_models)
    tester_m = _resolve_models(cfg.get("tester_models") or "", default_models)
    coder_m = _resolve_models(cfg.get("coder_models") or "", default_models)
    reviewer_m = _resolve_models(cfg.get("reviewer_models") or "", default_models)

    use_planner = bool(cfg.get("use_planner", True))
    use_reviewer = bool(cfg.get("use_reviewer", True))
    max_rounds = int(cfg.get("max_rounds") or 5)
    budget = int(cfg.get("token_budget") or 0)
    hard_budget = bool(cfg.get("hard_budget", True))
    tokens["token_budget"] = budget

    total_stages = 2 + int(use_planner) + int(use_reviewer)
    step_of = {"planner": 1, "tester": 1 + int(use_planner),
               "coder": 2 + int(use_planner), "reviewer": 3 + int(use_planner)}

    # ── small helpers ───────────────────────────────────────────────────────
    async def progress(stage: str, step: int, total: int, status: str = "running") -> None:
        ev = {"type": "progress", "stage": stage, "step": step,
              "total": total, "status": status}
        state["last"]["progress"] = ev
        await send_event(ev)

    def budget_info(tokens_used: int) -> str:
        if budget > 0:
            pct = round((tokens_used / budget) * 100, 1)
            return f"{tokens_used:,} tokens ({pct}% budget)"
        return f"{tokens_used:,} tokens"

    async def track(role: str, model: str, usage: dict) -> None:
        p = usage.get("prompt_tokens", 0) or 0
        c = usage.get("completion_tokens", 0) or 0
        t = usage.get("total_tokens", 0) or (p + c)

        tokens["prompt"] += p
        tokens["completion"] += c
        tokens["total"] += t
        tot = tokens["total"]
        overall_pct = round((tot / budget) * 100, 1) if budget > 0 else 0.0
        tokens["percent_used"] = overall_pct

        if role in tokens["by_role"]:
            r = tokens["by_role"][role]
            r["prompt"] += p
            r["completion"] += c
            r["total"] += t
            r["calls"] += 1
            r["model"] = short(model)

        for rv in tokens["by_role"].values():
            rv["pct"] = round((rv["total"] / budget) * 100, 1) if budget > 0 else 0.0
            rv["share_pct"] = round((rv["total"] / tot) * 100, 1) if tot > 0 else 0.0

        ev = {
            "type": "tokens", "role": role, "model": short(model),
            "step_usage": {"prompt": p, "completion": c, "total": t},
            "stats": tokens, "token_budget": budget, "percent_used": overall_pct,
        }
        state["last"]["tokens"] = copy.deepcopy(ev)
        await send_event(ev)

    async def llm(role: str, models: List[str], start: int,
                  msgs: List[dict]) -> Tuple[str, str, dict]:
        """One LLM call: budget guard before, token accounting + rotation notes after."""
        if budget > 0 and tokens["total"] >= budget:
            if hard_budget:
                raise BudgetExceeded()
            if not state.get("budget_warned"):
                state["budget_warned"] = True
                await _log(send_event, "system",
                           f"⚠ Đã vượt ngân sách {budget:,} tokens (chế độ chỉ cảnh báo).")
        reply, model, notes, usage = await call_role(models, start, msgs, api_key)
        await track(role, model, usage)
        for n in notes:
            await _log(send_event, role, f"  ⚠ {n}")
        return reply, model, usage

    def coder_messages(feedback: str) -> List[dict]:
        """Fresh, bounded prompt: spec + tests + current code + latest feedback only."""
        user = (f"Đặc tả:\n{state['spec']}\n\nCác file test (chuẩn, không được sửa):\n"
                f"{format_project(tests_of(work))}\n")
        current = code_of(work)
        if current:
            user += f"\nCode hiện tại:\n{format_project(current)}\n"
        user += "\n" + (feedback or "Hãy viết hoặc sửa các file code để toàn bộ test qua.")
        return [{"role": "system", "content": CODER_PROMPT},
                {"role": "user", "content": user}]

    async def write(files: Dict[str, str]) -> None:
        await asyncio.to_thread(write_project, workdir, files)

    # ── run ─────────────────────────────────────────────────────────────────
    try:
        # ────────── 1. Planner ──────────
        if state["stage"] == "planner":
            if use_planner:
                await progress("planner", step_of["planner"], total_stages)
                await _log(send_event, "planner", f"[{L['planner']}] Đang lập đặc tả...")

                user = f"Yêu cầu của người dùng:\n{command}"
                if project:
                    user = f"Dự án hiện tại:\n{format_project(project)}\n\n" + user

                reply, model, usage = await llm(
                    "planner", planner_m, 0,
                    [{"role": "system", "content": PLANNER_PROMPT},
                     {"role": "user", "content": user}])
                state["spec"] = reply.strip() or command
                await _log(send_event, "planner",
                           f"  ✓ {short(model)} đã lập xong đặc tả. "
                           f"(Dùng {budget_info(usage.get('total_tokens', 0))})")
                await send_event({"type": "spec", "content": state["spec"]})
                await progress("planner", step_of["planner"], total_stages, "done")
            else:
                state["spec"] = command
                await _log(send_event, "system",
                           "Bỏ qua bước lập kế hoạch (dùng thẳng yêu cầu làm đặc tả).")
                await send_event({"type": "spec", "content": state["spec"]})
            state["stage"] = "tester"
            await checkpoint(state)

        spec = state["spec"] or command

        # ────────── 2. Tester ──────────
        if state["stage"] == "tester":
            await progress("tester", step_of["tester"], total_stages)
            await _log(send_event, "tester",
                       f"[{L['tester']}] Đang viết test độc lập từ đặc tả...")

            user = f"Đặc tả:\n{spec}\n"
            old_tests = tests_of(work)
            if old_tests:
                user += ("\nCác file test hiện có (cập nhật và bổ sung cho khớp đặc tả, "
                         "giữ lại test cũ còn đúng):\n" + format_project(old_tests) + "\n")

            tester_msgs = [{"role": "system", "content": TESTER_PROMPT},
                           {"role": "user", "content": user}]
            new_tests: Dict[str, str] = {}
            model, usage = "", {}
            for _attempt in range(2):
                reply, model, usage = await llm("tester", tester_m, 0, tester_msgs)
                new_tests = tests_of(parse_files(reply)[0])
                if new_tests:
                    break
                tester_msgs.append({"role": "assistant", "content": reply})
                tester_msgs.append({"role": "user", "content":
                                    "Chưa có file test_*.py hợp lệ. Hãy gửi lại đúng định dạng."})

            if not new_tests:
                raise RuntimeError("Kiểm thử viên không trả về file test_*.py hợp lệ.")

            work.update(new_tests)
            await write(work)
            await _log(send_event, "tester",
                       f"  ✓ {short(model)} đã viết: {', '.join(sorted(new_tests))} "
                       f"(Dùng {budget_info(usage.get('total_tokens', 0))})")
            await send_event({"type": "code", "files": work})
            await progress("tester", step_of["tester"], total_stages, "done")
            state["stage"] = "coder"
            await checkpoint(state)

        # ────────── 3. Coder (debug loop, resumes at the saved round) ──────────
        if state["stage"] == "coder":
            await progress("coder", step_of["coder"], total_stages)

            while not state["ok"] and coder_st["round"] < max_rounds:
                round_no = coder_st["round"] + 1
                await _log(send_event, "coder",
                           f"[{L['coder']}] Vòng {round_no}/{max_rounds}: viết/sửa code...")
                await progress("coder", round_no, max_rounds)

                reply, model, usage = await llm(
                    "coder", coder_m, coder_st["fail_count"],
                    coder_messages(coder_st["feedback"]))

                files_parsed, rejected = parse_files(reply)
                if rejected:
                    await _log(send_event, "coder",
                               f"  ⚠ Bỏ qua tên file không hợp lệ: {', '.join(rejected)}")
                code_files = code_of(files_parsed)
                if len(files_parsed) > len(code_files):
                    await _log(send_event, "coder",
                               "  ⚠ Bỏ qua file test do lập trình viên gửi (test là chuẩn).")
                if not code_files:
                    coder_st["fail_count"] += 1
                    coder_st["round"] = round_no
                    coder_st["feedback"] = (
                        "Không đọc được file code nào. Hãy gửi lại theo đúng định dạng "
                        "'### FILE: ten_file.py' rồi khối ```python.")
                    await _log(send_event, "coder",
                               "  ✗ Không có file code hợp lệ, yêu cầu gửi lại.")
                    await checkpoint(state)
                    continue

                work.update(code_files)
                await write(work)
                await send_event({"type": "code", "files": work})
                await _log(send_event, "coder",
                           f"  Nhận code từ {short(model)}, đang chạy test... "
                           f"(Dùng {budget_info(usage.get('total_tokens', 0))})")

                ok, log = await asyncio.to_thread(verify, workdir, work, timeout)
                coder_st["round"] = round_no
                if ok:
                    state["ok"] = True
                    coder_st["feedback"] = ""
                    await _log(send_event, "coder", f"  ✓ Test đạt ở vòng {round_no}.")
                    await checkpoint(state)
                    break

                coder_st["fail_count"] += 1
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
                    reply2, _m2, _u2 = await llm(
                        "tester", tester_m, 0,
                        [{"role": "system", "content": TESTER_PROMPT},
                         {"role": "user", "content": arb}])
                    fixed_tests = tests_of(parse_files(reply2)[0])
                    if fixed_tests and not _accept_test_update(tests_of(work), fixed_tests):
                        await _log(send_event, "tester",
                                   "  ⚠ Từ chối bản sửa test: làm giảm quá nhiều test/assert "
                                   "so với bản cũ. Giữ test hiện tại.")
                        fixed_tests = {}
                    if fixed_tests:
                        work.update(fixed_tests)
                        await write(work)
                        await _log(send_event, "tester",
                                   f"  Test đã được sửa: {', '.join(sorted(fixed_tests))}")
                        feedback += ("\n\nLưu ý: file test đã được cập nhật cho đúng đặc tả "
                                     "(xem phần test ở trên).")
                    else:
                        await _log(send_event, "tester",
                                   "  Test đúng đặc tả, lỗi nằm ở code.")

                coder_st["feedback"] = feedback
                await checkpoint(state)

            await progress("coder", step_of["coder"], total_stages,
                           "done" if state["ok"] else "error")
            state["stage"] = "reviewer" if (state["ok"] and use_reviewer) else "done"
            await checkpoint(state)

        # ────────── 4. Reviewer ──────────
        if state["stage"] == "reviewer":
            rv_st = state["review"]
            if rv_st["good"] is None:
                rv_st["good"] = dict(work)
            await progress("reviewer", step_of["reviewer"], total_stages)

            while rv_st["done"] < REVIEW_ROUNDS:
                rv = rv_st["done"] + 1
                await _log(send_event, "reviewer",
                           f"[{L['reviewer']}] Đang review ({rv}/{REVIEW_ROUNDS})...")
                reply, model, usage = await llm(
                    "reviewer", reviewer_m, 0,
                    [{"role": "system", "content": REVIEWER_PROMPT},
                     {"role": "user", "content":
                      f"Đặc tả:\n{spec}\n\nCode và test hiện tại:\n{format_project(work)}"}])
                verdict = reply.strip()
                if verdict.upper().startswith("LGTM"):
                    await _log(send_event, "reviewer",
                               f"  ✓ {short(model)}: không phát hiện lỗi thực sự.")
                    rv_st["done"] = REVIEW_ROUNDS
                    break
                await _log(send_event, "reviewer",
                           f"  Phát hiện vấn đề ({short(model)}):\n" + tail(verdict, 1200))

                reply, model, usage = await llm(
                    "coder", coder_m, 0,
                    coder_messages("Reviewer phát hiện các vấn đề sau. Hãy sửa trong code "
                                   "(không sửa test), tất cả test vẫn phải qua:\n" + verdict))
                fixed = code_of(parse_files(reply)[0])
                if not fixed:
                    await _log(send_event, "coder",
                               "  Lập trình viên không đưa ra bản sửa, giữ bản hiện tại.")
                    rv_st["done"] = REVIEW_ROUNDS
                    break

                work.update(fixed)
                ok2, _ = await asyncio.to_thread(verify, workdir, work, timeout)
                if ok2:
                    rv_st["good"] = dict(work)
                    rv_st["done"] = rv
                    await send_event({"type": "code", "files": work})
                    await _log(send_event, "coder",
                               f"  ✓ {short(model)} đã sửa theo review, test vẫn đạt.")
                    await checkpoint(state)
                else:
                    work.clear()
                    work.update(rv_st["good"])
                    await write(work)
                    await _log(send_event, "coder",
                               "  ✗ Bản sửa làm test fail, giữ bản trước review.")
                    rv_st["done"] = REVIEW_ROUNDS
                    break

            await progress("reviewer", step_of["reviewer"], total_stages, "done")
            state["stage"] = "done"
            await checkpoint(state)

        # ────────── Finalize ──────────
        final = dict(work)
        state["stage"] = "done"
        await checkpoint(state)

        payload = {
            "type": "done", "ok": state["ok"], "files": final,
            "zip_url": f"/api/download/{run_id}", "run_id": run_id,
            "tokens": tokens,
        }
        if not state["ok"]:
            # Code dở dang của Coder được giữ lại (không mất công); bản gốc của bạn ở đây:
            payload["original_files"] = project
        await send_event(payload)
        return final

    except BudgetExceeded:
        state["paused"] = "budget"
        await _log(send_event, "system",
                   f"⏸ Đã dùng {tokens['total']:,}/{budget:,} tokens. Tiến độ đã được lưu — "
                   "hãy Tiếp tục (kèm ngân sách lớn hơn) để chạy tiếp từ chỗ này.")
        await checkpoint(state)
        # Gửi 'done' với paused=True để giao diện cũ cũng kết thúc gọn và vẫn tải được code.
        await send_event({
            "type": "done", "ok": False, "paused": True, "reason": "budget",
            "files": dict(work), "zip_url": f"/api/download/{run_id}",
            "run_id": run_id, "tokens": tokens,
        })
        return dict(work)

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Pipeline error")
        await send_event({"type": "error", "message": f"Lỗi pipeline: {exc}"})
        raise
