FROM python:3.11-slim

WORKDIR /app

# git + openssh-client: Stage 6's self-update endpoint shells out to the
# real `git` CLI (via SSH, using the mounted deploy key) against the
# deployed-copy clone — not a Python git library, so the binary has to
# actually be here.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git openssh-client \
    && rm -rf /var/lib/apt/lists/*

# The deployed-copy clone at /repo is bind-mounted from the NAS host, so its
# directory ownership won't match this container's (root) user — git's
# dubious-ownership check would otherwise refuse every `git pull`.
RUN git config --global --add safe.directory /repo

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Stage 5 replaces this with a deployed-copy bind mount; for local Compose
# (Stage 4) the source is baked into the image and re-built on change.
COPY app app

EXPOSE 8000

# --reload picks up source changes without a rebuild once Stage 5 swaps
# COPY for a bind mount, per the "confirmed architecture" deploy model.
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
