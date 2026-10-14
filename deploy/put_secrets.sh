#!/usr/bin/env bash
# Copy secrets from your local .env into AWS SSM Parameter Store (encrypted SecureString).
# Run this yourself:  ./deploy/put_secrets.sh
# Values go straight from .env to your AWS account; nothing is printed except the generated access code.
set -euo pipefail
cd "$(dirname "$0")/.."
REGION="${AWS_REGION:-ca-central-1}"
PREFIX="/changeblast"
set -a; . ./.env; set +a

put() {  # name value
  aws ssm put-parameter --region "$REGION" --name "$PREFIX/$1" --type SecureString --value "$2" --overwrite >/dev/null
  echo "stored $PREFIX/$1"
}

for key in ANTHROPIC_API_KEY OPENROUTER_API_KEY GITHUB_TOKEN; do
  val="${!key:-}"
  [ -n "$val" ] || { echo "missing $key in .env"; exit 1; }
  put "$key" "$val"
done

put DAILY_LIMIT_USD "${DAILY_LIMIT_USD:-15}"
put DOMAIN "${DEPLOY_DOMAIN:-changeblast.dvip.dev}"

# Access code for running investigations: keep an existing one, or generate a new one.
if ! aws ssm get-parameter --region "$REGION" --name "$PREFIX/ACCESS_CODE" >/dev/null 2>&1; then
  code="cb-$(openssl rand -hex 6)"
  put ACCESS_CODE "$code"
  echo
  echo "Access code (give this to the AI lead; it's needed to run investigations): $code"
else
  echo "kept existing $PREFIX/ACCESS_CODE (read it with: aws ssm get-parameter --name $PREFIX/ACCESS_CODE --with-decryption --query Parameter.Value --output text --region $REGION)"
fi
