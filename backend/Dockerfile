FROM python:3.11-slim

WORKDIR /app

# git + openssh-client: Stage 6's self-update endpoint shells out to the
# real `git` CLI (via SSH, using the mounted deploy key) against the
# deployed-copy clone — not a Python git library, so the binary has to
# actually be here.
# ffmpeg: trailers.py's yt-dlp download needs it to mux separate
# video/audio streams into one file — confirmed live (2026-09-09) that
# YouTube no longer serves a pre-merged/progressive format for most
# videos at a usable resolution, so a format string that avoided ffmpeg
# on purpose just failed outright ("Requested format is not available")
# on every real trailer. Merging is the only reliable path now.
# nodejs/npm: frontend migration Part B — deploy.py's run_git_pull() runs
# `npm ci` + `npm run build` in the deployed clone's own frontend/
# directory after every pull, so the Vite build has to happen somewhere,
# and folding it into the existing self-update flow (rather than a
# second, separate build mechanism) means it happens right here. Via
# NodeSource's own setup script, not Debian's bundled `nodejs` package —
# deliberately pinned to the same major version (20.x) as frontend/
# Dockerfile's own `node:20-alpine` build stage, rather than trusting
# whatever happens to be in this base image's apt repo at build time.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git openssh-client ffmpeg curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
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
