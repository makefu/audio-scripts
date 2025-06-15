#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
OUTDIR=${1:-/media/silent/music/kinder/podcasts}
"$HERE/gendownload.sh" "$OUTDIR/checkpod"  "CheckPod"  'https://feeds.br.de/checkpod-der-podcast-mit-checker-tobi/feed.xml'
"$HERE/gendownload.sh" "$OUTDIR/die.maus.zum.hoeren" "Die Maus zum Hören"  'https://kinder.wdr.de/radio/diemaus/audio/diemaus-60/diemaus-60-106.podcast'
"$HERE/gendownload.sh" "$OUTDIR/anna.und.die.wilden.tiere" "Anna und die wilden Tiere"  'https://feeds.br.de/anna-und-die-wilden-tiere/feed.xml'
"$HERE/gendownload.sh" "$OUTDIR/lachlabor" "Lachlabor"  'https://feeds.br.de/lachlabor/feed.xml'
"$HERE/gendownload.sh" "$OUTDIR/alle.gegen.nico" "Alle gegen Nico"  'https://feeds.br.de/alle-gegen-nico-zockt-um-die-quizkrone/feed.xml'
#./gendownload.sh "$OUTDIR/purplus" "Purplus Podcast"  'https://purplus-podcast.podigee.io/feed/mp3'
