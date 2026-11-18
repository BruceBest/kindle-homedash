#!/bin/bash
# Fill Kindle FAT32 storage leaving ~70 MB free (FAT32 max file < 4 GiB)
set -u
DIR=/mnt/fill_disk
TARGET_FREE_MB=70
mkdir -p "$DIR"
i=0
while true; do
  free_mb=$(( $(df --output=avail -k /mnt | tail -1) / 1024 ))
  need=$(( free_mb - TARGET_FREE_MB ))
  [ "$need" -le 0 ] && break
  chunk=$(( need > 3800 ? 3800 : need ))
  i=$((i+1))
  echo "$(date +%T) file $i: ${chunk} MB (free before: ${free_mb} MB)"
  dd if=/dev/zero of="$DIR/fill_$i.bin" bs=1M count="$chunk" conv=fsync status=none || { echo "dd failed"; break; }
done
sync
echo "DONE. free now: $(df -h --output=avail /mnt | tail -1)"
