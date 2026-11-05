# 本地语义搜索（可移植版）

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) Windows 10/11 · Python 3.10–3.12 · 全程离线

在 Windows 上离线搜索你的 PDF/文本：**按热键 → 输入一句话 → 直接跳到文件的那一页**。
不需要联网，不上传任何内容。

自带 BGE-small 中文向量模型（45 MB），开箱即用。

## 系统要求

- Windows 10 / 11（x64）
- Python **3.10 – 3.12**（安装时勾选 “Add Python to PATH”）
- 建议 8 GB 内存；有集显/NPU 会更快，没有也能用（自动退回 CPU）
- 磁盘：程序 ~50 MB，索引约为被索引文本的 1/3

## 安装

1. 把整个文件夹复制到目标电脑任意位置（路径里有中文也可以，但**不要放在 C:\Program Files**）。
2. 双击 **`setup.cmd`**，它会：
   - 创建 `.venv` 虚拟环境
   - 安装依赖（约 250 MB，含 OCR）
   - 生成 `settings.json`（默认索引桌面 + 下载）
   - 建立第一次索引
3. 双击 **`start-daemon.cmd`** 启动后台服务。

## 使用

按 **`Ctrl + Alt + Space`** 唤出搜索框：

| 操作 | 键 |
|---|---|
| 唤出 / 收起 | `Ctrl + Alt + Space` |
| 选择 | `↑` `↓` |
| 打开文件 | `Enter` |
| 隐藏 | `Esc` |

结果卡片里会显示命中段落和关键词高亮；页码同时给出书本页码和 PDF 物理页。

## 开机自启

双击 **`install-autostart.cmd`**（取消用 `uninstall-autostart.cmd`）。
之后登录 Windows 就在后台常驻，不占任务栏。

## 日常维护

- **新增/修改文件**：插电时后台每 10 分钟自动扫描，无需操作。
- **手动更新**：双击 `rebuild-index.cmd`。
- **停止服务**：`stop-daemon.cmd`（只停本程序，不影响其他 Python 进程）。

## 设置 `settings.json`

```json
{
 "hotkey": "ctrl+alt+space",
 "model": "bge-small",
 "device": "auto",
 "roots": ["C:\\Users\\你\\Desktop", "C:\\Users\\你\\Downloads"],
 "scan_interval_min": 10,
 "idle_unload_min": 10,
 "mode": "semantic",
 "scan_on_battery": false,
 "ocr": true,
 "ocr_pages_per_run": 240,
 "ocr_workers": 3,
 "max_results": 10,
 "window_width": 1040,
 "window_height": 680
}
```

| 字段 | 说明 |
|---|---|
| `hotkey` | 支持 `ctrl/alt/shift/win` + `space` / `f1..f12` / 字母 `a-z` / 数字 `0-9` |
| `device` | `auto` / `GPU` / `CPU` / `NPU` |
| `roots` | 要索引的目录，可多个 |
| `mode` | `semantic`（向量+关键词，约 500 MB）或 `lite`（仅关键词，约 100 MB） |
| `scan_on_battery` | 默认 `false`，电池供电时不做后台扫描 |
| `ocr` | 默认 `true`，后台自动识别扫描版 PDF |
| `ocr_pages_per_run` | 每轮最多识别多少页，默认 `120` |
| `ocr_workers` | OCR 并行进程数，默认 `1`（调大很吃内存） |
| `ocr_max_worker_mb` | 单个 OCR 进程内存上限，默认 `1100` |

改完需要重启服务（`stop-daemon.cmd` → `start-daemon.cmd`）。

### 16GB 内存的并行 OCR 预设

[`settings.performance.json`](settings.performance.json) 是 16GB 及以上内存的可选预设：
6 个 OCR worker、每轮 400 页、4 分钟扫描间隔。一批 OCR 结束并重建索引后，
下一批几乎立即开始，适合一次性清掉大量扫描件。

用法：把文件里的 `<你的用户名>` 换成自己的用户名，覆盖 `settings.json`，然后重启服务。

> 6 个 worker 的峰值约 4GB；8GB 内存的机器不要用这个预设，保留默认 1 个 worker。
> `ocr_max_worker_mb` 按单个 worker 统计；worker 仍然每 3 页回收一次，防止
> RapidOCR/OpenVINO 的 native 内存持续增长。
>
> 队列清空后仍会每 4 分钟做一次轻量文件扫描；想安静可把 `scan_interval_min` 改回 10。

## 扫描版 PDF 的 OCR（默认开启）

扫描件没有文字层，直接抽文本是空的。OCR 随 `setup.cmd` 一起装好，**后台自动跑，
不需要任何手动步骤**：

1. 每轮扫描先用 `ingest.py` 把「没有文字层、OCR 还没做完」的 PDF 连同缺多少页
   写进 `index/ocr/pending.json`
2. 插电状态下接着跑 `ocr_auto.py`，按预算识别一部分页面（默认每轮 240 页、
   最长 4 分钟），按页存进 `index/ocr/pages/`
3. 只有真的新增了页面才重建索引并热重载，搜索立刻能用

因为结果是一页页攒的，一本 500 页的书会分几轮慢慢补完；已识别的页不会重做，
中途关机、断电、手动中断都可以，下次接着来。速度约 1.5–2 页/秒。

