FROM python:3.11.9-slim-bookworm@sha256:8fb099199b9f2d70342674bd9dbccd3ed03a258f26bbd1d556822c6dfc60c317

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /opt/flowpilot

RUN groupadd --gid 10001 flowpilot \
    && useradd --uid 10001 --gid flowpilot --create-home --shell /usr/sbin/nologin flowpilot

COPY requirements.runtime.lock ./requirements.runtime.lock
RUN python -m pip install --no-cache-dir --no-deps -r requirements.runtime.lock \
    && python -m pip check

COPY --chown=flowpilot:flowpilot app ./app

USER flowpilot

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=6 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read()"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
