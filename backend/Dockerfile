# Three stages over one shared runtime:
#
#   runtime     the system packages and Python dependencies the app needs
#   release     the shipped image: .github/workflows/release.yml builds this
#               stage and pushes it to GHCR. Source baked in, version baked
#               in, no git, no Node, no --reload. Updating is pulling a newer
#               image, so nothing on the box ever builds anything.
#   git-deploy  the original git-pull install (truenas/custom-app-compose.yaml):
#               source bind-mounted from a clone, `POST /api/admin/deploy`
#               pulls it and rebuilds the frontend in place. Last, so it stays
#               the default for a plain `build:` — an existing install that
#               rebuilds gets exactly the image it always had.
FROM python:3.11-slim AS runtime

WORKDIR /app

# ffmpeg: trailers.py's yt-dlp download needs it to mux separate
# video/audio streams into one file — confirmed live (2026-09-09) that
# YouTube no longer serves a pre-merged/progressive format for most
# videos at a usable resolution, so a format string that avoided ffmpeg
# on purpose just failed outright ("Requested format is not available")
# on every real trailer. Merging is the only reliable path now. The
# organiser and logos.py use it (and ffprobe) too.
# mkvtoolnix: media_organizer's fix-audio-defaults backfill edits an
# MKV's default-audio-track flag with mkvpropedit, which rewrites the
# header in place — milliseconds whatever the file's size, against the
# full re-mux ffmpeg would need to change the same flag. Only the
# backfill uses it; the organiser itself is already re-muxing new files
# for other reasons and sets the flag on the way through.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg mkvtoolnix \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8000


FROM runtime AS release

COPY app app

# Filled in by CI — see app/core/version.py. APP_CHANNEL is the image tag this
# build is published under ("stable" or "beta"), and being set at all is
# what tells the app it updates by image rather than by `git pull`.
ARG APP_VERSION
ARG APP_COMMIT
ARG APP_BUILD_DATE
ARG APP_CHANNEL
ENV APP_VERSION=$APP_VERSION \
    APP_COMMIT=$APP_COMMIT \
    APP_BUILD_DATE=$APP_BUILD_DATE \
    APP_CHANNEL=$APP_CHANNEL \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# /api/health answers 200 whenever the process is up, whatever qBittorrent
# is doing (see its docstring), so this only ever fails for the backend
# itself — the signal an update needs to tell a good release from a bad one.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"]

# One process, deliberately: the background worker starts in the app's
# lifespan, so a second uvicorn worker would run a second copy of it.
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]


FROM runtime AS git-deploy

# git + openssh-client: Stage 6's self-update endpoint shells out to the
# real `git` CLI (via SSH, using the mounted deploy key) against the
# deployed-copy clone — not a Python git library, so the binary has to
# actually be here.
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
    && apt-get install -y --no-install-recommends git openssh-client curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# The deployed-copy clone at /repo is bind-mounted from the NAS host, so its
# directory ownership won't match this container's (root) user — git's
# dubious-ownership check would otherwise refuse every `git pull`.
RUN git config --global --add safe.directory /repo

# A bind mount of the clone's backend/app replaces this on the NAS; local
# Compose (docker-compose.yml) has no such mount and runs what's copied here.
COPY app app

# --reload picks up source changes from the bind-mounted clone without a
# rebuild, per the "confirmed architecture" deploy model.
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
