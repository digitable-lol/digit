#!/usr/bin/env bash
# Check the host, start Blender on a virtual display, verify the MCP port.
# Safe to re-run: it starts nothing that is already running.
set -uo pipefail

REPO="${BLENDER_MCP_HOME:-$HOME/src/blender-mcp}"
PORT="${BLENDERMCP_PORT:-9876}"
UPSTREAM="https://github.com/digitable-lol/blender-mcp"

ok()   { printf '  \033[32mok\033[0m   %s\n' "$1"; }
warn() { printf '  \033[33mwarn\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mfail\033[0m %s\n' "$1"; }

echo "Blender MCP setup"
echo

# ---- 1. Blender -------------------------------------------------------------
BLENDER="${BLENDER:-$(command -v blender || echo "$HOME/opt/blender/blender")}"
if [[ -x "$BLENDER" ]]; then
  ok "Blender: $("$BLENDER" --version 2>/dev/null | head -1) ($BLENDER)"
else
  fail "no Blender at '$BLENDER'"
  echo "       Install Blender 4.5 LTS or newer, or set BLENDER=/path/to/blender."
  echo "       It must be the GUI build — the addon cannot run in background mode."
  exit 1
fi

# ---- 2. Virtual display (headless hosts only) -------------------------------
if [[ -n "${DISPLAY:-}" ]]; then
  ok "DISPLAY=$DISPLAY — a real display is available"
elif command -v xvfb-run >/dev/null; then
  ok "xvfb-run present — headless launch supported"
else
  fail "no DISPLAY and no xvfb-run"
  echo "       Install it:  sudo apt install xvfb"
  exit 1
fi

# ---- 3. uvx -----------------------------------------------------------------
if UVX="$(command -v uvx)"; then
  ok "uvx: $UVX"
else
  fail "uvx not found — install uv (https://docs.astral.sh/uv/)"
  exit 1
fi

# ---- 4. The launcher --------------------------------------------------------
if [[ -x "$REPO/run.sh" ]]; then
  ok "launcher: $REPO/run.sh"
else
  warn "no launcher at $REPO"
  echo "       git clone $UPSTREAM \"$REPO\""
  echo "       (or set BLENDER_MCP_HOME to an existing clone)"
  exit 1
fi

# ---- 5. Start ---------------------------------------------------------------
echo
if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
  ok "already listening on port $PORT"
else
  echo "  starting Blender (first boot takes a few seconds)..."
  BLENDER="$BLENDER" "$REPO/run.sh" start || { fail "start failed — $REPO/run.sh log"; exit 1; }
fi

if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
  ok "MCP port $PORT is open"
else
  fail "port $PORT never opened — check $REPO/run.sh log"
  exit 1
fi

# ---- 6. What is left to do --------------------------------------------------
cat <<EOF

Register the MCP server with your agent (once, user scope):

  claude mcp add blender --scope user \\
    -e DISABLE_TELEMETRY=1 -e BLENDER_MCP_DISABLE_TELEMETRY=1 -e MCP_DISABLE_TELEMETRY=1 \\
    -- "$UVX" blender-mcp@1.6.4

The absolute uvx path matters: a desktop client may not inherit your shell PATH.
The three env vars switch off upstream telemetry, which is on by default and
transmits prompts, code and viewport screenshots to a third party.

Then verify from the agent: ask it for a viewport screenshot. It must come back
in colour. A uniformly black image means the addon is unpatched and reading the
window's front buffer — see references/headless.md.
EOF
