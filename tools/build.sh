#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./tools/build.sh         Build for the local architecture and load the image into Docker
#   ./tools/build.sh --push  Build for linux/amd64 and linux/arm64 and push to Docker Hub

# Navigate to the project root directory
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR/.."

# The version is defined once in pyproject.toml (bump it with `uv version --bump patch|minor|major`)
VERSION="$(uv version --short 2>/dev/null)"
IMAGE="rphbck/payfit-downloader"

case "${1:-}" in
  --push)
    echo "Building and pushing multi-arch Docker image $IMAGE:$VERSION (and $IMAGE:latest)..."
    docker buildx build --platform linux/amd64,linux/arm64 -t "$IMAGE:$VERSION" -t "$IMAGE:latest" --push .
    ;;
  "")
    echo "Building local Docker image $IMAGE:$VERSION (and $IMAGE:latest)..."
    docker buildx build --load -t "$IMAGE:$VERSION" -t "$IMAGE:latest" .
    ;;
  *)
    echo "Usage: $0 [--push]" >&2
    exit 1
    ;;
esac

echo "Build complete!"
