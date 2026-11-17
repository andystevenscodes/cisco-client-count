FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/andystevenscodes/cisco-client-count"
LABEL org.opencontainers.image.description="Counts connected Wi-Fi clients per access point from Cisco Catalyst 9800 telemetry"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY proto/ proto/
COPY ccc/ ccc/
COPY tools/ tools/
COPY VERSION .

# The protobuf classes are generated from the .proto files at build time.
RUN python -m grpc_tools.protoc -I proto --python_out=. \
        proto/ccc/pb/mdt_dialout.proto proto/ccc/pb/telemetry.proto \
    && useradd --system --uid 10001 --no-create-home ccc

ARG APP_VERSION=
ENV APP_VERSION=${APP_VERSION}

USER ccc

# 57500: telemetry from the controller (gRPC dial-out)   8080: web interface
EXPOSE 57500 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('WEB_LISTEN_PORT','8080'), timeout=4)"

CMD ["python", "-m", "ccc"]
