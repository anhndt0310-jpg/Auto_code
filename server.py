"""
server.py — FastAPI app: WebSocket control channel + REST history API.

Pipeline runs are background jobs (see jobs.py) saved to SQLite (see store.py), so:
  * closing the tab / losing the connection does NOT stop a run,
  * a client can re-attach to a live run ("attach") or continue an interrupted one ("resume"),
  * run history and chat history are available from any machine that reaches the same data.

Environment variables:
  HOST        bind address (default 127.0.0.1 — local only)
  PORT        port (default 8000)
  RELOAD      "1" enables uvicorn auto-reload (development only)
  APP_TOKEN   if set, WebSocket (?token=...) and /api/* (X-App-Token header or ?token=...)
              require it. Strongly recommended whenever HOST is not 127.0.0.1.
  DATA_DIR    where agent.db lives (default ./data)
  SANDBOX     "docker" runs generated tests inside a container (see Dockerfile.sandbox)
"""

import asyncio
import hmac
import io
import json
import logging
import os
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple

import httpx
from fastapi import (APIRouter, Depends, FastAPI, File, HTTPException, Request,
                     UploadFile, WebSocket, WebSocketDisconnect)
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from jobs import JobManager, build_snapshot
from llm import call_role
from models import RunRequest
from pipeline import CONFIG_KEYS, new_state
from store import get_store, user_id_for
from utils import format_project, make_zip_bytes, parse_models, short

logger = logging.getLogger(__name__)

APP_TOKEN = os.environ.get("APP_TOKEN", "").strip()
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))
RELOAD = os.environ.get("RELOAD", "0") == "1"

jobs = JobManager()


# ─────────────────────────── Auth helpers ───────────────────────────────────

def _check_app_token(provided: str) -> bool:
    if not APP_TOKEN:
        return True
    return hmac.compare_digest(provided or "", APP_TOKEN)


async def require_app_token(request: Request) -> None:
    token = request.headers.get("x-app-token") or request.query_params.get("token", "")
    if not _check_app_token(token):
        raise HTTPException(status_code=401, detail="Sai hoặc thiếu APP_TOKEN")


def _bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key", "").strip()


async def current_user(request: Request) -> str:
    """User id from the API key (or X-Profile sync code). The key itself is never stored."""
    secret = request.headers.get("x-profile", "").strip() or _bearer(request)
    if not secret:
        raise HTTPException(status_code=401,
                            detail="Thiếu API key (header 'Authorization: Bearer <key>')")
    return user_id_for(secret)


# ─────────────────────────── App ────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    store = get_store()
    n = await asyncio.to_thread(store.mark_interrupted)
    if n:
        logger.info("%d run(s) were interrupted by the last shutdown; they can be resumed.", n)
    if HOST not in ("127.0.0.1", "localhost", "::1") and not APP_TOKEN:
        logger.warning("Server is reachable from the network (HOST=%s) but APP_TOKEN is not "
                       "set: anyone who can reach it can run code on this machine.", HOST)
    logger.info("AI Code Agent server starting… (data: %s)", os.environ.get("DATA_DIR") or "./data")
    yield
    await jobs.shutdown()
    logger.info("Server shut down.")


app = FastAPI(title="AI Code Agent", version="3.0.0", lifespan=lifespan)

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    f = STATIC_DIR / "index.html"
    if f.is_file():
        return FileResponse(str(f), media_type="text/html")
    return JSONResponse({"error": "Frontend not found"}, status_code=404)


api = APIRouter(prefix="/api", dependencies=[Depends(require_app_token)])


# ─────────────────────────── Download ───────────────────────────────────────

@api.get("/download/{run_id}")
async def download_zip(run_id: str):
    """Zip of the run's current files. The (unguessable) run id acts as the link secret."""
    run = await asyncio.to_thread(get_store().get_run, run_id)
    if not run:
        return JSONResponse({"error": "Not found"}, status_code=404)
    state = run["state"]
    spec = state.get("spec")
    extra = {"SPEC.md": spec} if spec and spec != state.get("command") else None
    data = await asyncio.to_thread(make_zip_bytes, state.get("work") or {}, extra)
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="project.zip"'})


