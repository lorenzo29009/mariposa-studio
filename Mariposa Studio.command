#!/bin/bash
cd "$(dirname "$0")"
STUDIO_DIR="$(pwd)"

if [ ! -x "./venv/bin/python" ]; then
    # This copy isn't installed — but the installer relocates installs out of
    # Desktop/Documents/Downloads into ~/Applications, so a finished install
    # may exist there (e.g. the user re-downloaded the zip). Hand off to it
    # instead of telling them to reinstall something they already installed.
    for ALT in "$HOME/Applications/$(basename "$STUDIO_DIR")" "$HOME/Applications"/Mariposa*Studio*; do
        if [ "$ALT" != "$STUDIO_DIR" ] && [ -x "$ALT/venv/bin/python" ]; then
            cd "$ALT"
            exec "$ALT/venv/bin/python" "$ALT/src/studio.py"
        fi
    done
    clear
    echo "===================================================="
    echo " Mariposa Studio is not installed yet."
    echo "===================================================="
    echo ""
    echo " Right-click  install-mac.command  →  Open"
    echo " (the first time, so macOS lets you run it)"
    echo ""
    read -p "Press Enter to close..."
    exit 1
fi

# Run the app without keeping the terminal window around. Absolute paths so the
# interpreter never needs getcwd() (which fails under some Finder/iCloud launch
# contexts and crashes CPython's getpath before any app code runs).
exec "$STUDIO_DIR/venv/bin/python" "$STUDIO_DIR/src/studio.py"
