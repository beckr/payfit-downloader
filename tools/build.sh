#!/usr/bin/env bash
set -euo pipefail

# Navigate to the project root directory
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR/.."

# The version is defined once in pyproject.toml (bump it with `uv version --bump patch|minor|major`)
VERSION="$(uv version --short 2>/dev/null)"
IMAGE="rphbck/payfit-downloader"

echo "Building Docker image $IMAGE:$VERSION (and $IMAGE:latest)..."
#docker build -t "$IMAGE:$VERSION" -t "$IMAGE:latest" .
docker buildx build --platform linux/amd64,linux/arm64 -t "$IMAGE:$VERSION" -t "$IMAGE:latest" . --push
echo "Build complete!"
