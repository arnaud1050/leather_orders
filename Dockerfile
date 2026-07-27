FROM python:3.13-alpine

WORKDIR /app

# su-exec: lets the entrypoint start as root (to fix bind-mount ownership)
# and then drop to appuser before running the app.
RUN apk add --no-cache su-exec

# Install dependencies first so this layer is cached unless requirements.txt changes
COPY requirements.txt .
RUN pip install --no-cache-dir \
    --trusted-host pypi.python.org \
    --trusted-host files.pythonhosted.org \
    --trusted-host pypi.org \
    -r requirements.txt \
    gunicorn

COPY . /app

# Non-root user, plus a data dir for when persistence (SQLite, etc.) is added
RUN adduser -D appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app \
    && chmod +x /app/entrypoint.sh

EXPOSE 5000

ENTRYPOINT ["/app/entrypoint.sh"]
# app.py defines `app = Flask(__name__)`, so gunicorn targets "app:app"
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "app:app"]