# ─────────────────────────── Credits ────────────────────────────────────────

@api.get("/credits")
async def get_credits(request: Request, api_key: str = ""):
    """OpenRouter balance. Send the key in 'Authorization: Bearer <key>'.
    The ?api_key= form is a legacy fallback: it leaks the key into server logs."""
    key = _bearer(request) or api_key.strip()
    if not key:
        return JSONResponse({"ok": False, "error": "Chưa có API key"}, status_code=400)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get("https://openrouter.ai/api/v1/credits",
                                    headers={"Authorization": f"Bearer {key}"})
            if resp.status_code == 200:
                d = resp.json().get("data", {})
                total = float(d.get("total_credits", 0) or 0)
                usage = float(d.get("total_usage", 0) or 0)
                return {"ok": True, "total_credits": total, "total_usage": usage,
                        "remaining": round(max(0.0, total - usage), 4)}
            return JSONResponse({"ok": False, "error": f"HTTP {resp.status_code}"},
                                status_code=resp.status_code)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


# ─────────────────────────── History (REST) ─────────────────────────────────

def _run_summary(run: dict) -> dict:
    state = run["state"]
    return {
        "run_id": run["run_id"], "title": run["title"], "status": run["status"],
        "created": run["created"], "updated": run["updated"],
        "command": state.get("command", ""), "stage": state.get("stage"),
        "ok": state.get("ok", False), "paused": state.get("paused"),
        "round": (state.get("coder") or {}).get("round", 0),
        "spec": state.get("spec"), "files": state.get("work") or {},
        "tokens": state.get("tokens"), "config": state.get("config"),
        "live": jobs.get(run["run_id"]) is not None,
    }


@api.get("/runs")
async def list_runs(user_id: str = Depends(current_user)):
    runs = await asyncio.to_thread(get_store().list_runs, user_id)
    for r in runs:
        r["live"] = jobs.get(r["run_id"]) is not None
    return {"ok": True, "runs": runs}


@api.get("/runs/{run_id}")
async def get_run(run_id: str, user_id: str = Depends(current_user)):
    run = await asyncio.to_thread(get_store().get_run, run_id, user_id)
    if not run:
        raise HTTPException(status_code=404, detail="Không tìm thấy run")
    return {"ok": True, **_run_summary(run)}


