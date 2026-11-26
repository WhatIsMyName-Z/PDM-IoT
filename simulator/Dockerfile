FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# -u = ไม่บัฟเฟอร์ log จะได้เห็นใน docker compose logs ทันที
CMD ["python", "-u", "simulator.py"]
