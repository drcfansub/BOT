# Sử dụng Python gọn nhẹ (Efficiency)
FROM python:3.10-slim

# Cài đặt FFMPEG (Rất quan trọng để tải nhạc không bị lỗi)
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Thiết lập thư mục làm việc
WORKDIR /app

# Copy thư viện và cài đặt
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy toàn bộ code
COPY . .

# Chạy bot
CMD ["python", "bot.py"]
