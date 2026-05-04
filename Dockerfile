# Use Python 3.12 slim image
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# IMPORTANT: Install git so uv can clone the repositories
RUN apt-get update && apt-get install -y git build-essential && rm -rf /var/lib/apt/lists/*

# Install uv for faster dependency management
RUN pip install --no-cache-dir uv

# Copy project files
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Accept the GitHub token as a build argument
ARG GITHUB_TOKEN

# Configure git to use the token silently, install dependencies, then remove the config
RUN git config --global url."https://${GITHUB_TOKEN}@github.com/".insteadOf "https://github.com/" && \
    uv pip install --system -e . && \
    git config --global --unset url."https://${GITHUB_TOKEN}@github.com/".insteadOf

# Create directories for volumes (will be mounted from host)
RUN mkdir -p /app/data/uploads /app/data/saved_sessions /app/datasets

# Expose MCP server port
EXPOSE 8222

# Run the application
CMD ["main"]
