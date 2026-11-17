#!/usr/bin/env bash
# Creates .env from .env.example with freshly generated secrets.
# Run once, from the project directory:  ./scripts/init-env.sh
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -e .env ]; then
    echo ".env already exists; leaving it untouched." >&2
    exit 1
fi

secret() { openssl rand -hex "$1"; }

admin_password="$(secret 12)"
export_token="$(secret 20)"

umask 077
sed \
    -e "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=${admin_password}|" \
    -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(secret 20)|" \
    -e "s|^SECRET_KEY=.*|SECRET_KEY=$(secret 32)|" \
    -e "s|^EXPORT_TOKEN=.*|EXPORT_TOKEN=${export_token}|" \
    -e "s|^POWERBI_DB_PASSWORD=.*|POWERBI_DB_PASSWORD=$(secret 20)|" \
    .env.example > .env

cat <<EOF
.env written (readable by its owner only).

  Web interface password : ${admin_password}
  Export token (Power BI): ${export_token}

Both are in .env if you need them again. Start with:

  docker compose up -d --build
EOF
