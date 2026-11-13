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

## Update 09:25 — W2 done (work/W2/REPORT.md). W3 assigned to worker-W:
| W3 (west 3) | worker-W | -2048..10240 | -19456..-15360 | -16..79 | -152..-121 |
Target: 虹桥 T2, 上海虹桥站. Airport west/south edge still crosses x=-2048 (needs a later -x/SW band).

## Update 09:25 — N2 done (work/N2/REPORT.md). N3 assigned to worker-N:
| N3 (north 3) | worker-N | 18432..22528 | -7168..8192 | 144..175 | -56..63 |
Target: 吴淞炮台湾湿地 (x to ~19111), possible 宝钢主厂区, check for 吴淞口国际邮轮港 named polygons. Must reuse band_N2_sea.py coastline-to-sea fill wherever OSM has shoreline but no water polygon (Yangtze estuary).

## Update 09:26 — S done (work/S/REPORT.md). S2 assigned to worker-S:
| S2 (south 2) | worker-S | -10240..-6144 | -7168..8192 | -80..-49 | -56..63 |
Target: 西岸穹顶艺术中心, 西岸大剧院, 西岸美高酒店 (OSM height=279, was edge-only), 西岸梦中心, 新龙华. Keep Huangpu/water continuous with S. x=-6144 seam both ways like previous bands.

## Update 09:32 — W3 done (work/W3/REPORT.md). SW assigned to worker-W:
| SW (airport west) | worker-W | -6144..-2048 | -19456..-11264 | -48..-17 | -152..-89 |
Covers Hongqiao airport west spill past x=-2048 along both W2 and W3 latitudes (aerodrome xmin≈-2569). Huaxiang Green Park was almost entirely x<-2048. W4 further -y is not needed for the airport (south edge stays above y=-19456). Worker-W may close the x=-2049 apron on its own W2 and W3 tiles. Runways/aerodrome surfaces: document honestly if the pipeline still does not draw them; do not invent pavement. Same frozen 196-entry palette, no meta/ground_all/foliage.bin/overrides/Chrome.

## Update 09:34 — N3 done (work/N3/REPORT.md). N4 assigned to worker-N:
| N4 (north 4) | worker-N | 22528..26624 | -7168..8192 | 176..207 | -56..63 |
Target: finish 宝钢主厂区 w346858330 (xmax≈26621, almost exactly at 26624), plus 热轧 w979801421 and 冷轧 w1253955317 that crossed +x; east of 美兰湖高尔夫 w278510137 (xmax 23120; south edge y<-7168 is a corner, not N4). MUST reuse band_N2_sea.py sea-fill (coast still extends past x=22528; OSM water polygons alone would make the Yangtze into land). Close N3|N4 seam both ways (N3 is yours). Frozen 196 palette, no meta/ground_all/foliage.bin/overrides/Chrome.

## Update 09:40 — N4 done (work/N4/REPORT.md). N5 assigned to worker-N:
| N5 (north 5) | worker-N | 26624..30720 | -7168..8192 | 208..239 | -56..63 |
Finish 宝钢主厂区 w346858330: unclipped xmax=27100 (N3's 26621 was from a clipped extract), ~476 m / 0.26 km² past 26624. Also 北岸郡庭 w1061371906 (xmax 26636). MUST reuse band_N2_sea.py (sea already crosses 26624 in the N4 pad window; 30.30 km² in N4, continuous). Close N4|N5 seam both ways (N4 is yours). Frozen 196 palette, no meta/ground_all/foliage.bin/overrides/Chrome.

## Update 09:41 — SW done (work/SW/REPORT.md). WW assigned to worker-W (the gap between SW and S, west of band W):
| WW (west of W) | worker-W | -6144..-2048 | -11264..-7168 | -48..-17 | -88..-57 |
Do not join SW's y=-11264 edge directly to S (S starts at y=-7168). This 4096×4096 strip closes that hole. Expected: finish 近铁虹桥中心 w1373096357 (60% in SW, north edge into this gap). Seams: +y toward S at y=-7168 (S is owned by worker-S — write only WW's +y apron from ground_S if present; do not rewrite S tiles); -y toward SW at y=-11264 (you own SW, close both ways); +x toward W at x=-2048 (you own W, close both ways). Frozen 196 palette, no meta/ground_all/foliage.bin/overrides/Chrome. Do not write into S or S2.

## Update 09:47 — WW done (work/WW/REPORT.md). WX assigned to worker-W (west of SW+WW):
| WX | worker-W | -10240..-6144 | -19456..-7168 | -80..-49 | -152..-57 |
One 4096×8192 strip west of SW and WW. Expected: remainder of 闵行体育公园 w100676642 (30% was past x=-6144 in SW) and 上海师范大学第一附属小学 w1194908647 (13% past x=-6144 in WW). +x seams: you own SW and WW, close both ways. +y at y=-7168 adjoins S2 (worker-S, in progress, x=-10240..-6144, y=-7168..8192): write only WX's +y apron if ground_S2 exists; do NOT rewrite S2 tiles. Frozen 196 palette, no meta/ground_all/foliage.bin/overrides/Chrome. Do not write into S, S2, or past y=-7168.

## Update 09:48 — N5 done (work/N5/REPORT.md). Baosteel main plant w346858330 is complete across N3+N4+N5 (unclipped xmax=27100 < 30720). No N6 for the plant.
EN2 assigned to worker-N ( +y over the N2 column, to finish 滨江森林公园):
| EN2 | worker-N | 14336..18432 | 8192..12288 | 112..143 | 64..95 |
Target: 滨江森林公园 w74590795 and 二期 w867494948 (x in N2, 二期 y to 8999). MUST reuse band_N2_sea.py (N2/N3 sea pads already crossed y=8192). Close N2|EN2 seam both ways (you own N2). Frozen 196 palette, no meta/ground_all/foliage.bin/overrides/Chrome. Do not write into other +y columns yet.

## Update 09:49 — S2 done (work/S2/REPORT.md). West Bund dome/theatre/Meigao/Dream Center land sit on the S|S2 seam (x=-6144) but S+S2 together cover them. Dream Center buildings 1/3/8/9 etc. are fully in S2. S|S2 aprons already closed both ways.
S3 assigned to worker-S (further -x):
| S3 | worker-S | -14336..-10240 | -7168..8192 | -112..-81 | -56..63 |
Expected from S2 edge list: 华泾公园 w297060238, 上海市位育中学 w229320442, 华泾医院 w544553361. 上海南站主站房 crosses y=-7168 into WX (already running), not S3. Close S2|S3 seam both ways (you own S2). Frozen 196 palette, no meta/ground_all/foliage.bin/overrides/Chrome. Do not invent Xupu towers (bridge already fully in S2).

## Update 09:53 — EN2 done (work/EN2/REPORT.md). 滨江森林公园一期 w74590795 and 二期 w867494948 are complete across N2+EN2 (ymax 8273 / 8999, both < 12288; x stays in the N2 column).
EN assigned to worker-N ( +y over the N column, west of EN2):
| EN | worker-N | 10240..14336 | 8192..12288 | 80..111 | 64..95 |
Target: west of 外高桥港区 w74595894 (xmin=12168; ymax=12761 still crosses y=12288, ~473 m left for a later +y strip), 外高桥保税物流园区 w542559751 (west of x=14336), 凌桥中学 w841131015. Also measure 共青森林公园 w39176862 and 复兴岛 if they fall here. MUST reuse band_N2_sea.py. Close N|EN at y=8192 and EN|EN2 at x=14336 both ways (you own N and EN2). Frozen 196 palette, no meta/ground_all/foliage.bin/overrides/Chrome.
