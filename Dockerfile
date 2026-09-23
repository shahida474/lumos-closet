FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (better layer caching).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# /data is where the SQLite file lives. Mount a persistent volume here so
# orders/inventory survive container restarts and redeploys.
RUN mkdir -p /data
VOLUME /data
ENV DB_PATH=/data/lumos.db \
    PORT=8000 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "app:app"]
