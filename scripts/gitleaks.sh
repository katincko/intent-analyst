#!/usr/bin/env bash
# Runs gitleaks over the whole git history and over the working tree (so a secret that is not
# committed yet is caught too). The binary is pinned by version and checked against a SHA-256
# before it runs; nothing is installed outside .work/.
set -euo pipefail

VERSION="8.30.1"
BASE="https://github.com/gitleaks/gitleaks/releases/download/v${VERSION}"
SHA256_LINUX_X64="551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"
SHA256_WINDOWS_X64="d29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e"

root="$(cd "$(dirname "$0")/.." && pwd)"
dir="$root/.work/gitleaks-$VERSION"
case "$(uname -s)" in
  Linux*)  archive="gitleaks_${VERSION}_linux_x64.tar.gz";  want="$SHA256_LINUX_X64";   bin="gitleaks" ;;
  MINGW*|MSYS*|CYGWIN*) archive="gitleaks_${VERSION}_windows_x64.zip"; want="$SHA256_WINDOWS_X64"; bin="gitleaks.exe" ;;
  *) echo "unsupported platform: $(uname -s)" >&2; exit 2 ;;
esac

if [ ! -x "$dir/$bin" ]; then
  mkdir -p "$dir"
  curl -fsSL "$BASE/$archive" -o "$dir/$archive"
  got="$(sha256sum "$dir/$archive" | cut -d' ' -f1)"
  if [ "$got" != "$want" ]; then
    echo "SHA-256 mismatch for $archive: got $got, want $want" >&2
    rm -f "$dir/$archive"
    exit 1
  fi
  case "$archive" in
    *.tar.gz) tar -xzf "$dir/$archive" -C "$dir" "$bin" ;;
    *.zip)    unzip -oq "$dir/$archive" "$bin" -d "$dir" ;;
  esac
fi

cd "$root"
"$dir/$bin" git --no-banner --redact --config .gitleaks.toml .
"$dir/$bin" dir --no-banner --redact --config .gitleaks.toml .
