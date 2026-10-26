#!/usr/bin/env bash
# Setup script for whatsapp-form-automation
# Source this file to enable tab-completion and PATH setup:
#   source setup.sh
# Or run it directly:
#   bash setup.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_BIN="$SCRIPT_DIR/.venv/bin"

# Remove old alias that interferes with argcomplete
if grep -q 'alias wa=' ~/.bashrc 2>/dev/null; then
    sed -i '/alias wa=/d' ~/.bashrc
fi

# Add venv bin to PATH if not already there
if [[ ":$PATH:" != *":$VENV_BIN:"* ]]; then
    export PATH="$VENV_BIN:$PATH"
fi

# Create a symlink for wa in the venv bin so it's findable for completion
if [[ ! -L "$VENV_BIN/wa" ]]; then
    ln -sf "$SCRIPT_DIR/wa" "$VENV_BIN/wa"
fi

# Register tab-completion for wa using the built-in completion command
if command -v wa &>/dev/null; then
    eval "$(wa completion bash)"
fi

# Only print messages when run directly (not sourced)
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "Setup complete. Run 'wa --help' to get started."
fi