#!/bin/bash
set -euo pipefail

home=$(getent passwd desk | cut -d: -f6)
bin=$home/.local/bin
joined=$home/.relay-joined
region=us-west-2

read_parameter() {
  aws ssm get-parameter --region "$region" --name "$1" --with-decryption --query Parameter.Value --output text
}

deployment=$(read_parameter /ci/desk-host/deployment)
if [ "$deployment" = "{}" ]; then
  echo "desk-sync: nothing deployed yet"
  exit 0
fi

work=$(mktemp -d)
chmod 711 "$work"
trap 'rm -rf "$work"' EXIT

install_tool() {
  local name=$1 url sha member
  url=$(jq -er ".$name.url" <<<"$deployment")
  sha=$(jq -er ".$name.sha256" <<<"$deployment")
  member=$(jq -er ".$name.member" <<<"$deployment")
  curl -fsSL "$url" -o "$work/$name.tgz"
  if ! echo "$sha  $work/$name.tgz" | sha256sum -c --status -; then
    echo "desk-sync: refused $url, it is not the archive with SHA-256 $sha" >&2
    exit 1
  fi
  tar -xzOf "$work/$name.tgz" "$member" >"$work/$name"
  chmod 755 "$work/$name"
  "$work/$name" --version
  runuser -u desk -- install -m 755 "$work/$name" "$bin/$name.new"
  runuser -u desk -- mv "$bin/$name.new" "$bin/$name"
}

install_tool cci
install_tool lr

if [ ! -e "$joined" ]; then
  CCI_SERVICE_KEY=$(read_parameter /ci/desk-host/cci-service-key)
  export CCI_SERVICE_KEY
  runuser -u desk -- env HOME="$home" "$bin/cci" relay join --instance \
    --drive "$(jq -er .drive <<<"$deployment")" \
    --url "$(jq -er .relay_url <<<"$deployment")" \
    --owner "$(jq -er .relay_owner <<<"$deployment")" \
    --ttl 24h
  unset CCI_SERVICE_KEY
  runuser -u desk -- touch "$joined"
fi

systemctl enable cci.service
systemctl restart cci.service
systemctl try-restart 'desk@*'
