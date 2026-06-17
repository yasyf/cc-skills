#!/usr/bin/env bash
# Render an evp .tape, transparently choosing native execution or Docker.
#
#   evp-run.sh <tape> [--workdir DIR] [--install "CMD"]
#                     [--mount-bin HOSTPATH[:NAME]] [--base IMAGE]
#
# <tape>         Tape path, RELATIVE to --workdir (e.g. .cli-demo/demo.tape). The
#                tape's Output/Screenshot paths are likewise relative to --workdir.
# --workdir DIR  Directory mounted at /work and used as cwd (default: $PWD, the repo
#                root) so the demoed CLI can see project files.
# --install CMD  Shell command that installs the demoed CLI inside the container
#                (e.g. "pip install httpie"). Baked into a cached per-demo image.
# --mount-bin    A linux/amd64 binary on the host to mount onto the container PATH,
#                instead of installing one. "path" or "path:name".
# --base IMAGE   Override the base image (advanced).
#
# Decision: run evp natively only on Linux/x86_64 (the one platform evp ships a
# binary for). Everywhere else — macOS, Linux/arm64 — render inside a linux/amd64
# container built from that same static musl binary.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EVP_VERSION="${EVP_VERSION:-0.13.0}" # keep in sync with install-binary.sh

die() {
  echo "evp-run.sh: $*" >&2
  exit 1
}
_sha256() { if command -v sha256sum >/dev/null 2>&1; then sha256sum; else shasum -a 256; fi; }

TAPE=""
WORKDIR="$PWD"
INSTALL_CMD=""
MOUNT_BIN_SPEC=""
BASE_OVERRIDE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --workdir) WORKDIR="$2"; shift 2 ;;
    --install) INSTALL_CMD="$2"; shift 2 ;;
    --mount-bin) MOUNT_BIN_SPEC="$2"; shift 2 ;;
    --base) BASE_OVERRIDE="$2"; shift 2 ;;
    -*) die "unknown flag: $1" ;;
    *) [ -z "$TAPE" ] && TAPE="$1" || die "unexpected argument: $1"; shift ;;
  esac
done
[ -n "$TAPE" ] || die "usage: evp-run.sh <tape> [--workdir DIR] [--install CMD] [--mount-bin SPEC]"
WORKDIR="$(cd "$WORKDIR" && pwd)"
[ -f "$WORKDIR/$TAPE" ] || die "tape not found: $WORKDIR/$TAPE"
mkdir -p "$WORKDIR/$(dirname "$TAPE")"

# Always resolve (download if needed) the musl binary: native exec uses it directly,
# Docker bakes it into the base image.
EVP_BIN="$(bash "$SCRIPT_DIR/install-binary.sh")"

native_ok() {
  [ "$(uname -s)" = "Linux" ] || return 1
  case "$(uname -m)" in x86_64 | amd64) ;; *) return 1 ;; esac
  "$EVP_BIN" --version >/dev/null 2>&1
}

if native_ok; then
  cd "$WORKDIR"
  exec "$EVP_BIN" "$TAPE"
fi

# ---------------------------------------------------------------------------
# Docker path
# ---------------------------------------------------------------------------
command -v docker >/dev/null 2>&1 ||
  die "evp has no native binary for $(uname -s)/$(uname -m), so rendering needs Docker. Install Docker Desktop, or run on a Linux x86_64 host."
docker info >/dev/null 2>&1 ||
  die "Docker is installed but its daemon isn't responding — start Docker Desktop and retry."

BASE_IMG="${BASE_OVERRIDE:-cli-demo-evp:${EVP_VERSION}}"
if ! docker image inspect "$BASE_IMG" >/dev/null 2>&1; then
  echo "evp-run.sh: building base image $BASE_IMG (one-time; slow under emulation)…" >&2
  docker build --platform=linux/amd64 -t "$BASE_IMG" \
    -f "$SCRIPT_DIR/Dockerfile.base" "$(dirname "$EVP_BIN")" >&2
fi

if [ -n "$INSTALL_CMD" ]; then
  hash="$(printf '%s' "${BASE_IMG}::${INSTALL_CMD}" | _sha256 | cut -c1-12)"
  IMAGE="cli-demo-demo:${hash}"
  if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "evp-run.sh: building demo image $IMAGE (installs: $INSTALL_CMD)…" >&2
    docker build --platform=linux/amd64 -t "$IMAGE" \
      --build-arg BASE="$BASE_IMG" --build-arg INSTALL_CMD="$INSTALL_CMD" \
      -f "$SCRIPT_DIR/Dockerfile.demo" "$SCRIPT_DIR" >&2
  fi
else
  IMAGE="$BASE_IMG"
fi

# Optional: mount a prebuilt linux/amd64 binary onto PATH (must NOT be a macOS Mach-O
# binary — that yields "exec format error" inside the linux/amd64 container).
mount_args=()
if [ -n "$MOUNT_BIN_SPEC" ]; then
  host="${MOUNT_BIN_SPEC%%:*}"
  name="${MOUNT_BIN_SPEC#*:}"
  [ "$name" = "$MOUNT_BIN_SPEC" ] && name="$(basename "$host")"
  case "$host" in /*) ;; *) host="$PWD/$host" ;; esac
  [ -e "$host" ] || die "--mount-bin: host path not found: $host"
  mount_args=(-v "$host:/usr/local/bin/$name:ro")
fi

set +e
docker run --rm --platform=linux/amd64 \
  --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$WORKDIR:/work" -w /work \
  "${mount_args[@]+"${mount_args[@]}"}" \
  "$IMAGE" evp "$TAPE"
rc=$?
set -e
if [ "$rc" -ne 0 ]; then
  echo "evp-run.sh: evp render failed (exit $rc)." >&2
  echo "  • 'exec format error' → enable amd64 emulation (Linux/arm64 hosts):" >&2
  echo "      docker run --privileged --rm tonistiigi/binfmt --install amd64" >&2
  echo "  • a tool/display/GPU error → the demoed CLI may have failed; check the tape's" >&2
  echo "    commands and the --install step, then re-run." >&2
  exit "$rc"
fi
