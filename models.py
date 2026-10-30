"""
models.py — Pydantic schemas, enums, prompt constants, and config for the AI Code Agent.
"""

from enum import Enum
from pydantic import BaseModel, Field, field_validator
from typing import Dict


# ─────────────────────────── Role Enum & Metadata ───────────────────────────

class Role(str, Enum):
    PLANNER = "planner"
    TESTER = "tester"
    CODER = "coder"
    REVIEWER = "reviewer"


ROLE_LABEL: Dict[str, str] = {
    "planner": "Kiến trúc sư",
    "tester": "Kiểm thử viên",
    "coder": "Lập trình viên",
    "reviewer": "Reviewer",
}

ROLE_COLOR: Dict[str, str] = {
    "planner": "#a78bfa",   # purple
    "tester": "#60a5fa",    # blue
    "coder": "#34d399",     # green
    "reviewer": "#fbbf24",  # yellow
}

ROLE_ICON: Dict[str, str] = {
    "planner": "📐",
    "tester": "🧪",
    "coder": "💻",
    "reviewer": "🔍",
    "system": "⚙️",
}


# ─────────────────────────── Config Constants ───────────────────────────────

FALLBACK_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
MAX_LOG_CHARS = 4000
MAX_INSTRUCTION_CHARS = 4000
REVIEW_ROUNDS = 2
TOKEN_BUDGET_DEFAULT = 50000

# Khi trọng tài (Tester) sửa test, số hàm test và số assert không được tụt
# xuống dưới tỉ lệ này so với bản cũ — tránh "làm yếu" test cho Coder qua.
MIN_TEST_KEEP_RATIO = 0.75


# ─────────────────────────── Prompt Constants (original) ────────────────────

COMMON_RULES = """Quy ước chung:
- Tên file phẳng, chỉ gồm chữ, số, gạch dưới, đuôi .py; không dùng thư mục.
- Tiền tệ: dùng số nguyên (VND) hoặc Decimal, không dùng float.
- Không ghi token, key hay mật khẩu vào code; đọc từ biến môi trường (os.environ).
- Test không gọi mạng, không gọi API thật, không đọc ghi ngoài thư mục hiện tại. Phần gọi dịch vụ ngoài (Telegram, v.v.) phải tách khỏi logic; file chạy bot (ví dụ bot.py) không được import trong test.
- Chỉ dùng thư viện chuẩn của Python, trừ file chạy bot (được dùng python-telegram-bot nếu cần).
- Mỗi câu lệnh nằm trên MỘT DÒNG RIÊNG, thụt lề đúng chuẩn Python."""

FILE_FORMAT = """Mỗi file trả về đầy đủ nội dung, đúng định dạng:

### FILE: ten_file.py
```python
# toàn bộ nội dung file
```
"""

PLANNER_PROMPT = (
    "Bạn là kiến trúc sư phần mềm Python. Nhiệm vụ: biến yêu cầu ngắn của người dùng "
    "thành ĐẶC TẢ chi tiết, để một người viết test và một người khác viết code ĐỘC LẬP "
    "mà vẫn khớp nhau. Không viết code.\n\n"
    "Đặc tả gồm:\n"
    "1) Mục tiêu\n"
    "2) Danh sách file và vai trò\n"
    "3) Với mỗi hàm/class: tên CHÍNH XÁC, chữ ký (tham số, kiểu), hành vi, lỗi phải báo (loại exception)\n"
    "4) Trường hợp biên cần xử lý\n"
    "5) Giả định bạn đã chọn khi yêu cầu chưa rõ (ghi rõ để người dùng duyệt)\n\n"
    "Nếu có dự án hiện tại: giữ nguyên tên và hành vi đang có, chỉ đặc tả phần thay đổi "
    "và nêu rõ những gì phải giữ nguyên. Viết ngắn gọn nhưng đủ cụ thể để kiểm thử được.\n\n"
    + COMMON_RULES
)

