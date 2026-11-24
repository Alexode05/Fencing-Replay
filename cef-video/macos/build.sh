#!/bin/sh
set -eu

if [ "${1-}" = "--help" ]; then
    printf 'Usage: %s [chemin/CEF Vidéo.app]\n' "$0"
    printf 'Sans argument, l’application est construite dans un dossier temporaire.\n'
    exit 0
fi

macos_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
replay_source=$(dirname -- "$macos_dir")
if [ "$#" -eq 0 ]; then
    output_dir=$(mktemp -d "${TMPDIR:-/tmp}/cef-replay.XXXXXX")
    output_app="$output_dir/CEF Vidéo.app"
elif [ "$#" -eq 1 ]; then
    output_app=$1
    output_dir=$(dirname -- "$output_app")
else
    printf 'Usage: %s [chemin/CEF Vidéo.app]\n' "$0" >&2
    exit 2
fi

case "$output_app" in
    *.app) ;;
    *) printf 'Le chemin doit se terminer par .app.\n' >&2; exit 2 ;;
esac

if [ -e "$output_app" ] || [ -L "$output_app" ]; then
    printf 'Ce chemin existe déjà : %s\n' "$output_app" >&2
    exit 1
fi

build_dir=$(mktemp -d "$output_dir/.cef-replay.XXXXXX")
trap 'rm -rf "$build_dir"' EXIT
staged_app="$build_dir/CEF Vidéo.app"
resources="$staged_app/Contents/Resources"
mkdir -p "$resources" "$staged_app/Contents/MacOS"

cp "$macos_dir/Info.plist" "$staged_app/Contents/Info.plist"
cp "$replay_source/serveur_replay.py" "$replay_source/cef_upload.py" "$replay_source/lecteur.html" "$resources/"
cp -R "$replay_source/assets" "$resources/assets"

xcrun --sdk macosx swiftc -parse-as-library -target "$(uname -m)-apple-macosx12.3" \
    -module-cache-path "$build_dir/modules" -O "$macos_dir/ReplayApp.swift" \
    -o "$staged_app/Contents/MacOS/cef-replay"
codesign --force --sign - "$staged_app" >&2
mv "$staged_app" "$output_app"
printf '%s\n' "$output_app"
