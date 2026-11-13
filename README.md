# 上海体素城市

把上海市做成可飞可走的 1 米体素模型，完成度对标 [Piotr Migdał 的 Warsaw in voxels](https://github.com/stared/warsaw-vibe-voxel)。查看器改编自华沙项目的 three.js + Vite 引擎（MIT）。**没有使用、也没有拷贝华沙的城市数据。**

## 本机运行

仓库里已经包含当前预览窗口的瓦片，clone 下来装好依赖就能跑，不用重新生成数据。需要 Node 18+ 和 [pnpm](https://pnpm.io/)（`corepack enable` 或 `npm i -g pnpm`）。

```sh
git clone https://github.com/NovenBuilds/urban-model-shanghai.git
cd urban-model-shanghai/web
pnpm install
pnpm exec vite --host 127.0.0.1 --port 5173
```

浏览器打开 [http://127.0.0.1:5173/](http://127.0.0.1:5173/)。如果 corepack 不能写系统目录，先执行：

```sh
export COREPACK_HOME=$HOME/.corepack-home PNPM_HOME=$HOME/.pnpm-home
```

常用预置机位（在网址后加查询参数）：

- `?view=skyline` 陆家嘴天际线
- `?view=pearl` 东方明珠
- `?view=bund` 外滩
- `?view=yangpu_bridge` 杨浦大桥
- `?view=jiangwan_stadium` 江湾体育场

还可以改时刻和日期，例如 `?view=skyline&date=6-21&hour=16.8`，或把 `hour` 调到夜里看亮窗。

## 当前能逛的范围

查看器当前窗口大约 12.3 × 15.4 公里（网格 x=−2048..10240，y=−7168..8192），瓦片按需加载，坐标落在同一套全市网格上，以后向外扩不必给已有瓦片改号。已经覆盖外滩、陆家嘴、南京路、人民广场、静安、中山公园、豫园与老城厢、虹口、杨浦大桥、复旦光华楼、五角场、上海财经大学、江湾体育场、长海医院一带。

杨浦大桥按开放地图的桥面体量生成，没有编造 208 米主塔和斜拉索。金茂、东方明珠、光华楼等个别地标按公开出处做了示意高度，出处写在 `scripts/overrides.json`。地面是 Copernicus GLO-30（30 米公开高程），不是 LiDAR，空地可能出现台阶状伪影。窗户和树冠是类型化示意，不是实测。

西边（长风公园、华东师范大学中北校区、真如寺、宋庆龄陵园）和北边（复旦江湾校区、新江湾城）的瓦片已经生成，但还没有拼进查看器窗口，clone 下来暂时逛不到这两块。更外围仍在生成，之后会再推送。

## 数据来源与许可

- **代码**（查看器与流水线）：[MIT](LICENSE)。查看器与瓦片格式来自 Piotr Migdał 的 Warsaw in voxels，见 [NOTICE](NOTICE)。
- **建筑与道路**：© OpenStreetMap contributors，[ODbL](https://opendatacommons.org/licenses/odbl/)（[osm.fr 的上海提取](https://download.openstreetmap.fr/extracts/asia/china/shanghai.osm.pbf)，时间戳 2026-11-11T00:17:14Z）。
- **高程**：Copernicus GLO-30，不是 LiDAR，也不是 0.5 m DTM。
- 生成的场景数据在 `web/public/data/`，派生自上述来源。

原始 OSM / DEM 缓存（约 1.5 GB）没有放进仓库。若要自己重跑生成流水线，依赖见 `pyproject.toml`，入口是 `scripts/build_data.sh`。
