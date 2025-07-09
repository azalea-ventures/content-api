#!/usr/bin/env sh

# Set default values if not provided
export WORKER_TIMEOUT=${WORKER_TIMEOUT:-1800}
export WORKER_COUNT=${WORKER_COUNT:-2}
export MAX_REQUESTS=${MAX_REQUESTS:-1000}

exec gunicorn main:app \
  -k uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:$PORT \
  --workers $WORKER_COUNT \
  --worker-connections 1000 \
  --timeout $WORKER_TIMEOUT \
  --keep-alive 5 \
  --max-requests $MAX_REQUESTS \
  --max-requests-jitter 100 \
  --worker-tmp-dir /dev/shm \
  --preload
