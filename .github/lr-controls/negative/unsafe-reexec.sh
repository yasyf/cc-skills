source "$(dirname "$0")/../lib.sh"
checkout .ci-unsafe-reexec-baseline 35fa995a3f249a26137b9876a42052b7fd86fde7
baseline=.ci-unsafe-reexec-baseline/plugins/long-running
cp plugins/long-running/tests/test_board_watch.py "$baseline/tests/test_board_watch.py"
cp plugins/long-running/tests/test_owned_reexec.py "$baseline/tests/test_owned_reexec.py"
i=0
for node in "$baseline/tests/test_board_watch.py::test_a_reexecuted_watch_ends_the_streams_its_older_image_started" "$baseline/tests/test_owned_reexec.py::test_reexec_does_not_signal_unregistered_inherited_children"; do
  i=$((i + 1))
  negative="$RUNNER_TEMP/unsafe-inherited-reexec-$i.log"
  if uv run --locked --project "$baseline" pytest "$node" -q >"$negative" 2>&1; then
    cat "$negative"
    echo "unsafe inherited-child implementation unexpectedly passed"
    exit 1
  fi
  cat "$negative"
  grep -F 'inherited protected child was terminated or changed' "$negative"
  grep -F '1 failed' "$negative"
done
