#!/usr/bin/env sh
# Create .env with random secrets. Never overwrites an existing file.
set -eu
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  echo ".env already exists — leaving it untouched."
  exit 0
fi

rand() { # $1 = number of random bytes; prints URL-safe characters only
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "$1"
  else
    python3 -c "import secrets,sys; print(secrets.token_hex(int(sys.argv[1])))" "$1"
  fi
}

PG=$(rand 24)
SK=$(rand 32)
ADMIN=$(rand 12)
TOKEN="onet_$(rand 32)"
NEO=$(rand 16)

umask 077
sed \
  -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${PG}|" \
  -e "s|^OBSERVENET_SECRET_KEY=.*|OBSERVENET_SECRET_KEY=${SK}|" \
  -e "s|^OBSERVENET_ADMIN_PASSWORD=.*|OBSERVENET_ADMIN_PASSWORD=${ADMIN}|" \
  -e "s|^OBSERVENET_BOOTSTRAP_AGENT_TOKEN=.*|OBSERVENET_BOOTSTRAP_AGENT_TOKEN=${TOKEN}|" \
  -e "s|^NEO4J_PASSWORD=.*|NEO4J_PASSWORD=${NEO}|" \
  .env.example > .env
chmod 600 .env

cat <<MSG
Created .env (mode 600) with random secrets.

  Admin username : admin
  Admin password : ${ADMIN}

The password is stored in .env. Sign in, change it under Settings → Users & access, then
remove OBSERVENET_ADMIN_PASSWORD from .env (it is only read when no account exists).
MSG
