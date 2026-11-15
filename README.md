# 上海体素城市

把上海市做成可飞可走的 1 米体素模型，完成度对标 [Piotr Migdał 的 Warsaw in voxels](https://github.com/stared/warsaw-vibe-voxel)。查看器改编自华沙项目的 three.js + Vite 引擎（MIT）。**没有使用、也没有拷贝华沙的城市数据。**

**这个项目目前已暂停**（2026-11-15）。已经做完、能看的部分留在 Git 和 Releases 里，以后有条件再继续。旧版本不会被覆盖。

## 项目起因，以及为什么先停在这里

看到 [Warsaw in voxels](https://github.com/stared/warsaw-vibe-voxel) 的 1 米体素、可飞可走的城市模型，觉得喜欢，就想搞一套上海的，目标是整座上海市。推着推着才发现：这座城太大了，token 烧不起。qiong。

所以先停在这里。不是不想做，是现在烧不起。已有的源码、查看器，以及 GitHub Releases 里的数据包都还在，以后再说。

本仓库只包含源码、配置和文档。自动生成的地图瓦片和查看器运行所需的栅格数据不进 Git，通过 [GitHub Releases](https://github.com/NovenBuilds/urban-model-shanghai/releases) 分包发布。当前最新数据包是阶段性**预览版** `v0.1.0-preview.2`，不是全市完成稿。以后扩大范围会发新的 Release，不会覆盖已有版本。

## 本机运行（从零开始）

需要 Node 18+。包管理器用 [pnpm](https://pnpm.io/)，可用 Node 自带的 corepack 准备，或自行安装。

```sh
git clone https://github.com/NovenBuilds/urban-model-shanghai.git
cd urban-model-shanghai
git checkout v0.1.0-preview.2

# 下载本预览版数据包（约 516.2 MiB）。也可用浏览器从 Release 页面下载后放到仓库根目录。
gh release download v0.1.0-preview.2 \
  -R NovenBuilds/urban-model-shanghai \
  -p urban-model-shanghai-v0.1.0-preview.2-data.tar.gz

# 可选：核对校验值
# sha256sum urban-model-shanghai-v0.1.0-preview.2-data.tar.gz
# 应为 c078f3d919b0f268cf001482ee20d637722960ff167a1ce1c393aec6658b7273

# 解压到 web/public/data/（归档根目录带有 web/public/data/ 前缀）
tar -xzf urban-model-shanghai-v0.1.0-preview.2-data.tar.gz

corepack enable
cd web
pnpm install
pnpm exec vite --host 127.0.0.1 --port 5173
```

如果 `corepack enable` 没有权限写系统目录，先执行：

```sh
export COREPACK_HOME=$HOME/.corepack-home PNPM_HOME=$HOME/.pnpm-home
```

或改用 `npm i -g pnpm`。没有 `gh` 时，从 [v0.1.0-preview.2 Release](https://github.com/NovenBuilds/urban-model-shanghai/releases/tag/v0.1.0-preview.2) 下载同名附件，放到仓库根目录后再执行上面的 `tar` 命令。

浏览器打开 [http://127.0.0.1:5173/](http://127.0.0.1:5173/)。地址后可以加机位，例如：

- `?view=skyline` 陆家嘴天际线
- `?view=pearl` 东方明珠
- `?view=bund` 外滩
- `?view=institute_704` 704所热工环境制造中心
- `?view=xinchang_forest` 南汇新场片林
- `?view=panshi_park` 潘石公园
- `?view=shanghai_daxue_baoshan_xiaoqu` 上海大学（宝山校区）
- `?view=jinqiu_huayuan` 锦秋花园
- `?view=jindi_gelin_chunxiao` 金地格林春晓
- `?view=dangdai_gaodi` 当代高邸

还可以改时刻和日期，例如 `?view=skyline&date=6-21&hour=16.8`，或把 `hour` 调到夜里看亮窗。

## 本预览版覆盖范围

对应 git tag：`v0.1.0-preview.2`（完整 commit SHA 写在该 Release 说明和 `MANIFEST.md` 里）。

查看器窗口是一个包围盒，不是矩形填满：网格 x=−35840..30720、y=−19456..29056（66,560 × 48,512 米，约 66.6 × 48.5 公里）。`meta.json` 里的 `W` 和 `D` 是最大边缘坐标，不是宽度。已结算瓦片按列覆盖，列内不连续的不写成连续，缺口处**没有编造瓦片**。1 m typ 覆盖（1f 相同）：

- typ tx=−280..−273：typ ty=−88..−57
- typ tx=−272..−246：typ ty=−96..63
- typ tx=−245：typ ty=−96..65（typ ty=66 仍空，不得把 typ tx=−272..−241 整块写成 typ ty=−96..65）
- typ tx=−244..−241：typ ty=−96..63
- typ tx=−240..−209：typ ty=−152..63
- typ tx=−208..−201：typ ty=−152..76
- typ tx=−200..−137：typ ty=−152..63
- typ tx=−136..−89：typ ty=−152..143
- typ tx=−88..−81：typ ty=−152..151
- typ tx=−80..−49：typ ty=−152..167
- typ tx=−48..56：typ ty=−152..223
- typ tx=57..62：typ ty=−152..226
- typ tx=63..79：typ ty=−152..223
- typ tx=80：typ ty=−150 与 −124..−95 与 −85..−79 与 −68..−66 与 −56..223（−150、−68..−66 是孤岛；缺口 typ ty=−149..−125、−94..−86、−78..−69、−65..−57 仍在）
- typ tx=81：typ ty=−125..−95 与 −85..−79 与 −57..223
- typ tx=82：typ ty=−125..−120 与 −117..−115 与 −112..−101 与 −85..−79 与 −60..223（typ ty=−108 已填，故 typ ty=−112..−101 连续）
- typ tx=83：typ ty=−122..−121 与 −117..−115 与 −112..−105 与 −85..−79 与 −60..223
- typ tx=84：typ ty=−109..−105 与 −85..−79 与 −62..223
- typ tx=85：typ ty=−85..−79 与 −62..223
- typ tx=86：typ ty=−85..−79 与 −59..−58 与 −56..223（typ ty=−57 未写，不得写成 typ ty=−59..223）
- typ tx=87：typ ty=−81..−78 与 −64..223
- typ tx=88：typ ty=−81..−78 与 −63..223
- typ tx=89：typ ty=−80..−77 与 −63..223
- typ tx=90：typ ty=−80..−77 与 −60..223（typ ty=−61 未写）
- typ tx=91：typ ty=−79..−77 与 −60..223
- typ tx=92：typ ty=−79..−76 与 −59..223
- typ tx=93..96：typ ty=−78..−77 与 −61..223（接到 typ ty=−58，但 typ ty=−76..−62 仍空，不得写成 typ ty=−78..223）
- typ tx=97：typ ty=−78..−77 与 −59..223
- typ tx=98..100：typ ty=−59..223
- typ tx=101..102：typ ty=−58..223（typ ty=−59 空）
- typ tx=103..143：typ ty=−56..223
- typ tx=144：typ ty=−56..63 与 142..145（缺口 typ ty=64..141）
- typ tx=145..239：typ ty=−56..63

8 m typ 覆盖（按文件存在；相邻 typ ty 才写成连续）：

- typ tx=−35：typ ty=−11..−8
- typ tx=−34..−32：typ ty=−12..7
- typ tx=−31：typ ty=−12..8（typ ty=8 只含 typ tx=−245 typ ty=64/65 切片，不是整张 8×8）
- typ tx=−30..−27：typ ty=−19..7
- typ tx=−26：typ ty=−19..9（typ ty=9 只是部分切片）
- typ tx=−25..−18：typ ty=−19..7
- typ tx=−17..−12：typ ty=−19..17
- typ tx=−11：typ ty=−19..18
- typ tx=−10..−7：typ ty=−19..20
- typ tx=−6..6：typ ty=−19..27
- typ tx=7：typ ty=−19..28（typ ty=28 只含 typ tx=57..62 typ ty=224..226 切片）
- typ tx=8..9：typ ty=−19..27
- typ tx=10：typ ty=−19 与 −16..27（缺口 typ ty=−18..−17）
- typ tx=11：typ ty=−11..−10 与 −8..27（缺口 typ ty=−9；typ ty=−8 是部分 1 m 切片修补，不得写成 typ ty=−11..27 连续）
- typ tx=12：typ ty=−10 与 −8..27（缺口 typ ty=−11 与 typ ty=−9）
- typ tx=13..17：typ ty=−7..27
- typ tx=18：typ ty=−7..7 与 17..18（缺口 typ ty=8..16）
- typ tx=19..29：typ ty=−7..7

已结算瓦数：1 m 132694、1f 132694、8 m 2088。已结算瓦片合计 548,026,116 字节。本数据包还包含查看器需要、且不进 Git 的 `foliage.bin`、`terrain.bin`、`traffic.json`、`zones.json` 以及核心窗口的 `zones/`、`windows/` 分块（样式栅格仍只覆盖核心窗口 x=−2048..10240、y=−7168..8192）。归档解压后 `web/public/data/` 合计 612,214,279 字节；附件压缩后 541,233,020 字节。

相对上一版 `v0.1.0-preview.1`，本窗口向西扩到 typ tx=−280，并向 typ tx=−245 typ ty=65 延伸；北缘 `D` 扩到 29056，纳入 704所热工环境制造中心；另外折入南汇新场片林、南翔一带、上大/锦秋一带、当代高邸，以及 typ tx=144 typ ty=142..145 的潘石公园。仍空的缺口包括 typ tx=86 typ ty=−57，以及 typ tx=144 typ ty=64..141。

附件：

- 文件名：`urban-model-shanghai-v0.1.0-preview.2-data.tar.gz`
- SHA256：`c078f3d919b0f268cf001482ee20d637722960ff167a1ce1c393aec6658b7273`

已经覆盖外滩、陆家嘴、南京路、人民广场、静安、中山公园、豫园与老城厢、虹口、杨浦大桥、复旦、五角场、江湾、长兴岛、奉贤南桥一带，以及本版新纳入的 704所、南汇新场片林、南翔、上大/锦秋、当代高邸、潘石公园等已结算区域。杨浦大桥按开放地图的桥面体量生成，没有编造 208 米主塔和斜拉索。金茂、东方明珠、光华楼等个别地标按公开出处做了示意高度，出处写在 `overrides.json`。地面是 Copernicus GLO-30（30 米公开高程），不是 LiDAR，空地可能出现台阶状伪影。窗户和树冠是类型化示意，不是实测。

## 数据来源与许可

- **代码**（查看器与流水线）：[MIT](LICENSE)。查看器与瓦片格式来自 Piotr Migdał 的 Warsaw in voxels，见 [NOTICE](NOTICE)。
- **建筑与道路**：© OpenStreetMap contributors，[ODbL](https://opendatacommons.org/licenses/odbl/)（[osm.fr 的上海提取](https://download.openstreetmap.fr/extracts/asia/china/shanghai.osm.pbf)，时间戳 2026-11-11T00:17:14Z）。
- **高程**：Copernicus GLO-30，不是 LiDAR，也不是 0.5 m DTM。
- 生成的场景数据在 `web/public/data/`（派生自上述来源），其中瓦片和运行时栅格随 Releases 发布，不进入普通 Git 提交。原始 OSM / DEM 缓存和 `work/` 中间结果也没有放进仓库。若要自己重跑生成流水线，依赖见 `pyproject.toml`，入口是 `scripts/build_data.sh`。
