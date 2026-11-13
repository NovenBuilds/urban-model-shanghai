# Parallel region build (started 2026-11-13 09:0x Asia/Shanghai)

Same citywide grid / origin / theta. Never translate, renumber, or overwrite tiles outside your band.
1 m typ tile = 128 m. Local axes: -y ≈ west (Puxi inland), +y ≈ east, +x ≈ NNW, -x ≈ SSE.
Accepted core after round 15: x=-2048..10240 (tx -16..79), y=-7168..8192 (ty -56..63).

| Band | Owner | x range | y range | 1 m typ tx | 1 m typ ty |
|---|---|---|---|---|---|
| W (west)  | worker-W | -2048..10240  | -11264..-7168 | -16..79  | -88..-57 |
| S (south) | worker-S | -6144..-2048  | -7168..8192   | -48..-17 | -56..63  |
| N (north) | worker-N | 10240..14336  | -7168..8192   | 80..111  | -56..63  |
| E (east)  | worker-E (starts after round 15) | -2048..10240 | 8192..12288 | -16..79 | 64..95 |
| corners + global stitch | integrator | — | — | — | — |

Workers write ONLY: tiles/1, tiles/1f, tiles/8 inside their band (8 m tiles: only those fully inside band; boundary 8 m tiles -> list them for integrator),
plus their own notes in work/<band>/ (logs, landmark list, any per-band raw data).
Workers must NOT edit shared scripts in place (copy to scripts/band_<X>_*.py if a change is needed), must NOT touch
zones/, windows/, traffic.json, labels, camera presets, overrides.json, raster chunks, README/NEXT, and must NOT launch Chrome/screenshots.
Shared raw data downloads go to data/raw/<band>/ to avoid collisions.
Memory: box has 15 GB, no swap. Run one heavy python process at a time per worker; before each batch check `free -g` available >= 3 GB, else wait.
Integrator owns: corners, seams/aprons between bands, zones/windows chunks, traffic, labels, presets, screenshots, topdown, NEXT.md.

## Update 09:13 — W done (work/W/REPORT.md). W2 assigned to worker-W:
| W2 (west 2) | worker-W | -2048..10240 | -15360..-11264 | -16..79 | -120..-89 |
Note: band workers freeze the palette to meta.json's existing 196 entries (see scripts/band_W_voxelize.py); all workers should do the same.

## Update 09:16 — N done (work/N/REPORT.md). N2 assigned to worker-N:
| N2 (north 2) | worker-N | 14336..18432 | -7168..8192 | 112..143 | -56..63 |
