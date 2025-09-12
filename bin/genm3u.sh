#!/usr/bin/env nix-shell
#!nix-shell -p dos2unix  -i bash

set -eu
cd "${1:-.}"
tmp=$(mktemp)
for i in */Album/* */Album/*/CD*;do
  echo "folder $i";
  test -d "$i" || continue
  cd "$i"
  if ! ls ./*.mp3 >/dev/null;then
    echo "no mp3 files in $i, doing nothing"
    cd -
    continue
  fi
  rm -f ./*.m3u
  #mv "$(basename "$i")" "$(basename "$i")".m3u
  m3ufile=playlist.m3u
  cat > "$tmp" <<EOF
#EXTM3U
#PLAYLIST:$(basename "$i")
#EXTENC: ISO8859-1
#EXTIMG:cover.jpg
EOF
  ls *.mp3 >> "$tmp"
  if ! iconv -f UTF-8 -t ISO8859-1 "$tmp" -o "$m3ufile";then
    echo "emergency, cannot convert data from $i, falling back"
    cp "$tmp" "$m3ufile"
  fi
  unix2dos "$m3ufile"
  cd -
done
