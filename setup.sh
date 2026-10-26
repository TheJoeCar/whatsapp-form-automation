#!/usr/bin/env bash
# Setup script for whatsapp-form-automation
# Source this file to enable tab-completion and PATH setup:
#   source setup.sh
# Or run it directly:
#   bash setup.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_BIN="$SCRIPT_DIR/.venv/bin"

# Add venv bin to PATH if not already there
if [[ ":$PATH:" != *":$VENV_BIN:"* ]]; then
    export PATH="$VENV_BIN:$PATH"
    echo "Added $VENV_BIN to PATH"
fi

# Create a symlink for wa in the venv bin so it's findable for completion
if [[ ! -L "$VENV_BIN/wa" ]]; then
    ln -sf "$SCRIPT_DIR/wa" "$VENV_BIN/wa"
    echo "Created symlink: $VENV_BIN/wa -> $SCRIPT_DIR/wa"
fi

# Register tab-completion for wa
if command -v register-python-argcomplete &>/dev/null; then
    eval "$(register-python-argcomplete wa)"
    echo "Tab-completion registered for 'wa'"
else
    echo "Warning: register-python-argcomplete not found, skipping completion setup"
fi

echo "Setup complete. Run 'wa --help' to get started."