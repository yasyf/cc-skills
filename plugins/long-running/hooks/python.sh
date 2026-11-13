python=python3
unread=$PATH:
while [ -n "$unread" ]; do
  folder=${unread%%:*}
  unread=${unread#*:}
  case $folder in
    */shims) continue ;;
  esac
  if [ -x "$folder/python3" ]; then
    python=$folder/python3
    break
  fi
done
