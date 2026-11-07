#!/bin/zsh
set -e
cd "${0:A:h}"
exec ../.venv/bin/python tools/start_mac_monitor.py "$@"
