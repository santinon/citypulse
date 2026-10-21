#!/bin/sh
# Runs guard.py with the first working Python 3.9+ (python3, then python), passing the
# hook JSON through stdin. Blocks (exit 2) when no interpreter works, so a missing Python
# never silently disables the policy. On Windows, `python3` may be a Microsoft Store stub
# that fails the version probe; the loop then falls back to `python`.
dir=$(dirname "$0")
for py in python3 python; do
    if command -v "$py" >/dev/null 2>&1 &&
        "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
        exec "$py" -I "$dir/guard.py"
    fi
done
echo "BLOCKED: .agent-policy/guard.sh found no Python 3.9+ interpreter." >&2
exit 2
