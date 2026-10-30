# 🤖 AI Code Agent (Multi-Agent Software Engineering Studio)

Hệ thống lập trình tự động đa tác tử (Multi-Agent System) kết hợp giao diện Web hiện đại, hỗ trợ chia vai thông minh (**Kiến trúc sư, Kiểm thử viên, Lập trình viên, Reviewer**) để phân tích đặc tả, viết test pytest, sinh mã nguồn, tự động debug và soát lỗi qua OpenRouter API.

---

## ✨ Tính năng nổi bật

- **⚡ Quy trình 4 vai AI tự động:**
  - 📐 **Kiến trúc sư (Planner):** Lập đặc tả chi tiết từ yêu cầu ngắn.
  - 🧪 **Kiểm thử viên (Tester):** Viết test pytest độc lập.
  - 💻 **Lập trình viên (Coder):** Viết code và tự động debug qua nhiều vòng cho tới khi vượt qua toàn bộ test.
  - 🔍 **Reviewer:** Soát lỗi logic, bảo mật, tính toán tiền tệ và tối ưu code.
- **💾 Lưu lịch sử & chạy tiếp từ chỗ đã dừng (mới):**
  - Mọi lần chạy được lưu checkpoint vào SQLite sau từng bước (đặc tả, test, từng vòng Coder, từng lượt Review).
  - Mất mạng / đóng tab / tắt trình duyệt **không làm dừng** pipeline: nó vẫn chạy nền trên server, bạn kết nối lại là thấy tiếp.
  - Server bị tắt giữa chừng → bấm **Tiếp tục**, pipeline chạy tiếp đúng vòng đang dở (không chạy lại Planner/Tester/các vòng đã xong).
  - Lịch sử trò chuyện (kèm file đính kèm) cũng được lưu.
- **💬 Chế độ Trò chuyện & Tư vấn (Chat Mode):** Hỏi đáp, nhờ AI đọc và giải thích code mà không kích hoạt chu trình viết test tự động.
- **📎 Đính kèm & Đọc file linh hoạt:** Hỗ trợ file mã nguồn hoặc `.zip` (có giới hạn kích thước chống zip bomb).
- **📊 Theo dõi Token & Số dư thời gian thực**, kèm **Token Budget chặn thật**: chạm hạn mức thì run được *tạm dừng* (không mất gì), nâng hạn mức rồi Tiếp tục.
- **⚡ Quản lý cấu hình Model 1-Click (Dynamic Presets)** và **🎨 Theme Studio** (phía giao diện).

---

## 🚀 Cài đặt & Khởi chạy

Yêu cầu: Python 3.10+.

```bash
pip install -r requirements.txt     # đã gồm pytest
python server.py                    # hoặc click đúp start.bat trên Windows
```

Mở **http://localhost:8000**.

### Biến môi trường

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `HOST` | `127.0.0.1` | Địa chỉ lắng nghe. Mặc định chỉ máy này truy cập được. |
| `PORT` | `8000` | Cổng. |
| `RELOAD` | `0` | `1` = auto-reload (chỉ dùng khi phát triển). |
| `APP_TOKEN` | *(trống)* | Nếu đặt, WebSocket (`/ws?token=...`) và `/api/*` (header `X-App-Token` hoặc `?token=`) đều yêu cầu token. **Bắt buộc nên đặt nếu `HOST` khác `127.0.0.1`** — server chạy code do AI sinh ra. |
| `DATA_DIR` | `./data` | Nơi lưu `agent.db` (lịch sử, checkpoint). |
| `SANDBOX` | *(trống)* | `docker` = chạy test trong container (xem bên dưới). |
| `SANDBOX_IMAGE` | `agent-sandbox` | Tên image Docker dùng cho sandbox. |

---

## 💾 Lịch sử, đổi máy, mất kết nối

### Dữ liệu nằm ở đâu?
Trong file `DATA_DIR/agent.db` **trên máy chạy server** (không nằm trong trình duyệt). API key **không** được lưu; lịch sử gắn với `sha256(API key)`
(hoặc với mã `profile` nếu bạn gửi kèm — dùng cùng mã trên mọi máy để thấy chung lịch sử kể cả khi đổi API key).

### Đổi máy — 3 cách
1. **Chạy server ở một chỗ cố định** (VPS, máy nhà luôn bật) và mở từ máy nào cũng được. Nhớ đặt `HOST=0.0.0.0` **và** `APP_TOKEN`, tốt nhất đặt sau HTTPS (reverse proxy).
2. **Đặt `DATA_DIR` vào thư mục đồng bộ** (OneDrive/Dropbox/Google Drive). Chỉ chạy server trên **một** máy tại một thời điểm — hai máy cùng ghi một file SQLite có thể làm hỏng dữ liệu.
3. **Sao lưu thủ công:** `GET /api/export` (JSON) trên máy cũ → `POST /api/import` trên máy mới. Id đã tồn tại sẽ được bỏ qua nên import nhiều lần vẫn an toàn.

> Lưu ý: `Authorization: Bearer <API key>` (hoặc `X-Profile`) là thứ xác định bạn là ai. Ai có API key đó và truy cập được server thì thấy lịch sử của bạn.
>
> Nếu đã đặt `APP_TOKEN`, giao diện phải gửi kèm token: `new WebSocket("/ws?token=...")` và nối `?token=...` vào link `zip_url` khi tải về.

