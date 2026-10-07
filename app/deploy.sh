#!/bin/zsh
# Build payload + deploy Sales Web (repo Itthicheta/sales; backbone venv) to Cloudflare Pages (project mamapook-sales). No auth.
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")"
source "$HOME/mamapook-data/venv/bin/activate"
python build/build.py
# --branch main = the project's PRODUCTION branch (otherwise deploys land as previews)
npx wrangler pages deploy site --project-name mamapook-sales --branch main --commit-dirty=true 2>&1 | tail -2
