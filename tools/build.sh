#!/usr/bin/env bash
set -euo pipefail

# Navigate to the project root directory
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR/.."

echo "Building Docker image rphbck/payfit-downloader:latest..."
docker build -t rphbck/payfit-downloader:latest .
#docker buildx build --platform linux/amd64,linux/arm64 -t rphbck/payfit-downloader:latest .
echo "Build complete!"
