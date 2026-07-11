FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /sandbox
COPY sandbox/runner.py /sandbox/runner.py

RUN useradd --system --uid 65534 --no-create-home --shell /usr/sbin/nologin sandbox || true

USER 65534:65534
ENTRYPOINT ["python", "/sandbox/runner.py"]
