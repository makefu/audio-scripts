#!/bin/sh
set -eu

export ABCDETEMPDIR=$PWD
cd "$(dirname "$(readlink -f "$0")")"
cdrom=/dev/sr0
if ! test -e $cdrom;then
  cdrom=/dev/sr1
fi
abcde -d "$cdrom" -V -x -c lol.conf  -o mp3 -a "cddb,read,embedalbumart,getalbumart,encode,tag,move"
for i in hoerbucher/*/albumart_backup/cover.jpg;do
  echo "found backup cover in $i"
  echo "generating album.m3u"
  mv -v "$i" "$(echo "$i" | sed 's#albumart_backup/##')"
done
rmdir hoerbucher/*/albumart_backup
