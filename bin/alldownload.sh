#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
OUTDIR=${1:-/media/silent/music/kinder/podcasts}
"$HERE/gendownload.sh" "$OUTDIR/checkpod"  "CheckPod - Der Podcast mit Checker Tobi"  'https://feeds.br.de/checkpod-der-podcast-mit-checker-tobi/feed.xml'
"$HERE/gendownload.sh" "$OUTDIR/die.maus.zum.hoeren" "Die Maus zum Hören"  'https://kinder.wdr.de/radio/diemaus/audio/diemaus-60/diemaus-60-106.podcast'
#./gendownload.sh "$OUTDIR/purplus" "Purplus Podcast"  'https://purplus-podcast.podigee.io/feed/mp3'
