# Headless Bot Kalung server worker — the PC-free ingest + BNCT poll.
#
# Build:  docker build -t bot-kalung-server .
# Run:    docker run -d --restart unless-stopped \
#           -e SUPABASE_DB_URL="postgresql://postgres.<ref>:PW@aws-0-...pooler.supabase.com:5432/postgres" \
#           -e DRIVE_CREDENTIALS=/run/secrets/drive.json \
#           -v /host/path/drive-key.json:/run/secrets/drive.json:ro \
#           bot-kalung-server
#
# Secrets are provided at runtime (env + a mounted key) and are NOT baked into
# the image (.dockerignore excludes secrets/).
FROM python:3.11-slim

WORKDIR /app

COPY requirements-server.txt ./
RUN pip install --no-cache-dir -r requirements-server.txt

# Only the server-side package (the desktop UI + assets are excluded by
# .dockerignore since the worker never imports them).
COPY bot_kalung ./bot_kalung

ENV PYTHONUNBUFFERED=1

# Long-running worker (scan + poll on a schedule). For cron-style hosts, run
# `python -m bot_kalung.server --once` per tick instead.
CMD ["python", "-m", "bot_kalung.server"]
