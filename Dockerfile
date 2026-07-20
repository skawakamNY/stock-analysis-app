# Stage 1: Build the React frontend
FROM node:20-slim AS frontend-builder
WORKDIR /usr/src/app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Build the FastAPI backend using uv python image
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS backend
WORKDIR /usr/src/app

# Copy python dependency lockfiles
COPY pyproject.toml uv.lock ./
# Pre-install dependencies to take advantage of Docker layering cache
RUN uv sync --frozen --no-install-project

# Copy project files
COPY app/ ./app/

# Copy built frontend assets to target location mounted by FastAPI
COPY --from=frontend-builder /usr/src/app/frontend/dist ./frontend/dist

# Create necessary directories for runtime
RUN mkdir -p app/logs app/documents database

# Expose FastAPI port
EXPOSE 8000

# Run FastAPI backend server
CMD ["uv", "run", "python", "app/server.py"]
