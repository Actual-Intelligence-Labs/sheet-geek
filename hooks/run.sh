#!/bin/sh
# Sheet Geek hooks: run only when a working Python 3.10+ is on PATH; otherwise do nothing.
for py in python3 python; do
  if "$py" -c "import sys; sys.exit(sys.version_info < (3, 10))" >/dev/null 2>&1; then
    exec "$py" "${CLAUDE_PLUGIN_ROOT}/skills/sheet-geek/scripts/sb.py" hook "$1"
  fi
done
exit 0
