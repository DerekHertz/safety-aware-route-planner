#!/usr/bin/env bash
# Build the Berkeley-only OSRM dataset into server/osrm/data/ (gitignored). Needs Docker.
#   server/osrm/prepare.sh          # download, clip, extract, partition, customize
# The demo trip bbox (trips.json) is S37.850 W-122.293 N37.875 E-122.255; the clip adds ~0.05 deg
# (about 5 km) on every side so rerouting around the corridor and Emeryville/north Oakland resolve.
set -euo pipefail
cd "$(dirname "$0")"
DATA="$PWD/data"
BBOX="${GAPCROSS_CLIP_BBOX:--122.34,37.80,-122.20,37.93}"   # west,south,east,north
PBF_URL="${GAPCROSS_PBF_URL:-https://download.geofabrik.de/north-america/us/california/norcal-latest.osm.pbf}"
OSRM_IMAGE="${OSRM_IMAGE:-ghcr.io/project-osrm/osrm-backend:v5.27.1}"
OSMIUM_IMAGE="${OSMIUM_IMAGE:-iboates/osmium:latest}"
mkdir -p "$DATA"

if [ ! -s "$DATA/norcal.osm.pbf" ]; then
  curl -fL --retry 3 --progress-bar -o "$DATA/norcal.osm.pbf.part" "$PBF_URL"
  mv "$DATA/norcal.osm.pbf.part" "$DATA/norcal.osm.pbf"
fi

docker run --rm -v "$DATA:/data" "$OSMIUM_IMAGE" \
  extract --bbox="$BBOX" --strategy=complete_ways --overwrite -o /data/berkeley.osm.pbf /data/norcal.osm.pbf

docker run --rm -v "$DATA:/data" "$OSRM_IMAGE" osrm-extract -p /opt/car.lua /data/berkeley.osm.pbf
docker run --rm -v "$DATA:/data" "$OSRM_IMAGE" osrm-partition /data/berkeley.osrm
docker run --rm -v "$DATA:/data" "$OSRM_IMAGE" osrm-customize /data/berkeley.osrm
echo "done: $DATA/berkeley.osrm (serve with: docker compose up osrm)"
