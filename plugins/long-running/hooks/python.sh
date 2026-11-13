python=python3
recorded=${LONG_RUNNING_STATE:-${XDG_STATE_HOME:-$HOME/.local/state}/long-running}/interpreter
if [ -r "$recorded" ] && read -r kept < "$recorded" && [ -x "$kept" ]; then
  python=$kept
fi
