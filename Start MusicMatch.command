#!/bin/bash
# macOS: double-click to open MusicMatch. First run sets everything up.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
    echo "First run: setting up MusicMatch..."
    python3 -m venv .venv || { echo "Install Python 3.10+ from https://www.python.org/downloads/"; read -r; exit 1; }
fi
# Install requirements on first run and whenever requirements.txt changes (after a git pull).
if ! cmp -s requirements.txt .venv/requirements.installed; then
    echo "Installing MusicMatch's requirements..."
    .venv/bin/pip install -q -r requirements.txt && cp requirements.txt .venv/requirements.installed
fi
if ! .venv/bin/python -c "import tkinter" 2>/dev/null; then
    echo "This Python has no Tk. Install Python from python.org, or run: brew install python-tk"
    read -r
    exit 1
fi
nohup .venv/bin/python -m musicmatch.gui >/dev/null 2>&1 &
osascript -e 'tell application "Terminal" to close front window' >/dev/null 2>&1 &
