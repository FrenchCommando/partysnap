FROM python:3.12-slim

# ffmpeg/ffprobe for video thumbnails + metadata (slice 7a derivatives)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# 8000 is the container-INTERNAL port (fixed). The host-facing port is APP_PORT,
# mapped in docker-compose.yml — don't confuse the two.
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
