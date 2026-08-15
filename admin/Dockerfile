FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends imagemagick \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 oohstory \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin oohstory
COPY admin /app
RUN python -m pip install --no-cache-dir .
COPY deploy/compose/run-as-oohstory.py /usr/local/bin/run-as-oohstory

EXPOSE 8092
ENTRYPOINT ["python", "/usr/local/bin/run-as-oohstory"]
CMD ["python", "-m", "uvicorn", "oohstory_admin.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8092", "--proxy-headers", "--forwarded-allow-ips=*"]
