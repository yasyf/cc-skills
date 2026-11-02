source "$(dirname "$0")/../lib.sh"
checkout .ci-ruling-binary-baseline c729f3d928531710b7cdc0b5137c099058a01460
baseline=.ci-ruling-binary-baseline/plugins/long-running
cp plugins/long-running/tests/test_ruling_binary_reader.py "$baseline/tests/test_ruling_binary_reader.py"
for caller in reader index readback classify; do
  case "$caller" in
    reader) node=test_ruling_reader_binary_prefix ;;
    index) node=test_document_index_binary_prefix ;;
    readback) node=test_readback_sweeper_binary_prefix ;;
    classify) node=test_classify_sweeper_binary_prefix ;;
  esac
  negative="$RUNNER_TEMP/ruling-binary-$caller-negative.log"
  if uv run --locked --project plugins/long-running pytest -c "$baseline/pyproject.toml" "$baseline/tests/test_ruling_binary_reader.py::$node[large]" -q -s > "$negative" 2>&1; then
    cat "$negative"
    exit 1
  fi
  cat "$negative"
  grep -F "binary ruling $caller read" "$negative"
  grep -F '1 failed' "$negative"
done
