# 上海体素城市

把上海市做成可飞可走的 1 米体素模型，完成度对标 [Piotr Migdał 的 Warsaw in voxels](https://github.com/stared/warsaw-vibe-voxel)。查看器改编自华沙项目的 three.js + Vite 引擎（MIT）。**没有使用、也没有拷贝华沙的城市数据。**

本仓库只包含源码、配置和文档。自动生成的地图瓦片和查看器运行所需的栅格数据不进 Git，通过 [GitHub Releases](https://github.com/NovenBuilds/urban-model-shanghai/releases) 分包发布。当前最新数据包是阶段性**预览版** `v0.1.0-preview.1`，不是全市完成稿。以后扩大范围会发新的 Release，不会覆盖已有版本。

## 本机运行（从零开始）

需要 Node 18+。包管理器用 [pnpm](https://pnpm.io/)，可用 Node 自带的 corepack 准备，或自行安装。

```sh
git clone https://github.com/NovenBuilds/urban-model-shanghai.git
cd urban-model-shanghai
git checkout v0.1.0-preview.1

# 下载本预览版数据包（约 468 MiB）。也可用浏览器从 Release 页面下载后放到仓库根目录。
gh release download v0.1.0-preview.1 \
  -R NovenBuilds/urban-model-shanghai \
  -p urban-model-shanghai-v0.1.0-preview.1-data.tar.gz

# 可选：核对校验值
# sha256sum urban-model-shanghai-v0.1.0-preview.1-data.tar.gz
# 应为 aeb1b0023e7d9f3204574434d72e0280239ba1a3daf275a62995eb70c24c6e60

# 解压到 web/public/data/（归档根目录带有 web/public/data/ 前缀）
tar -xzf urban-model-shanghai-v0.1.0-preview.1-data.tar.gz

corepack enable
cd web
pnpm install
pnpm exec vite --host 127.0.0.1 --port 5173
```

如果 `corepack enable` 没有权限写系统目录，先执行：

```sh
export COREPACK_HOME=$HOME/.corepack-home PNPM_HOME=$HOME/.pnpm-home
```

或改用 `npm i -g pnpm`。没有 `gh` 时，从 [v0.1.0-preview.1 Release](https://github.com/NovenBuilds/urban-model-shanghai/releases/tag/v0.1.0-preview.1) 下载同名附件，放到仓库根目录后再执行上面的 `tar` 命令。

浏览器打开 [http://127.0.0.1:5173/](http://127.0.0.1:5173/)。地址后可以加机位，例如：

- `?view=skyline` 陆家嘴天际线
- `?view=pearl` 东方明珠
- `?view=bund` 外滩
- `?view=jiukeshu_arts_center` 九棵树未来艺术中心
- `?view=changxing_island_country_park` 长兴岛郊野公园
- `?view=zhenhua_heavy_industries` 振华重工
- `?view=fengxian_government_center` 奉贤区政务中心
- `?view=baoji_lanwan` 保集·澜湾
- `?view=wanggang_primary` 王港小学

还可以改时刻和日期，例如 `?view=skyline&date=6-21&hour=16.8`，或把 `hour` 调到夜里看亮窗。

## 本预览版覆盖范围

对应 git tag：`v0.1.0-preview.1`（完整 commit SHA 写在该 Release 说明和 `MANIFEST.md` 里）。

查看器窗口是一个包围盒，不是矩形填满：网格 x=−34816..30720、y=−19456..28672（约 65.5 × 48.1 公里）。`meta.json` 里的 `W` 和 `D` 是最大边缘坐标，不是宽度。已结算瓦片按列覆盖，缺口处**没有编造瓦片**：

- 1 m typ tx=−272..−241：typ ty=−96..63
- typ tx=−240..−49：typ ty=−152..63
- typ tx=−48..−17：typ ty=−152..63 与 128..223
- typ tx=−16..79：typ ty=−152..223
- typ tx=80..143：typ ty=−56..223
- typ tx=144..239：typ ty=−56..63

已结算瓦数：1 m 122112、1f 122112、8 m 1908。已结算瓦片合计 494,668,912 字节。本数据包还包含查看器需要、且不进 Git 的 `foliage.bin`、`terrain.bin`、`traffic.json`、`zones.json` 以及核心窗口的 `zones/`、`windows/` 分块（样式栅格仍只覆盖核心窗口 x=−2048..10240、y=−7168..8192）。归档解压后约 558 MB；附件压缩后 490,906,172 字节。

本版已折入至 band EYNWS4（含九棵树、长兴岛郊野公园、振华重工、奉贤区政务中心、保集·澜湾、王港小学等已结算部分）。工作区里可能还有更大、含未结算在制瓦的数据，那些瓦没有打进本包。

附件：

- 文件名：`urban-model-shanghai-v0.1.0-preview.1-data.tar.gz`
- SHA256：`aeb1b0023e7d9f3204574434d72e0280239ba1a3daf275a62995eb70c24c6e60`

已经覆盖外滩、陆家嘴、南京路、人民广场、静安、中山公园、豫园与老城厢、虹口、杨浦大桥、复旦、五角场、江湾、长兴岛、奉贤南桥一带等已结算区域。杨浦大桥按开放地图的桥面体量生成，没有编造 208 米主塔和斜拉索。金茂、东方明珠、光华楼等个别地标按公开出处做了示意高度，出处写在 `overrides.json`。地面是 Copernicus GLO-30（30 米公开高程），不是 LiDAR，空地可能出现台阶状伪影。窗户和树冠是类型化示意，不是实测。

## 数据来源与许可

- **代码**（查看器与流水线）：[MIT](LICENSE)。查看器与瓦片格式来自 Piotr Migdał 的 Warsaw in voxels，见 [NOTICE](NOTICE)。
- **建筑与道路**：© OpenStreetMap contributors，[ODbL](https://opendatacommons.org/licenses/odbl/)（[osm.fr 的上海提取](https://download.openstreetmap.fr/extracts/asia/china/shanghai.osm.pbf)，时间戳 2026-11-11T00:17:14Z）。
- **高程**：Copernicus GLO-30，不是 LiDAR，也不是 0.5 m DTM。
- 生成的场景数据在 `web/public/data/`（派生自上述来源），其中瓦片和运行时栅格随 Releases 发布，不进入普通 Git 提交。原始 OSM / DEM 缓存和 `work/` 中间结果也没有放进仓库。若要自己重跑生成流水线，依赖见 `pyproject.toml`，入口是 `scripts/build_data.sh`。