TESTER_PROMPT = (
    "Bạn là kiểm thử viên Python độc lập. Chỉ dựa vào ĐẶC TẢ (bạn không thấy code) để viết "
    "test pytest: dùng đúng tên hàm/class trong đặc tả, bao gồm trường hợp biên và trường hợp "
    "báo lỗi. Test phải xác định (không ngẫu nhiên, không phụ thuộc thời gian hiện tại). "
    "Chỉ trả về các file test_*.py.\n\n" + FILE_FORMAT + "\n" + COMMON_RULES
)

CODER_PROMPT = (
    "Bạn là lập trình viên Python senior. Viết code để TOÀN BỘ test qua và đúng đặc tả. "
    "Test là chuẩn: không được sửa và không được trả về file test_*.py. "
    "Chỉ trả về các file code MỚI hoặc ĐÃ THAY ĐỔI, mỗi file viết đầy đủ, không viết tắt "
    "kiểu 'giữ nguyên phần còn lại'; file không nhắc tới sẽ được giữ nguyên. "
    "Khi nhận log lỗi: sửa nguyên nhân gốc trong code.\n\n" + FILE_FORMAT + "\n" + COMMON_RULES
)

REVIEWER_PROMPT = (
    "Bạn là reviewer khó tính nhưng thực tế. Đọc đặc tả, code và test, tìm lỗi THỰC SỰ: "
    "sai so với đặc tả, bug logic, trường hợp biên bị bỏ sót, lỗi bảo mật (lộ key, tin input "
    "chưa kiểm tra), sai tiền tệ hoặc làm tròn, cộng trùng giao dịch. "
    "Không góp ý về phong cách hay đặt tên.\n"
    "Nếu không có lỗi thật: trả lời đúng một từ LGTM.\n"
    "Nếu có: liệt kê tối đa 6 gạch đầu dòng, mỗi dòng nêu file, vấn đề và cách sửa cụ thể. "
    "Không viết lại code."
)


# ─────────────────────────── Pydantic Schemas ───────────────────────────────

class RunRequest(BaseModel):
    """Schema for a 'run' WebSocket message from the client (validated + clamped)."""
    type: str = "run"
    command: str
    project_text: str = ""
    default_models: str = ""
    planner_models: str = ""
    tester_models: str = ""
    coder_models: str = ""
    reviewer_models: str = ""
    use_planner: bool = True
    use_reviewer: bool = True
    max_rounds: int = Field(default=5, ge=1, le=20)
    token_budget: int = TOKEN_BUDGET_DEFAULT
    # True: dừng (có thể tiếp tục) khi chạm token_budget. False: chỉ cảnh báo.
    hard_budget: bool = True
    api_key: str = ""
    # Mã đồng bộ tùy chọn: dùng cùng mã trên mọi máy để thấy cùng lịch sử,
    # kể cả khi đổi API key. Mặc định lịch sử gắn với API key.
    profile: str = ""

    @field_validator("max_rounds", mode="before")
    @classmethod
    def _clamp_rounds(cls, v):
        try:
            return max(1, min(20, int(v)))
        except (TypeError, ValueError):
            return 5

    @field_validator("token_budget", mode="before")
    @classmethod
    def _coerce_budget(cls, v):
        try:
            return max(0, int(v or TOKEN_BUDGET_DEFAULT))
        except (TypeError, ValueError):
            return TOKEN_BUDGET_DEFAULT

    @field_validator("use_planner", "use_reviewer", "hard_budget", mode="before")
    @classmethod
    def _default_true(cls, v):
        return True if v is None else v


class LogEvent(BaseModel):
    type: str = "log"
    role: str
    message: str


class SpecEvent(BaseModel):
    type: str = "spec"
    content: str


class ProgressEvent(BaseModel):
    type: str = "progress"
    stage: str
    step: int
    total: int
    status: str  # "running" | "done" | "error"


class CodeEvent(BaseModel):
    type: str = "code"
    files: Dict[str, str]


class DoneEvent(BaseModel):
    type: str = "done"
    ok: bool
    files: Dict[str, str]
    zip_url: str


class ErrorEvent(BaseModel):
    type: str = "error"
    message: str
