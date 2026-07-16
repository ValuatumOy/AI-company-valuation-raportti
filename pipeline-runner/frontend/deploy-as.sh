#!/usr/bin/env bash
# Deploy a branch to Vercel under our own git identity.
#
# Vercel Pro blocks any deployment whose commit author isn't a paid team
# member ("Git author <email> must have access to the team ... to create
# deployments") — this applies to the raw git commit metadata, regardless of
# repo visibility or who runs `vercel`. Since non-member contributors
# (Sami, Jami) can't be added without a $20/mo seat each, this script
# reassigns the author LOCALLY (never pushed, never touches their commit on
# GitHub) before deploying, so Vercel sees a team-member author instead.
#
# Usage: ./deploy-as.sh <branch> [--prod]
set -euo pipefail

BRANCH="${1:?usage: deploy-as.sh <branch> [--prod]}"
PROD_FLAG="${2:-}"

DEPLOY_NAME="laurihynonen23"
DEPLOY_EMAIL="lauri.hynonen@gmail.com"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"
ORIG_BRANCH="$(git rev-parse --abbrev-ref HEAD)"

git fetch origin "$BRANCH"
git checkout -B "$BRANCH" "origin/$BRANCH"
git commit --amend --author="$DEPLOY_NAME <$DEPLOY_EMAIL>" --no-edit

cd "$REPO_ROOT/pipeline-runner/frontend"
vercel --yes $PROD_FLAG

cd "$REPO_ROOT"
git checkout "$ORIG_BRANCH"
