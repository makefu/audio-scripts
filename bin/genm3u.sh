#!/usr/bin/env nix-shell
#!nix-shell -p dos2unix  -i bash
cd "${1:-.}"
tmp=$(mktemp)
chmod 0755 "$tmp"
find -name cover.jpg | while read line; do
  i=$(dirname "$line")
  echo "folder '$i'";
  test -d "$i" || continue
  cd "$i"
  rm -f *.m3u *.m3u8
  #mv "$(basename "$i")" "$(basename "$i")".m3u
  m3ufile="$(basename "$i")".m3u
  cat > "$tmp" <<EOF
#EXTM3U
#PLAYLIST:$(basename "$i")
#EXTIMG:cover.jpg
EOF
  ls *.mp3 >> "$tmp"
  cp "$tmp" "$m3ufile"
  cp "$tmp" "playlist.m3u"
  cp "$tmp" "playlist.m3u8"
  #iconv -f UTF-8 -t ISO8859-1 "$tmp" -o "$m3ufile"
  #unix2dos "$m3ufile"
  cd - >/dev/null 2>&1
done
