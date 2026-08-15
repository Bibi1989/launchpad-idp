FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      ca-certificates \
      curl \
      unzip \
      nodejs \
      npm \
    && rm -rf /var/lib/apt/lists/*

# AWS CLI v2 (EC2 VM provisioning / ECR from the control-plane worker)
RUN set -eux; \
    ARCH="$(uname -m)"; \
    case "$ARCH" in \
      x86_64) AWS_ARCH=x86_64; PULUMI_ARCH=linux-x64 ;; \
      aarch64|arm64) AWS_ARCH=aarch64; PULUMI_ARCH=linux-arm64 ;; \
      *) echo "unsupported arch: $ARCH" >&2; exit 1 ;; \
    esac; \
    curl -fsSLo /tmp/awscliv2.zip \
      "https://awscli.amazonaws.com/awscli-exe-linux-${AWS_ARCH}.zip"; \
    unzip -q /tmp/awscliv2.zip -d /tmp; \
    /tmp/aws/install; \
    rm -rf /tmp/aws /tmp/awscliv2.zip; \
    aws --version; \
    PULUMI_VERSION=3.143.0; \
    curl -fsSL \
      "https://github.com/pulumi/pulumi/releases/download/v${PULUMI_VERSION}/pulumi-v${PULUMI_VERSION}-${PULUMI_ARCH}.tar.gz" \
      | tar -xz -C /tmp; \
    mv /tmp/pulumi/* /usr/local/bin/; \
    rm -rf /tmp/pulumi; \
    pulumi version

# Build context is the monorepo root so shared `pkg/` is available.
COPY apps/api/pyproject.toml ./
COPY apps/api/app ./app
COPY apps/api/alembic.ini ./
COPY apps/api/alembic ./alembic
COPY pkg ./pkg

RUN pip install --upgrade pip && pip install .

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
