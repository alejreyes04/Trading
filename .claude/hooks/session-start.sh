#!/bin/bash
# Instala las dependencias del proyecto en sesiones de Claude Code en la nube
# (incluidas las Routines programadas). Idempotente y no interactivo.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(pwd)}"
python3 -m pip install --quiet --root-user-action=ignore -e ".[dev]"
