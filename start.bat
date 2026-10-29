@echo off
chcp 65001 > nul
echo ===================================================
echo       Đang khởi động AI Code Agent Server...
echo ===================================================
cd /d "%~dp0"

echo 1. Kiểm tra và cài đặt thư viện cần thiết...
python -m pip install -r requirements.txt pytest

echo 2. Khởi chạy server FastAPI...
echo Mở trình duyệt và truy cập: http://localhost:8000
echo Nhấn Ctrl+C để dừng server.
echo ===================================================
python server.py
pause
