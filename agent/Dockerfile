# Launchpad hybrid agent image.
# Built and published as ghcr.io/launchpad/agent:latest (see settings.agent_image).
# Runs with the host Docker socket mounted so it can manage local workloads.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    AGENT_STATE_DIR=/var/lib/launchpad-agent

WORKDIR /opt/launchpad-agent

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY __init__.py runner.py main.py ./

VOLUME ["/var/lib/launchpad-agent"]

# LAUNCHPAD_URL and TOKEN are provided at `docker run` time by install.sh.
ENTRYPOINT ["python", "main.py"]
