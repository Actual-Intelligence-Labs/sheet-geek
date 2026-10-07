#!/bin/sh
# Sheet Geek hooks: run only when a working Python 3.10+ is on PATH and the hook file is there;
# otherwise do nothing. Always exit 0, so a hook can never block a prompt or a session.
h="${CLAUDE_PLUGIN_ROOT}/hooks/hook.py"
[ -f "$h" ] || exit 0
for py in python3 python; do
  if "$py" -c "import sys; sys.exit(sys.version_info < (3, 10))" >/dev/null 2>&1; then
    "$py" "$h" "$1"
    exit 0
  fi
done
exit 0