| 设置 | 说明 |
|---|---|
| `ocr` | `true` 自动识别（默认）；`false` 完全关闭 |
| `ocr_pages_per_run` | 每轮最多识别多少页，调小更省 CPU |
| `ocr_workers` | 并行进程数，默认 **1**（每个进程约 0.7–1 GB，2 个以上很容易吃满内存） |
| `ocr_max_worker_mb` | 单个 OCR 进程的内存上限，超过就立刻结束它，默认 1100 |

OCR 引擎（RapidOCR + OpenVINO）**每识别一页大约多占 100 MB 且不会主动释放**，
所以 `ocr_auto.py` 每识别 3 页就换一个新进程，并有内存看门狗兜底：
实测峰值约 1.1 GB，识别完立刻回落到 100 MB 左右。

想一次跑完用 `ocr-scan.cmd`（同样可中断续跑）；依赖坏了用 `ocr-setup.cmd` 重装。

## 可选：换成更强的 BGE-M3 模型

M3 对中英混排更好，但模型 1.1 GB、建索引慢 12 倍，且实测在本类语料上
检索命中率与 small 差不多。需要时：

```powershell
.\.venv\Scripts\python.exe scripts\fetch_hf_files.py Xenova/bge-m3 models\bge-m3-src config.json tokenizer.json onnx\model_fp16.onnx
.\.venv\Scripts\python.exe scripts\convert_bge.py      # 需先改脚本里的路径，或直接用 bge-small
```

（`convert_bge.py` 默认转换 `models/bge-small-zh-src`；换模型时把 `JOBS` 里的
输入输出改成 bge-m3 的路径即可。）

## 目录结构

```
setup.cmd / start-daemon.cmd / stop-daemon.cmd / rebuild-index.cmd
settings.json              本机配置
requirements.txt           基础依赖
requirements-ocr.txt       OCR 依赖（可选）
scripts/
  daemon.py                常驻进程：热键、弹窗、自动扫描
  search_engine.py         检索引擎（mmap 索引）
  bm25.py                  关键词索引
  ingest.py                抽文本、切块、增量判断、删除同步、列出待 OCR 页
  embed_docs.py            向量化
  build_all.py             合并成可 mmap 的单文件索引
  ocr_auto.py              按预算自动补 OCR（daemon 每轮扫描后调用）
  ocr_run.py / ocr_fix.py  扫描件 OCR 引擎与修复工具
  trigger_show.py          唤出窗口（热键冲突时用 show-search.cmd）
  stop_daemon.py           停止后台服务
models/bge-small-zh-fp16/  OpenVINO 模型（45 MB）
models/bge-small-zh-src/   分词器
index/                     索引（自动生成，可删除重建）
  all/                     合并索引：向量 + BM25 + 文本
  docs/ vectors-small/     每文件的分片（增量更新用）
  ocr/pages/               每页的 OCR 文本（增量、可续跑）
  ocr/pending.json         待 OCR 清单（ingest 生成，ocr_auto 消费）
  daemon.log               运行日志
```

## 许可

本项目以 **MIT 许可**开源，见 [LICENSE](LICENSE)。内置模型与第三方依赖的许可见下一节。

## 第三方组件

| 组件 | 用途 | 许可 |
|---|---|---|
| PyMuPDF | 抽取 PDF 文本、渲染扫描页 | AGPL-3.0（或商业许可） |
| OpenVINO | 向量模型 / OCR 推理 | Apache-2.0 |
| RapidOCR | 扫描件 OCR | Apache-2.0 |
| tokenizers、numpy | 分词、数组运算 | Apache-2.0 / BSD-3-Clause |

内置的 `models/bge-small-zh-fp16` 由 [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5)
（MIT 许可）转换为 OpenVINO IR。

## 自检（可选）

```
.venv\Scripts\python.exe tests\run_all.py
```

五组测试：纯函数、沙箱全链路（抽取→向量→索引→搜索→删除→OCR）、守护进程、界面交互、
后台循环。除了「界面」和「守护进程」两组会用真实索引做只读查询外，其余都在临时目录里跑，
不会动你的 `index/`。

## 常见问题

**按热键没反应**
看 `index/daemon.log` 里有没有 `RegisterHotKey failed`——某软件占用了这个组合，
改 `settings.json` 的 `hotkey` 换一个，然后重启服务。

**搜索是空的**
确认 `index\all\meta.json` 存在；没有就运行 `rebuild-index.cmd`。
日志里若有 `no documents with vectors` 说明向量还没建。

**别人电脑上提示找不到 python**
安装 Python 3.10–3.12 并勾选 Add to PATH，重新运行 `setup.cmd`。

**能搜到文件名搜不到内容**
多半是扫描版 PDF（没有文字层），按上面的 OCR 步骤处理。

**OCR 没动静**
看 `index/daemon.log` 里有没有 `scan: ocr` 开头的行。没有任何行说明还在等下一轮扫描
（每 10 分钟一次，电池供电时不跑）；出现 `rapidocr unavailable` 就跑一次
`ocr-setup.cmd` 修复。

**索引占用多大**
本项目 169 个文件、3.6 万块：索引约 105 MB，常驻内存约 500 MB。
想要更小就把 `mode` 改成 `lite`（约 100 MB）。

## 已知限制

- 扫描件的公式、表格 OCR 出来是噪声。
- 全大写的英文标题有时会被 OCR 连成一个词（`SCANNEDPAGEALPHA`），按单词搜不到；中文不受影响。
- 只索引 `roots` 下的文件；移动文件会被当成「删除 + 新增」。
- 删除的文件在下次扫描时才会从索引里剔除。
