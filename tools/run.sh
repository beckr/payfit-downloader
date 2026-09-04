#!/usr/bin/env bash
set -euo pipefail

# Navigate to the project root directory
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR/.."

# Check if secrets.private.env or .env exists and load it safely
ENV_FILE=""
if [ -f secrets.private.env ]; then
  ENV_FILE="secrets.private.env"
elif [ -f .env ]; then
  ENV_FILE=".env"
fi

if [ -n "$ENV_FILE" ]; then
  echo "Loading environment variables from $ENV_FILE..."
  while IFS= read -r line || [ -n "$line" ]; do
    # Skip comments and empty lines
    if [[ "$line" =~ ^[[:space:]]*# ]] || [[ -z "$line" ]]; then
      continue
    fi
    # Split key and value on the first =
    if [[ "$line" =~ ^([^=]+)=(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      val="${BASH_REMATCH[2]}"
      # Strip surrounding quotes if any
      val="${val#\"}"
      val="${val%\"}"
      val="${val#\'}"
      val="${val%\'}"
      export "$key"="$val"
    fi
  done < "$ENV_FILE"
fi

# Fallback values
LOGIN="${LOGIN:-""}"
PASSWORD="${PASSWORD:-""}"
TOTP_SECRET="${TOTP_SECRET:-""}"
COMPANY_NAME="${COMPANY_NAME:-""}"
DOWNLOAD_DIR_HOST="${DOWNLOAD_DIR_HOST:-"$(pwd)/downloads"}"

if [ -z "$LOGIN" ] || [ -z "$PASSWORD" ]; then
  echo "Error: LOGIN and PASSWORD must be set in your environment or a local .env file."
  echo "Please read README.md for instructions on setting up configuration parameters."
  exit 1
fi

# Create download directory on the host if it does not exist
mkdir -p "$DOWNLOAD_DIR_HOST"

echo "Launching Payfit Downloader in Docker..."
docker run --rm \
  -v "$DOWNLOAD_DIR_HOST":/app/payfit/downloads \
  -e DOWNLOAD_DIR=/app/payfit/downloads \
  -e LOG_FILE_PATH=/app/payfit/downloads/downloader.log \
  -e LOGIN="$LOGIN" \
  -e PASSWORD="$PASSWORD" \
  -e TOTP_SECRET="$TOTP_SECRET" \
  -e COMPANY_NAME="$COMPANY_NAME" \
  rphbck/payfit-downloader:latest
