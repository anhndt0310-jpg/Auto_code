"""
server.py — FastAPI app with WebSocket, session management, file downloads.
"""

import asyncio
import json
import logging
import tempfile
import uuid
import io
import zipfile
from pathlib import Path
from typing import Dict, Optional, List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from pipeline import run_pipeline
from llm import call_llm
from utils import format_project, parse_models, short

logger = logging.getLogger(__name__)


class Session:
    __slots__ = ("session_id", "files", "task", "workdir", "zip_path", "api_key")

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.files: Dict[str, str] = {}
        self.task: Optional[asyncio.Task] = None
        self.workdir = tempfile.mkdtemp(prefix=f"session_{session_id}_")
        self.zip_path: Optional[str] = None
        self.api_key = ""

    def cancel_task(self):
        if self.task and not self.task.done():
            self.task.cancel()
            self.task = None

    def reset(self):
        self.cancel_task()
        self.files = {}
        self.zip_path = None


sessions: Dict[str, Session] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("AI Code Agent server starting…")
    yield
    for s in sessions.values():
        s.cancel_task()
    sessions.clear()
    logger.info("Server shut down.")


app = FastAPI(title="AI Code Agent", version="2.0.0", lifespan=lifespan)

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    f = STATIC_DIR / "index.html"
    if f.is_file():
        return FileResponse(str(f), media_type="text/html")
    return JSONResponse({"error": "Frontend not found"}, status_code=404)


@app.get("/api/download/{session_id}")
async def download_zip(session_id: str):
    session = sessions.get(session_id)
    if not session or not session.zip_path:
        return JSONResponse({"error": "Not found"}, status_code=404)
    p = Path(session.zip_path)
    if not p.is_file():
        return JSONResponse({"error": "Zip not ready"}, status_code=404)
    return FileResponse(str(p), media_type="application/zip", filename="project.zip")


