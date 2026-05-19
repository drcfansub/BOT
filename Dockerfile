# Dùng bản Python nhẹ gọn (Efficiency)
FROM python:3.10-slim

# Cài đặt thư mục làm việc
WORKDIR /app

# Copy file requirements và cài đặt
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy toàn bộ code vào container
COPY . .

# Chạy bot
CMD ["python", "main.py"]
