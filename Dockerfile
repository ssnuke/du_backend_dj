# Root-level Dockerfile for AIC Cloud App Hosting, which builds the Dockerfile at the
# repository root with the repository root (django-app/) as the build context.
# Same image as docker/Dockerfile (which Render keeps using); keep the two in step.
# PORT defaults to AIC's default app port (3000); if you change the port in the
# AIC app settings, set a matching PORT environment variable.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=3000
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