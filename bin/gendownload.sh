#!/bin/sh
set -eu
OUTDIR="${1?must provide outdir}"
NAME="${2?must provide name}"
FEED="${3?must provide feed url}"
tmp=$(mktemp)

# find the latest podcast and create a file in the output directory
latest_podcast_playlist="$OUTDIR/aktuelle_folge.m3u"
archive_file=$OUTDIR/archive.txt

trap "rm -f '$tmp'" INT TERM EXIT
yt-dlp --download-archive "$archive_file" --write-thumbnail --write-description --write-info-json -P "$OUTDIR" -o "%(upload_date)s %(title)s.%(ext)s" "$FEED"
echo "yt-dlp finished, transforming folders"

for i in "$OUTDIR/"*.info.json;do
  base_file=${i//.info.json}
  name=$(basename "$base_file" | cut -d"_" -f2- )
  this_podcast_date=$(jq -r .upload_date "$i")
  audiofile=${base_file}.mp3
  echo "handling $name"
  if ! test -e "$audiofile";then
    echo "$i does not have any $name"
    continue
  fi
  echo touch -a -m -t "${this_podcast_date}1200" "$audiofile"
  touch -t "${this_podcast_date}1200" "$audiofile"
  convert "${base_file}".jp*g -resize 600x600 "${base_file}.cover.jpg"
  mid3v2 --delete-frames=APIC --TPE2 "$NAME" -a "$NAME" -A "$NAME" -t "$name" -p "${base_file}.cover.jpg" "$audiofile" 

done

echo "creating playlist with latest podcast"
latest_podcast=$(ls -1 "$OUTDIR"/*.mp3 | sort -n| head -n1)
latest_podcast_name=$(basename "${latest_podcast//.mp3}")

cat > "$tmp" <<EOF
#EXTM3U
#EXTENC: ISO8859-1
#EXTIMG: ${latest_podcast_name}.cover.jpg
#PLAYLIST: ${latest_podcast_name} | $NAME
${latest_podcast_name}.mp3
EOF

iconv  -f UTF-8 -t 'ISO8859-1//TRANSLIT' "$tmp" -o "$latest_podcast_playlist"
unix2dos "$latest_podcast_playlist" 2>/dev/null

echo "all done"
