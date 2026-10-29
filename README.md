# 🤖 AI Code Agent (Multi-Agent Software Engineering Studio)

Hệ thống lập trình tự động đa tác tử (Multi-Agent System) kết hợp giao diện Web hiện đại, hỗ trợ chia vai thông minh (**Kiến trúc sư, Kiểm thử viên, Lập trình viên, Reviewer**) để phân tích đặc tả, viết test pytest, sinh mã nguồn, tự động debug và soát lỗi qua OpenRouter API.

---

## ✨ Tính năng nổi bật

- **⚡ Quy trình 4 vai AI tự động:**
  - 📐 **Kiến trúc sư (Planner):** Lập đặc tả chi tiết từ yêu cầu ngắn.
  - 🧪 **Kiểm thử viên (Tester):** Viết test pytest độc lập.
  - 💻 **Lập trình viên (Coder):** Viết code và tự động debug qua nhiều vòng cho tới khi vượt qua toàn bộ test.
  - 🔍 **Reviewer:** Soát lỗi logic, bảo mật, tính toán tiền tệ và tối ưu code.
- **💬 Chế độ Trò chuyện & Tư vấn (Chat Mode):** Cho phép đặt câu hỏi, thảo luận, nhờ AI đọc và giải thích code mà không kích hoạt chu trình viết test tự động.
- **📎 Đính kèm & Đọc file linh hoạt:** Hỗ trợ tải file mã nguồn hoặc file `.zip` (hỗ trợ kéo thả Drag & Drop) để AI đọc và phân tích.
- **📊 Theo dõi Token & Số dư thời gian thực:**
  - Hiển thị số dư USD thực tế còn lại trong tài khoản OpenRouter.
  - Thống kê chi tiết lượng token (Prompt / Output) theo từng vai AI.
  - Hạn mức cảnh báo Token Budget.
- **⚡ Quản lý cấu hình Model 1-Click (Dynamic Presets):**
  - Tự do chuyển đổi nhanh giữa các chế độ: *🎁 Miễn phí (Free)*, *🌟 Ngon - Bổ - Rẻ (DeepSeek/Qwen)*, *👑 Cao cấp (Claude 3.7 / GPT-4o)*.
  - Hỗ trợ thêm mới, chỉnh sửa, đổi tên hoặc xóa cấu hình theo ý muốn.
- **🎨 Tùy biến giao diện (Theme Studio):** Thay đổi tiêu đề, logo emoji, màu chủ đạo (Accent Color) và phong cách nền tối (GitHub Dark, OLED, Midnight Navy, Slate).

---

## 🚀 Hướng dẫn cài đặt & Khởi chạy

### 1. Yêu cầu hệ thống
- Python 3.10 trở lên.

### 2. Cài đặt các thư viện cần thiết
```bash
pip install -r requirements.txt pytest
```

### 3. Khởi chạy ứng dụng

**Trên Windows:**
- Click đúp trực tiếp vào file **`start.bat`**.

**Hoặc chạy qua dòng lệnh:**
```bash
python server.py
```

### 4. Truy cập giao diện
Mở trình duyệt và truy cập:
👉 **`http://localhost:8000`**

---

## 📁 Cấu trúc dự án

```text
ai_code_agent/
├── server.py          # FastAPI Backend (REST API + WebSocket + File Upload)
├── pipeline.py        # Logic điều phối quy trình 4 vai AI (Async)
├── llm.py             # Client kết nối OpenRouter API, xoay vòng model & tính token
├── models.py          # Khai báo cấu trúc dữ liệu, Pydantic schemas & Prompt mẫu
├── utils.py           # Công cụ xử lý file, kiểm tra cú pháp AST, chạy pytest sandbox
├── requirements.txt   # Danh sách thư viện phụ thuộc
├── start.bat          # File chạy nhanh 1-click cho Windows
├── static/
│   ├── index.html     # Giao diện người dùng Web (SPA)
│   ├── style.css      # Thiết kế giao diện Dark Mode & Modal Tùy biến
│   └── app.js         # Xử lý WebSocket, Token Tracker, Presets & Upload
└── README.md
```

---

## 🔑 Cấu hình API Key
Ứng dụng sử dụng API từ [OpenRouter](https://openrouter.ai/). Bạn chỉ cần lấy API Key từ OpenRouter, nhập trực tiếp vào ô `🔑 OpenRouter API Key` trên giao diện web và bấm **Lưu**. Key được lưu bảo mật trên trình duyệt của bạn (`localStorage`).