@app.get("/api/credits")
async def get_credits(api_key: str):
    """Fetch OpenRouter account credits and remaining balance."""
    if not api_key:
        return JSONResponse({"ok": False, "error": "Chưa có API key"}, status_code=400)

    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://openrouter.ai/api/v1/credits",
                headers={"Authorization": f"Bearer {api_key}"}
            )
            if resp.status_code == 200:
                d = resp.json().get("data", {})
                total = float(d.get("total_credits", 0) or 0)
                usage = float(d.get("total_usage", 0) or 0)
                remaining = max(0.0, total - usage)
                return {
                    "ok": True,
                    "total_credits": total,
                    "total_usage": usage,
                    "remaining": round(remaining, 4)
                }
            return JSONResponse(
                {"ok": False, "error": f"HTTP {resp.status_code}"},
                status_code=resp.status_code
            )
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.post("/api/upload")
async def upload_files(files: List[UploadFile] = File(...)):
    """Upload files or zip archives and extract text contents."""
    extracted = {}
    for f in files:
        content = await f.read()
        filename = f.filename or "file.txt"
        if filename.endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as z:
                    for name in z.namelist():
                        if not name.endswith("/") and not name.startswith("__MACOSX"):
                            try:
                                data = z.read(name).decode("utf-8")
                                clean_name = Path(name).name
                                if clean_name:
                                    extracted[clean_name] = data
                            except UnicodeDecodeError:
                                pass
            except Exception as e:
                logger.warning("Error reading zip %s: %s", filename, e)
        else:
            try:
                text = content.decode("utf-8")
                clean_name = Path(filename).name
                extracted[clean_name] = text
            except UnicodeDecodeError:
                pass
    return {"ok": True, "files": extracted}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    session_id = uuid.uuid4().hex[:12]
    session = Session(session_id)
    sessions[session_id] = session
    logger.info("WS connected: %s", session_id)

    async def send_event(event: dict):
        try:
            await ws.send_json(event)
        except Exception:
            pass

    try:
        await send_event({"type": "session", "session_id": session_id})

        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await send_event({"type": "error", "message": "Invalid JSON"})
                continue

            msg_type = msg.get("type", "")

            if msg_type == "stop":
                session.cancel_task()
                await send_event({"type": "log", "role": "system",
                                  "message": "⏹️ Đã dừng pipeline."})

            elif msg_type == "reset":
                session.reset()
                await send_event({"type": "log", "role": "system",
                                  "message": "🔄 Đã reset session."})

            elif msg_type == "run":
                session.cancel_task()
                command = msg.get("command", "").strip()
                if not command:
                    await send_event({"type": "error", "message": "Vui lòng nhập yêu cầu."})
                    continue
                api_key = msg.get("api_key", "").strip()
                if not api_key:
                    await send_event({"type": "error", "message": "Vui lòng nhập API key."})
                    continue

                session.api_key = api_key

                # capture all params
                project_text = msg.get("project_text", "")
                default_models = msg.get("default_models", "")
                planner_models = msg.get("planner_models", "")
                tester_models = msg.get("tester_models", "")
                coder_models = msg.get("coder_models", "")
                reviewer_models = msg.get("reviewer_models", "")
                use_planner = msg.get("use_planner", True)
                use_reviewer = msg.get("use_reviewer", True)
                max_rounds = max(1, min(20, int(msg.get("max_rounds", 5))))

                async def _run():
                    try:
                        files = await run_pipeline(
                            command=command,
                            project_text=project_text,
                            default_models_str=default_models,
                            planner_models_str=planner_models,
                            tester_models_str=tester_models,
                            coder_models_str=coder_models,
                            reviewer_models_str=reviewer_models,
                            use_planner=use_planner,
                            use_reviewer=use_reviewer,
                            max_rounds=max_rounds,
                            api_key=api_key,
                            session_id=session_id,
                            send_event=send_event,
                        )
                        session.files = files
                        # Find zip from pipeline's tempdir
                        import glob
                        pattern = str(Path(tempfile.gettempdir()) / f"agent_{session_id}_*" / "project.zip")
                        matches = glob.glob(pattern)
                        if matches:
                            session.zip_path = matches[0]
                    except asyncio.CancelledError:
                        logger.info("Pipeline cancelled: %s", session_id)
                    except Exception as exc:
                        logger.exception("Pipeline error: %s", session_id)
                        await send_event({"type": "error", "message": str(exc)})

                session.task = asyncio.create_task(_run())

            elif msg_type == "chat":
                # Chat mode: normal conversation without code generation loop
                session.cancel_task()
                user_msg = msg.get("message", "").strip()
                if not user_msg:
                    continue
                api_key = msg.get("api_key", "").strip() or session.api_key
                if not api_key:
                    await send_event({"type": "error", "message": "Vui lòng nhập API key."})
                    continue

                session.api_key = api_key
                default_models = msg.get("default_models", "")
                models = parse_models(default_models) or ["deepseek/deepseek-chat"]
                chosen_model = models[0]

                # Project / attached files context
                files_dict = msg.get("files", {})
                files_context = ""
                if files_dict:
                    files_context = f"\n\nCác file đính kèm/dự án hiện có để bạn tham khảo:\n{format_project(files_dict)}"

                system_prompt = (
                    "Bạn là trợ lý AI thông minh, nhiệt tình và am hiểu lập trình. "
                    "Hãy trò chuyện và giải đáp các thắc mắc của người dùng bằng tiếng Việt một cách tự nhiên. "
                    "Nếu người dùng chỉ trò chuyện thông thường, chào hỏi hoặc nhờ giải thích/tư vấn tài liệu/file, "
                    "hãy trả lời rõ ràng, chi tiết, giải thích dễ hiểu mà KHÔNG tự tiện sinh toàn bộ mã nguồn hoặc tạo file test khi không được yêu cầu. "
                    "Nếu người dùng nhờ phân tích hay tìm lỗi trong các file đính kèm, hãy chỉ rõ lỗi và giải thích cách khắc phục."
                )

                history = msg.get("history", [])
                messages = [{"role": "system", "content": system_prompt + files_context}]
                for h in history:
                    messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
                messages.append({"role": "user", "content": user_msg})

                await send_event({
                    "type": "log",
                    "role": "system",
                    "message": f"💬 Đang gửi tin nhắn tới {short(chosen_model)}..."
                })

                async def _chat_task():
                    try:
                        reply, usage = await call_llm(messages, chosen_model, api_key)
                        await send_event({
                            "type": "chat_reply",
                            "content": reply,
                            "model": short(chosen_model),
                            "usage": usage
                        })
                        await send_event({
                            "type": "log",
                            "role": "system",
                            "message": f"✓ {short(chosen_model)} đã phản hồi. (Dùng {usage.get('total_tokens', 0):,} tokens)"
                        })
                    except Exception as e:
                        logger.exception("Chat error")
                        await send_event({"type": "error", "message": f"Lỗi chat: {e}"})

                session.task = asyncio.create_task(_chat_task())

            else:
                await send_event({"type": "error",
                                  "message": f"Unknown type: {msg_type}"})

    except WebSocketDisconnect:
        logger.info("WS disconnected: %s", session_id)
    except Exception:
        logger.exception("WS error: %s", session_id)
    finally:
        session.cancel_task()


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
