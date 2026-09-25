#!/usr/bin/env bash
# One-time host preparation for pull-based CD against a private GitHub repo.
#
# 1. creates a dedicated, passphrase-less ed25519 deploy key
# 2. prints the PUBLIC key so it can be registered on GitHub
#    (Repository -> Settings -> Deploy keys -> Add deploy key, read-only)
# 3. points ~/.ssh/config at it so `git fetch` uses it for github.com
# 4. trusts github.com's host key
# 5. clones (or rewires) /opt/service-hub/src to the SSH remote
#
# Usage:
#   setup-deploy-key.sh <git@github.com:OWNER/REPO.git>
set -Eeuo pipefail

REPO_URL="${1:-}"
REMOTE_DIR="${REMOTE_DIR:-/opt/service-hub}"
SRC_DIR="$REMOTE_DIR/src"
KEY="$HOME/.ssh/id_ed25519_service_hub_deploy"

if [ -z "$REPO_URL" ]; then
  echo "usage: $0 <git@github.com:OWNER/REPO.git>" >&2
  exit 2
fi

mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"

if [ ! -f "$KEY" ]; then
  ssh-keygen -t ed25519 -N '' -C 'service-hub-deploy' -f "$KEY" >/dev/null
  echo "generated $KEY"
else
  echo "reusing existing $KEY"
fi
chmod 600 "$KEY"
chmod 644 "$KEY.pub"

if ! grep -q "$KEY" "$HOME/.ssh/config" 2>/dev/null; then
  cat >> "$HOME/.ssh/config" <<EOF

Host github.com
    User git
    IdentityFile $KEY
    IdentitiesOnly yes
EOF
  echo "ssh config updated"
fi

ssh-keyscan -t ed25519,rsa github.com >> "$HOME/.ssh/known_hosts" 2>/dev/null
sort -u "$HOME/.ssh/known_hosts" -o "$HOME/.ssh/known_hosts"

mkdir -p "$REMOTE_DIR"
if [ -d "$SRC_DIR/.git" ]; then
  echo "rewiring existing checkout at $SRC_DIR"
  git -C "$SRC_DIR" remote set-url origin "$REPO_URL"
else
  echo "cloning into $SRC_DIR"
  git clone "$REPO_URL" "$SRC_DIR"
fi

cat <<EOF

------------------------------------------------------------------
Add this PUBLIC key to GitHub:
  Repo -> Settings -> Deploy keys -> Add deploy key (read-only)

$(cat "$KEY.pub")
------------------------------------------------------------------
Then verify with:
  ssh -T git@github.com
  git -C $SRC_DIR fetch origin
EOF
