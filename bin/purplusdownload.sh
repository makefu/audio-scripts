#!/bin/sh
set -eu
FEED=https://www.zdf.de/kinder/purplus/wissendrin-mit-eric-der-purplus-podcast-100.html
NAME='Pur+ Wissendrin mit Eric'
OUTDIR="$(dirname "$(readlink -f "$0")")"/hoerbucher
tmp=$(mktemp)
trap "rm -f '$tmp'" INT TERM EXIT
yt-dlp --write-thumbnail --write-description --write-info-json -P "$OUTDIR" -o "$NAME - %(title)s/Podcast.%(ext)s" "$FEED"
echo "yt-dlp finished, transforming folders"

for i in "$OUTDIR/$NAME"*;do
  name=$(basename "$i")
  title=$(echo "$name" |sed "s/$NAME - //")
  echo "handling $name"
  cd "$i"

  if test "$(ls *.mp3 2>/dev/null | wc -l)" -eq  0;then
    echo "$i does not contain any usable audio files, removing"
    cd -
    rm -vrf "$i"
    continue
  fi

  rm -f cover_resized.jpg
  test -e cover.jpg || mv -v *.jpg cover.jpg

  convert cover.jpg -resize 350x350 cover_resized.jpg

  for j in *.mp3;do
    echo "Injecting cover into $j"
    mid3v2 --TPE2 "$NAME" "$j"
    mid3v2 -a "$NAME" -A "$title | $NAME" -t "$title" -p "cover_resized.jpg" "$j" 
  done

  echo "genm3u for folder $name"
  rm -f *.m3u
  m3ufile="$name".m3u
  # ISO8859-1 is required for sonos to correctly load the playlist
  cat > "$tmp" <<EOF
#EXTM3U
#EXTENC: ISO8859-1
#EXTIMG:cover.jpg
#PLAYLIST:${name}
EOF
  ls *.mp3 >> "$tmp"
  iconv  -f UTF-8 -t 'ISO8859-1//TRANSLIT' "$tmp" -o "$m3ufile"
      
  unix2dos "$m3ufile" 2>/dev/null

  echo "finished $i"
  cd - >/dev/null 2>&1
done

echo "all done"