### Mất kết nối khi đang chạy
- **Chỉ mất kết nối mạng/đóng tab:** run vẫn chạy trên server. Mở lại và gửi `attach` → nhận lại trạng thái hiện tại + các dòng log bị lỡ.
- **Server tắt/crash:** run được đánh dấu `interrupted`. Gửi `resume` (kèm API key) → chạy tiếp từ checkpoint cuối.
- **Hết token budget:** run ở trạng thái `paused_budget`; gửi `resume` kèm `token_budget` lớn hơn.
- **Hết số vòng mà test chưa qua:** trạng thái `failed`; `resume` mở lại vòng debug thêm tối thiểu 3 vòng (hoặc `max_rounds` bạn chỉ định).
- Một lệnh LLM đang gọi dở khi bị ngắt sẽ được gọi lại (tốn lại token của riêng lệnh đó), phần đã xong không bị làm lại.

### Giao thức WebSocket mới (cho `static/app.js`)

Các message cũ (`run`, `chat`, `stop`, `reset`) vẫn dùng như trước. Thêm:

| Gửi lên | Ý nghĩa | Server trả về |
|---|---|---|
| `{"type":"identify","api_key":"..."}` | Nhận diện, lấy danh sách lịch sử | `{"type":"history","runs":[...],"conversations":[...]}` |
| `{"type":"attach","run_id":"...","after_seq":N}` | Nối lại run đang chạy / đã xong | `run_info`, `spec`, `code`, `progress`, `tokens`, các `log` có `seq > N` |
| `{"type":"resume","run_id":"...","api_key":"...","token_budget":N,"max_rounds":N}` | Chạy tiếp run dở | `run_started` rồi như `attach` |
| `{"type":"chat","message":"...","conversation_id":"..."}` | Chat có lưu lịch sử | `conversation` (id), `chat_reply` |
| `{"type":"stop"}` / `{"type":"stop","run_id":"..."}` | Dừng (vẫn tiếp tục được) | log "đã dừng", `run_status` |

Sự kiện mới: `run_started{run_id}`, `run_info`, `run_status{status}` (`done|failed|stopped|error|paused_budget`), `conversation{conversation_id}`, `history`.
Sự kiện `done` có thêm `run_id`; khi dừng vì budget có `paused:true`; khi test không qua có `original_files` (bản gốc của bạn) còn `files` là code dở dang của Coder.

Gợi ý tích hợp giao diện: lưu `run_id` + `seq` log cuối vào `localStorage`; khi WebSocket mở lại, gửi `attach`; tự reconnect với backoff.

REST (header `Authorization: Bearer <API key>`): `GET /api/runs`, `GET /api/runs/{id}`, `DELETE /api/runs/{id}`,
`GET /api/conversations`, `GET /api/conversations/{id}`, `DELETE /api/conversations/{id}`, `GET /api/export`, `POST /api/import`,
`GET /api/credits`, `GET /api/download/{run_id}`.

---

## 🛡️ Bảo mật & sandbox

- Server mặc định chỉ lắng nghe `127.0.0.1`. Nếu mở ra mạng, **hãy đặt `APP_TOKEN`** (server sẽ cảnh báo nếu quên).
- API key gửi qua header (`/api/credits`); dạng `?api_key=` chỉ còn là đường lùi cho giao diện cũ vì nó lộ key vào log — hãy đổi `app.js` sang header.
- Test do AI sinh ra chạy ở subprocess với môi trường đã lọc (không lộ key). Trên Linux/macOS còn giới hạn CPU, RAM ảo và dung lượng file. **Trên Windows chỉ có lọc môi trường + timeout** — code vẫn đọc/ghi/gọi mạng được.
- Để cô lập thật sự: cài Docker, rồi
  ```bash
  docker build -t agent-sandbox -f Dockerfile.sandbox .
  SANDBOX=docker python server.py        # Windows (cmd): set SANDBOX=docker && python server.py
  ```
  Container chạy với `--network none`, giới hạn RAM/CPU/số tiến trình, bỏ toàn bộ capabilities.
- Upload: giới hạn 20 MB/lần, 2 MB/file, 500 file & 20 MB giải nén cho mỗi zip; file trùng tên ở thư mục khác nhau không còn ghi đè nhau.
- Trọng tài sửa test bị từ chối nếu làm số test/assert tụt dưới 75% (chống "làm yếu" test cho Coder qua).

---

## 📁 Cấu trúc dự án

```text
ai_code_agent/
├── server.py          # FastAPI: WebSocket điều khiển + REST lịch sử/upload/download
├── jobs.py            # Job chạy nền sống độc lập với WebSocket, attach/replay
├── pipeline.py        # Máy trạng thái 4 vai AI, có checkpoint & resume
├── store.py           # SQLite: runs (checkpoint), events, conversations, export/import
├── llm.py             # Client OpenRouter: retry/Retry-After, lỗi key/credit dừng ngay, xoay model
├── models.py          # Prompt, hằng số, RunRequest (validate + kẹp giá trị)
├── utils.py           # Parse file, AST, chạy pytest (ulimit/docker), zip
├── requirements.txt
├── Dockerfile.sandbox # Image cho SANDBOX=docker
├── start.bat          # Chạy nhanh trên Windows
├── data/              # (tự tạo) agent.db — lịch sử & checkpoint
└── static/            # index.html, style.css, app.js (giao diện)
```

---

## 🔑 Cấu hình API Key
Ứng dụng dùng API của [OpenRouter](https://openrouter.ai/). Nhập key vào ô `🔑 OpenRouter API Key` trên giao diện rồi bấm **Lưu** (key lưu ở `localStorage` của trình duyệt, **không** lưu trên server).