@api.delete("/runs/{run_id}")
async def delete_run(run_id: str, user_id: str = Depends(current_user)):
    job = jobs.get(run_id)
    if job and job.running:
        raise HTTPException(status_code=409, detail="Run đang chạy, hãy dừng trước khi xóa")
    ok = await asyncio.to_thread(get_store().delete_run, run_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Không tìm thấy run")
    return {"ok": True}


@api.get("/conversations")
async def list_conversations(user_id: str = Depends(current_user)):
    convs = await asyncio.to_thread(get_store().list_conversations, user_id)
    return {"ok": True, "conversations": convs}


@api.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str, user_id: str = Depends(current_user)):
    conv = await asyncio.to_thread(get_store().get_conversation, conv_id, user_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Không tìm thấy cuộc trò chuyện")
    return {"ok": True, **conv}


@api.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str, user_id: str = Depends(current_user)):
    ok = await asyncio.to_thread(get_store().delete_conversation, conv_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Không tìm thấy cuộc trò chuyện")
    return {"ok": True}


@api.get("/export")
async def export_history(user_id: str = Depends(current_user)):
    """Full backup of this user's runs + conversations (JSON). Use /api/import elsewhere."""
    data = await asyncio.to_thread(get_store().export_user, user_id)
    return Response(content=json.dumps(data, ensure_ascii=False), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="agent_history.json"'})


@api.post("/import")
async def import_history(request: Request, user_id: str = Depends(current_user)):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Body không phải JSON hợp lệ")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Định dạng không đúng")
    counts = await asyncio.to_thread(get_store().import_user, user_id, data)
    return {"ok": True, **counts}


# ─────────────────────────── Upload ─────────────────────────────────────────

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_ZIP_ENTRIES = 500
MAX_ZIP_TOTAL = 20 * 1024 * 1024
SKIP_PARTS = {"__MACOSX", "__pycache__", ".git", "node_modules", ".venv", "venv"}


def _add_text(extracted: Dict[str, str], skipped: List[str], path_name: str, raw: bytes) -> None:
    p = PurePosixPath(path_name.replace("\\", "/"))
    if any(part in SKIP_PARTS for part in p.parts) or not p.name:
        return
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        skipped.append(p.name)
        return
    name = p.name
    if name in extracted:                      # same base name in different folders
        base = "__".join(p.parts[-2:]) if len(p.parts) > 1 else name
        name, i = base, 2
        while name in extracted:
            name, i = f"{i}_{base}", i + 1
    extracted[name] = text


def _extract(filename: str, content: bytes) -> Tuple[Dict[str, str], List[str]]:
    extracted: Dict[str, str] = {}
    skipped: List[str] = []
    if filename.lower().endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                infos = [i for i in z.infolist() if not i.is_dir()]
                if len(infos) > MAX_ZIP_ENTRIES or sum(i.file_size for i in infos) > MAX_ZIP_TOTAL:
                    skipped.append(f"{filename} (zip quá lớn)")
                    return extracted, skipped
                for info in infos:
                    if info.file_size > MAX_FILE_BYTES:
                        skipped.append(PurePosixPath(info.filename).name)
                        continue
                    with z.open(info) as fh:
                        raw = fh.read(MAX_FILE_BYTES + 1)   # don't trust the declared size
                    if len(raw) > MAX_FILE_BYTES:
                        skipped.append(PurePosixPath(info.filename).name)
                        continue
                    _add_text(extracted, skipped, info.filename, raw)
        except zipfile.BadZipFile as e:
            logger.warning("Error reading zip %s: %s", filename, e)
            skipped.append(f"{filename} (zip hỏng)")
    else:
        if len(content) > MAX_FILE_BYTES:
            skipped.append(f"{filename} (quá lớn)")
        else:
            _add_text(extracted, skipped, filename, content)
    return extracted, skipped


@api.post("/upload")
async def upload_files(files: List[UploadFile] = File(...)):
    """Upload files or zip archives and extract text contents."""
    extracted: Dict[str, str] = {}
    skipped: List[str] = []
    for f in files:
        content = await f.read(MAX_UPLOAD_BYTES + 1)
        filename = f.filename or "file.txt"
        if len(content) > MAX_UPLOAD_BYTES:
            skipped.append(f"{filename} (quá lớn)")
            continue
        got, skip = await asyncio.to_thread(_extract, filename, content)
        for name, text in got.items():
            key, i = name, 2
            while key in extracted:
                key, i = f"{i}_{name}", i + 1
            extracted[key] = text
        skipped.extend(skip)
    return {"ok": True, "files": extracted, "skipped": skipped}


app.include_router(api)


# ─────────────────────────── WebSocket ──────────────────────────────────────

class Session:
    __slots__ = ("session_id", "api_key", "profile", "user_id",
                 "job_id", "sub", "chat_task", "conv_id")

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.api_key = ""
        self.profile = ""
        self.user_id: Optional[str] = None
        self.job_id: Optional[str] = None      # run this socket is attached to
        self.sub = None                        # its jobs.Subscriber
        self.chat_task: Optional[asyncio.Task] = None
        self.conv_id: Optional[str] = None     # current conversation of this socket

    def identify(self, msg: dict) -> bool:
        key = (msg.get("api_key") or "").strip()
        prof = (msg.get("profile") or "").strip()
        if key:
            self.api_key = key
        if prof:
            self.profile = prof
        secret = self.profile or self.api_key
        self.user_id = user_id_for(secret) if secret else None
        return self.user_id is not None


def _as_int(value, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    if not _check_app_token(ws.query_params.get("token", "")):
        await ws.close(code=4401)
        return
    await ws.accept()
    session = Session(uuid.uuid4().hex[:12])
    store = get_store()
    logger.info("WS connected: %s", session.session_id)

    async def push(event: dict) -> None:           # raises when the socket is gone
        await ws.send_json(event)

    async def send_event(event: dict) -> None:     # best effort
        try:
            await push(event)
        except Exception:
            pass

    def detach() -> None:
        job = jobs.get(session.job_id)
        if job:
            job.detach(session.sub)
        session.sub = None

    def attach_job(job, after_seq: int = 0) -> None:
        detach()
        session.job_id = job.run_id
        session.sub = job.attach(push, after_seq)

    async def send_history() -> None:
        runs = await asyncio.to_thread(store.list_runs, session.user_id)
        for r in runs:
            r["live"] = jobs.get(r["run_id"]) is not None
        convs = await asyncio.to_thread(store.list_conversations, session.user_id)
        await send_event({"type": "history", "runs": runs, "conversations": convs})

    try:
        await send_event({"type": "session", "session_id": session.session_id})

        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await send_event({"type": "error", "message": "Invalid JSON"})
                continue
            if not isinstance(msg, dict):
                await send_event({"type": "error", "message": "Invalid message"})
                continue

            msg_type = msg.get("type", "")

            # ── identify / history ───────────────────────────────────────
            if msg_type in ("identify", "history"):
                if not session.identify(msg):
                    await send_event({"type": "error", "message": "Vui lòng nhập API key."})
                    continue
                await send_history()

            # ── stop ─────────────────────────────────────────────────────
            elif msg_type == "stop":
                session.identify(msg)
                rid = msg.get("run_id") or session.job_id
                job = jobs.get(rid)
                if job and job.running and (session.user_id in (None, job.user_id)):
                    await jobs.stop(rid)        # the job logs "đã dừng" itself
                else:
                    await send_event({"type": "log", "role": "system",
                                      "message": "Không có tiến trình nào đang chạy."})

            # ── reset ────────────────────────────────────────────────────
            elif msg_type == "reset":
                if session.job_id:
                    await jobs.stop(session.job_id)
                detach()
                session.job_id = None
                session.conv_id = None
                if session.chat_task and not session.chat_task.done():
                    session.chat_task.cancel()
                await send_event({"type": "log", "role": "system",
                                  "message": "🔄 Đã reset session."})

            # ── run (new) ────────────────────────────────────────────────
            elif msg_type == "run":
                command = (msg.get("command") or "").strip()
                if not command:
                    await send_event({"type": "error", "message": "Vui lòng nhập yêu cầu."})
                    continue
                if not (msg.get("api_key") or "").strip():
                    await send_event({"type": "error", "message": "Vui lòng nhập API key."})
                    continue
                session.identify(msg)
                try:
                    req = RunRequest.model_validate({**msg, "command": command})
                except ValidationError as exc:
                    await send_event({"type": "error", "message": f"Tham số không hợp lệ: {exc}"})
                    continue

                if session.job_id:                      # this socket's previous run: pause it
                    await jobs.stop(session.job_id)
                detach()

                run_id = uuid.uuid4().hex
                state = new_state(run_id, command, req.project_text,
                                  {k: getattr(req, k) for k in CONFIG_KEYS})
                await asyncio.to_thread(store.create_run, run_id, session.user_id, command,
                                        json.dumps(state, ensure_ascii=False))
                job = await jobs.start(run_id, session.user_id, state, session.api_key)
                await send_event({"type": "run_started", "run_id": run_id})
                attach_job(job, 0)

            # ── attach (re-connect to a live or finished run) ────────────
            elif msg_type == "attach":
                if not session.identify(msg):
                    await send_event({"type": "error", "message": "Vui lòng nhập API key."})
                    continue
                rid = msg.get("run_id") or ""
                after = _as_int(msg.get("after_seq"), 0) or 0
                job = jobs.get(rid)
                if job and job.user_id == session.user_id:
                    attach_job(job, after)
                    continue
                run = await asyncio.to_thread(store.get_run, rid, session.user_id)
                if not run:
                    await send_event({"type": "error", "message": "Không tìm thấy run."})
                    continue
                detach()
                session.job_id = None
                for ev in build_snapshot(rid, run["status"], run["state"]):
                    await send_event(ev)
                for ev in await asyncio.to_thread(store.get_events, rid, after):
                    await send_event(ev)
                await send_event({"type": "run_status", "run_id": rid, "status": run["status"]})

            # ── resume (continue an interrupted / paused / failed run) ───
            elif msg_type == "resume":
                if not session.identify(msg) or not session.api_key:
                    await send_event({"type": "error", "message": "Vui lòng nhập API key."})
                    continue
                rid = msg.get("run_id") or ""
                job = jobs.get(rid)
                if job and job.running and job.user_id == session.user_id:
                    attach_job(job, _as_int(msg.get("after_seq"), job.seq) or 0)
                    await send_event({"type": "log", "role": "system",
                                      "message": "Run đang chạy, đã kết nối lại."})
                    continue
                run = await asyncio.to_thread(store.get_run, rid, session.user_id)
                if not run:
                    await send_event({"type": "error", "message": "Không tìm thấy run."})
                    continue
                state = run["state"]
                if state.get("stage") == "done" and state.get("ok"):
                    await send_event({"type": "error", "message": "Run này đã hoàn tất."})
                    continue

                cfg = state["config"]
                if msg.get("token_budget") is not None:
                    cfg["token_budget"] = max(0, _as_int(msg["token_budget"], cfg["token_budget"]))
                if "hard_budget" in msg:
                    cfg["hard_budget"] = bool(msg["hard_budget"])
                wanted_rounds = _as_int(msg.get("max_rounds"))
                if wanted_rounds:
                    cfg["max_rounds"] = max(1, min(20, wanted_rounds))
                state["paused"] = None
                state.pop("budget_warned", None)

                if state.get("stage") == "done" and not state.get("ok"):
                    # finished without passing the tests: reopen the debug loop
                    done_rounds = state["coder"]["round"]
                    cfg["max_rounds"] = (cfg["max_rounds"] if wanted_rounds and
                                         cfg["max_rounds"] > done_rounds else done_rounds + 3)
                    state["stage"] = "coder"
                    state["review"] = {"done": 0, "good": None}

                after = _as_int(msg.get("after_seq"))
                job = await jobs.start(rid, session.user_id, state, session.api_key)
                await send_event({"type": "run_started", "run_id": rid, "resumed": True})
                attach_job(job, job.seq if after is None else after)

            # ── chat ─────────────────────────────────────────────────────
            elif msg_type == "chat":
                if session.chat_task and not session.chat_task.done():
                    session.chat_task.cancel()
                user_msg = (msg.get("message") or "").strip()
                if not user_msg:
                    continue
                if not (session.identify(msg)) or not session.api_key:
                    await send_event({"type": "error", "message": "Vui lòng nhập API key."})
                    continue
                api_key = session.api_key
                user_id = session.user_id

                models = parse_models(msg.get("default_models", "")) or ["deepseek/deepseek-chat"]
                files_dict = msg.get("files")
                files_dict = files_dict if isinstance(files_dict, dict) else {}

                conv_id = msg.get("conversation_id") or session.conv_id
                conv = None
                if conv_id:
                    conv = await asyncio.to_thread(store.get_conversation, conv_id, user_id, False)
                if conv is None:
                    conv_id = await asyncio.to_thread(
                        store.create_conversation, user_id, user_msg[:60], files_dict)
                    old_history = msg.get("history") or []
                    for h in old_history:           # client-side history of a brand-new conversation
                        if isinstance(h, dict) and h.get("content"):
                            await asyncio.to_thread(store.add_message, conv_id,
                                                    h.get("role", "user"), str(h["content"]))
                    history = await asyncio.to_thread(store.recent_messages, conv_id, 40)
                else:
                    if files_dict and files_dict != conv["files"]:
                        await asyncio.to_thread(store.set_conversation_files, conv_id, files_dict)
                    else:
                        files_dict = conv["files"]
                    history = await asyncio.to_thread(store.recent_messages, conv_id, 40)
                session.conv_id = conv_id

                # keep the prompt bounded: drop oldest turns beyond ~60k characters
                total = sum(len(h["content"]) for h in history)
                while history and total > 60000:
                    total -= len(history.pop(0)["content"])

                await asyncio.to_thread(store.add_message, conv_id, "user", user_msg)
                await send_event({"type": "conversation", "conversation_id": conv_id})

                files_context = ""
                if files_dict:
                    files_context = ("\n\nCác file đính kèm/dự án hiện có để bạn tham khảo:\n"
                                     f"{format_project(files_dict)}")
                system_prompt = (
                    "Bạn là trợ lý AI thông minh, nhiệt tình và am hiểu lập trình. "
                    "Hãy trò chuyện và giải đáp các thắc mắc của người dùng bằng tiếng Việt một cách tự nhiên. "
                    "Nếu người dùng chỉ trò chuyện thông thường, chào hỏi hoặc nhờ giải thích/tư vấn tài liệu/file, "
                    "hãy trả lời rõ ràng, chi tiết, giải thích dễ hiểu mà KHÔNG tự tiện sinh toàn bộ mã nguồn hoặc tạo file test khi không được yêu cầu. "
                    "Nếu người dùng nhờ phân tích hay tìm lỗi trong các file đính kèm, hãy chỉ rõ lỗi và giải thích cách khắc phục."
                )
                messages = [{"role": "system", "content": system_prompt + files_context}]
                messages += [{"role": h["role"], "content": h["content"]} for h in history]
                messages.append({"role": "user", "content": user_msg})

                await send_event({"type": "log", "role": "system",
                                  "message": f"💬 Đang gửi tin nhắn tới {short(models[0])}..."})

                async def _chat_task(messages=messages, models=models, api_key=api_key,
                                     conv_id=conv_id) -> None:
                    # Not tied to the socket: the reply is saved even if the client disconnects.
                    try:
                        reply, model, notes, usage = await call_role(models, 0, messages, api_key)
                        await asyncio.to_thread(store.add_message, conv_id, "assistant",
                                                reply, short(model))
                        await send_event({"type": "chat_reply", "content": reply,
                                          "model": short(model), "usage": usage,
                                          "conversation_id": conv_id})
                        await send_event({"type": "log", "role": "system",
                                          "message": f"✓ {short(model)} đã phản hồi. "
                                                     f"(Dùng {usage.get('total_tokens', 0):,} tokens)"})
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.exception("Chat error")
                        await send_event({"type": "error", "message": f"Lỗi chat: {e}"})

                session.chat_task = asyncio.create_task(_chat_task())

            else:
                await send_event({"type": "error", "message": f"Unknown type: {msg_type}"})

    except WebSocketDisconnect:
        logger.info("WS disconnected: %s", session.session_id)
    except Exception:
        logger.exception("WS error: %s", session.session_id)
    finally:
        # Disconnecting must NOT stop a run — it keeps going in the background.
        detach()


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    uvicorn.run("server:app", host=HOST, port=PORT, reload=RELOAD, log_level="info")
