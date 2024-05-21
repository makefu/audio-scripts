#!/bin/sh
set -eu

for i in "$@";do
  find "$i" -name cover.jpg | while read line;do
    bdir=$(dirname "$line")
    cd "$bdir"
    test -e cover_resize.jpg || convert cover.jpg -resize 350x350 cover_resize.jpg
    for j in *.mp3;do
      echo "handling $j"
      if mid3v2 "$j" | grep '^APIC';then
        echo "skipping $j , APIC already exists"
        echo "skipping whole folder"
        break
      else
        if mid3v2 -p cover_resize.jpg "$j";then
          echo "successfully added cover to $j"
        else
          echo "error while trying to add cover to $j"
        fi
      fi
    done
    cd -
  done
done
