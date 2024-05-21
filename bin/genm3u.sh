#!/usr/bin/env nix-shell
#!nix-shell -p dos2unix  -i bash
cd "${1:-.}"
tmp=$(mktemp)
for i in *;do
  echo "folder $i";
  test -d "$i" || continue
  cd "$i"
  rm *.m3u
  #mv "$(basename "$i")" "$(basename "$i")".m3u
  m3ufile="$(basename "$i")".m3u
  cat > "$tmp" <<EOF
#EXTM3U
#EXTENC: ISO8859-1
#EXTIMG:cover.jpg
#PLAYLIST:$i
EOF
  ls *.mp3 >> "$tmp"
  iconv -f UTF-8 -t ISO8859-1 "$tmp" -o "$m3ufile"
  unix2dos "$m3ufile"
  cd -
done
