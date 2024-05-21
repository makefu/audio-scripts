#!/bin/sh

# find . -mindepth 2 -maxdepth 2 -type d '!' -exec test -e "{}/cover.jpg" ';' -print

set -eu
URL=${2?please provide url to picture}
DIR=${1?please provide dir path to directory}
TMP=$(mktemp)

wget "$URL" -O "$TMP"

#ext=$(file --extension "$TMP")
cover=$DIR/cover.jpg
cover_resized=$DIR/cover_resized.jpg

convert "$TMP" "$DIR/cover.jpg"
trap 'rm -f "$TMP"' INT TERM EXIT

convert "$cover" -resize 350x350 "$cover_resized"
for j in "$DIR/"*.mp3;do
  echo "handling $j"
  mid3v2 -p "$cover_resized" "$j"
done
