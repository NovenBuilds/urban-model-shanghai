#!/bin/sh
# Build the first Bund–Lujiazui AOI tiles from cached OSM + public DEM.
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
RAW="$ROOT/data/raw"
mkdir -p "$RAW" data/debug web/public/data/tiles/1 web/public/data/tiles/1f web/public/data/tiles/8

OSM_PBF="$RAW/shanghai-latest.osm.pbf"
OSM_URL="https://download.openstreetmap.fr/extracts/asia/china/shanghai.osm.pbf"
DEM="$RAW/Copernicus_DSM_COG_10_N31_00_E121_00_DEM.tif"
DEM_URL="https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N31_00_E121_00_DEM/Copernicus_DSM_COG_10_N31_00_E121_00_DEM.tif"

if [ ! -s "$OSM_PBF" ]; then
  echo "downloading Shanghai OSM extract…"
  curl -L --retry 3 -o "$OSM_PBF" "$OSM_URL"
fi
if [ ! -s "$DEM" ]; then
  echo "downloading Copernicus GLO-30 DEM…"
  curl -L --retry 3 -o "$DEM" "$DEM_URL"
fi

# Generous first cut (WGS84 S,W,N,E) so we can measure the Bund and place the window.
BBOX="${OSM_BBOX:-31.215,121.455,31.260,121.535}"
if [ ! -s "$RAW/osm.json" ] || [ "$OSM_PBF" -nt "$RAW/osm.json" ]; then
  echo "cutting OSM PBF to $BBOX…"
  "$PY" scripts/osm_pbf.py "$OSM_PBF" "$BBOX" "$RAW/osm.json"
fi

echo "measuring AOI…"
"$PY" scripts/measure_aoi.py

echo "resampling DEM…"
"$PY" scripts/prep_dem.py

echo "climate…"
"$PY" scripts/climate.py || true

echo "notable (Wikidata + pageviews)…"
if [ ! -s "$RAW/notable.json" ]; then
  "$PY" scripts/notable.py || true
fi

echo "voxelizing…"
"$PY" scripts/voxelize.py

echo "labels…"
"$PY" scripts/labels.py || true

echo "traffic…"
"$PY" scripts/traffic.py || true

echo "done. tiles in web/public/data/tiles"
