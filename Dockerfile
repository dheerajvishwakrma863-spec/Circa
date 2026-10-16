FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends libzbar0 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY analytics ./analytics
COPY dashboard ./dashboard
EXPOSE 8000
CMD ["sh", "-c", "gunicorn backend.app:app -k uvicorn.workers.UvicornWorker -w 2 --forwarded-allow-ips="*" -b 0.0.0.0:${PORT:-8000}"]
