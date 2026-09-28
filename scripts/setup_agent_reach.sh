#!/bin/bash
# Instala Agent-Reach v1.5.0 (versión fijada) y solo los canales que usa el agente investigador.
# Instalación autorizada explícitamente por el usuario el 28-09-2026.
# Sin --system, sin sudo, sin npm -g. Idempotente. Las credenciales llegan por variables de entorno
# del entorno cloud (TWITTER_AUTH_TOKEN, TWITTER_CT0, REDDIT_SESSION) y nunca se guardan en el repo.
set -euo pipefail

AR_TAG="v1.5.0"
AR_COMMIT="f65526cbaaad3879473acc1ba6dbefd195caf2be"
RDT_COMMIT="5e4fb3720d5c174e976cd425ccc3b879d52cac66"
SRC="$HOME/.agent-reach-src"
VENV="$HOME/.agent-reach-venv"
TOOLS="$HOME/.agent-reach-tools"
BIN="$HOME/.local/bin"

if [ "$(agent-reach --version 2>/dev/null || true)" != "Agent Reach v1.5.0" ]; then
  if [ ! -d "$SRC/.git" ]; then
    GIT_LFS_SKIP_SMUDGE=1 git clone --quiet --depth 1 --branch "$AR_TAG" \
      https://github.com/Panniantong/agent-reach "$SRC"
  fi
  actual="$(git -C "$SRC" rev-parse HEAD)"
  if [ "$actual" != "$AR_COMMIT" ]; then
    echo "Agent-Reach: commit inesperado ($actual); no se instala." >&2
    exit 1
  fi
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q "$SRC" "twitter-cli==0.8.5" \
    "git+https://github.com/public-clis/rdt-cli.git@$RDT_COMMIT"
  mkdir -p "$TOOLS" "$BIN"
  npm install --silent --no-audit --no-fund --prefix "$TOOLS" mcporter@0.14.1
  for b in agent-reach twitter rdt yt-dlp; do ln -sf "$VENV/bin/$b" "$BIN/$b"; done
  ln -sf "$TOOLS/node_modules/.bin/mcporter" "$BIN/mcporter"
fi

# Exa (búsqueda web sin clave) en la configuración global de mcporter.
mkdir -p "$HOME/.mcporter"
if [ ! -f "$HOME/.mcporter/mcporter.json" ]; then
  printf '{\n  "mcpServers": {\n    "exa": {"baseUrl": "https://mcp.exa.ai/mcp"}\n  }\n}\n' > "$HOME/.mcporter/mcporter.json"
fi

# Reddit: rdt-cli lee ~/.config/rdt-cli/credential.json; se genera desde REDDIT_SESSION con permisos 600.
if [ -n "${REDDIT_SESSION:-}" ]; then
  mkdir -p "$HOME/.config/rdt-cli"
  (umask 077 && python3 - <<'PY'
import json, os, pathlib, time
p = pathlib.Path.home() / ".config" / "rdt-cli" / "credential.json"
p.write_text(json.dumps({"cookies": {"reddit_session": os.environ["REDDIT_SESSION"]},
                         "source": "env", "saved_at": time.time()}))
os.chmod(p, 0o600)
PY
  )
fi

echo "Agent-Reach listo: $(agent-reach --version 2>/dev/null)"
