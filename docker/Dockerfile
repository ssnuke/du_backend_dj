FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app
# ffmpeg: transcodes voice notes to AAC/m4a server-side (core/utils/audio_transcode.py)
# so every voice note plays on Safari/iOS regardless of which browser recorded it —
# WebKit can't decode the WebM/Opus format Chrome/Firefox/Android record by default.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Copy Django project
COPY backend /app
# Copy docker scripts
COPY docker /app/docker
# Make entrypoint executable
RUN chmod +x /app/docker/entrypoint.sh
CMD ["/app/docker/entrypoint.sh"]