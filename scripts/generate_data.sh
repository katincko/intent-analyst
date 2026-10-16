#!/usr/bin/env bash
# Reproduces data/synthea/*.csv. Needs Docker (Java runs in a container).
set -euo pipefail

SYNTHEA_VERSION="v4.0.0"
JAR_URL="https://github.com/synthetichealth/synthea/releases/download/${SYNTHEA_VERSION}/synthea-with-dependencies.jar"
JAR_SHA256="ed43c20ad40ba5c3bc724503a5af032715fe3c491620b766148e7c2361e6ecc1"
IMAGE="eclipse-temurin:17.0.13_11-jre"
# One generator thread (below) is what makes the output reproducible byte for byte.
CLINICIAN_SEED=42
SEED=42
REFERENCE_DATE=20260101   # yyyymmdd, fixes "now" for ages and encounter windows
POPULATION=50
STATE="Massachusetts"
TABLES="patients encounters conditions medications procedures"

root="$(cd "$(dirname "$0")/.." && pwd)"
work="$root/.work"
rm -rf "$work"; mkdir -p "$work"
trap 'rm -rf "$work"' EXIT

curl -fsSL "$JAR_URL" -o "$work/synthea.jar"
echo "$JAR_SHA256  $work/synthea.jar" | sha256sum -c -

MSYS_NO_PATHCONV=1 docker run --rm -v "$(cygpath -m "$work" 2>/dev/null || echo "$work"):/work" -w /work "$IMAGE" \
  java -jar synthea.jar -s "$SEED" -cs "$CLINICIAN_SEED" -r "$REFERENCE_DATE" -p "$POPULATION" \
  --generate.thread_pool_size=1 \
  --exporter.csv.export=true --exporter.fhir.export=false --exporter.baseDirectory=/work/out \
  "$STATE"

mkdir -p "$root/data/synthea"
for t in $TABLES; do cp "$work/out/csv/$t.csv" "$root/data/synthea/$t.csv"; done
