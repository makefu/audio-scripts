#!/bin/sh
cd "$(dirname "$(readlink -f "$0")")/hoerbucher" || exit 1
FILTER=${1:-}
echo -n '{{[' ; for i in *; do
  # blacklist
  if echo "$i" | grep -E '(Feuerwehrmann Sam|Peppa Pig|Peppa Wutz|Leo Lausemaus|Reisemaus)' >/dev/null;then
    continue
  fi
  if test -n "${1:-}";then
    if echo "$i" | grep -E "$FILTER">/dev/null 2>&1;then
      # echo "$i matches filter $1" >&2
      :
    else
      continue
    fi
  fi
  echo -n "'${i/\'/\'\'}'",;
done
echo -n '] | random }}'

