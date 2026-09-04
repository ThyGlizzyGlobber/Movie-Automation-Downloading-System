FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Stage 5 replaces this with a deployed-copy bind mount; for local Compose
# (Stage 4) the source is baked into the image and re-built on change.
COPY app app

EXPOSE 8000

# --reload picks up source changes without a rebuild once Stage 5 swaps
# COPY for a bind mount, per the "confirmed architecture" deploy model.
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
