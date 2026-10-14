#!/usr/bin/env bash
# Runs on the EC2 host: pull the latest code, write .env from SSM, (re)build and start the stack.
# Used at first boot (user-data) and for every redeploy (via SSM Run Command).
set -euo pipefail
REGION="${AWS_REGION:-ca-central-1}"
APP=/opt/changeblast
cd "$APP"
git fetch -q origin main && git reset -q --hard origin/main

# Secrets from SSM Parameter Store -> .env (readable by root only).
umask 077
aws ssm get-parameters-by-path --region "$REGION" --path /changeblast --with-decryption \
  --query 'Parameters[].[Name,Value]' --output text \
  | awk -F'\t' '{ sub("^/changeblast/", "", $1); printf "%s=%s\n", $1, $2 }' > .env.ssm

# Postgres password is generated once on this host and kept.
if [ ! -f .env.local ]; then echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)" > .env.local; fi
cat .env.ssm .env.local > .env
rm -f .env.ssm

set -a; . ./.env; set +a
export DOMAIN="${DOMAIN:-:80}"
docker compose -f docker-compose.prod.yml up -d --build --remove-orphans
docker image prune -f >/dev/null
echo "deployed $(git rev-parse --short HEAD) for $DOMAIN"
