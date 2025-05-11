#!/bin/sh
set -eu
OUTDIR="${1?must provide outdir}"
NAME="${2?must provide name}"
FEED="${3?must provide feed url}"
tmp=$(mktemp)

# find the latest podcast and create a file in the output directory
latest_podcast_date=0
latest_podcast="no-such-podcast"
latest_podcast_playlist="aktuelle_folge.m3u"
archive_file=$OUTDIR/archive.txt

trap "rm -f '$tmp'" INT TERM EXIT
yt-dlp --download-archive "$archive_file" --write-thumbnail --write-description --write-info-json -P "$OUTDIR" -o "$NAME - %(title)s/Podcast.%(ext)s" "$FEED"
echo "yt-dlp finished, transforming folders"

for i in "$OUTDIR/$NAME"*;do
  name=$(basename "$i")
  m3ufile="$name".m3u
  title=$(echo "$name" |sed "s/$NAME - //")
  echo "handling $name"
  cd "$i"

  if test "$(ls *.mp3 2>/dev/null | wc -l)" -eq  0;then
    echo "$i does not contain any usable audio files, removing"
    cd -
    rm -vrf "$i"
    continue
  fi


  this_podcast_date=$(jq -r .upload_date *.json)
  if test "$this_podcast_date" '>' "$latest_podcast_date";then
    latest_podcast=$name
    latest_podcast_date=$this_podcast_date
    echo "[II] found latest podcast $i ($name @ $this_podcast_date)"
  fi

  if test -e folder.jpg;then
    echo "[WW] $m3ufile already exists, skipping album"
    cd -
    continue
  fi

  echo "genm3u for folder $name"
  rm -f *.m3u *.m3u8
  # ISO8859-1 is required for sonos to correctly load the playlist (not anymore
  cat > "$tmp" <<EOF
#EXTM3U
#EXTENC: ISO8859-1
#EXTIMG:folder.jpg
#PLAYLIST:${name}
EOF
  ls *.mp3 >> "$tmp"

  echo "generating cover file"
  rm -f cover_resized.jpg
  if ! ls *.jp*g; then
    echo "[WW] found no images in folder $OUTDIR/$NAME, skipping cover generation"
    continue
  fi
  test -e cover.jpg || mv -v *.jp*g cover.jpg

  convert cover.jpg -resize 350x350 cover_resized.jpg
  cp cover_resized.jpg folder.jpg

  for j in *.mp3;do
    echo "Injecting cover into $j"
    mid3v2 --TPE2 "$NAME" "$j"
    mid3v2 -a "$NAME" -A "$title | $NAME" -t "$title" -p "cover_resized.jpg" "$j" 
  done

  iconv  -f UTF-8 -t 'ISO8859-1//TRANSLIT' "$tmp" -o "$m3ufile"
      
  unix2dos "$m3ufile" 2>/dev/null

  echo "finished $i"
  cd - >/dev/null 2>&1
done

cd "$OUTDIR"
echo "creating playlist with latest podcast"
cat > "$tmp" <<EOF
#EXTM3U
#EXTENC: ISO8859-1
#EXTIMG:$latest_podcast/folder.jpg
#PLAYLIST:Neuste Sendung
EOF
ls "$latest_podcast/"*.mp3 >> "$tmp"
iconv  -f UTF-8 -t 'ISO8859-1//TRANSLIT' "$tmp" -o "$latest_podcast_playlist"
unix2dos "$latest_podcast_playlist" 2>/dev/null

echo "all done"
