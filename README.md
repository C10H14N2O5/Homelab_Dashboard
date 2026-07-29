# Zoraxy HomeLab Service Dashboard

这是一个由 Zoraxy 静态网页服务器托管的 HomeLab 服务导航页。前端是无外部依赖的单页；仅监听回环地址的 Python Dashboard 状态服务负责跨设备同步和管理壁纸，并经本机 Zoraxy 批量检测服务入口。开发时直接运行 Python，生产环境通过 PyInstaller 分发单文件 `HomeLabWallpaperState.exe`，不需要安装 Python 或虚拟环境。

## 项目概览

| 项目 | 当前实现 |
| --- | --- |
| 页面入口 | `index.html` |
| 背景图片 | `backgrounds/bg.jpg` 随仓库提供兜底，其余原图由部署机器本地保存；缩略图由后端生成 |
| 服务入口 | 11 个 HomeLab 子域名 |
| 联网搜索 | 纯前端 Bing、Google、DuckDuckGo、百度搜索；默认 Bing，无 JavaScript 时仍可提交 |
| 状态服务 | Python 服务；提供壁纸偏好、图片管理、Bestdori 卡面抽取与批量入口探测，生产为 Windows x64 单文件 EXE |
| API 地址 | `127.0.0.1:8787`，只能监听本机回环地址 |
| 运行角色 | 同一 EXE 提供隐藏 API 后端和可见终端监视器 |
| 自动启动 | 当前用户 `shell:startup` 快捷方式，登录后显示监视器 |
| 配置与状态 | 严格 v2 JSON 配置；背景状态和独立抽卡去重记录位于 `HomeLabWallpaperState-data/` |
| 前端依赖 | 无 CDN、无框架、内联 SVG |

## 文件结构

```text
Zoraxy_StaticWebSite/
├── backgrounds/
│   ├── .gitignore               # 仅放行兜底 bg.jpg
│   ├── bg.jpg                   # 拉取后即可使用的默认壁纸
│   └── thumbnails/              # 运行时生成的 WebP 缩略图缓存，不进入 Git
├── state-service/
│   ├── main.py                  # API、入口探测、状态存储、启动器、监视器和首次引导
│   └── test_main.py             # Python 单元测试
├── scripts/
│   ├── build-state-service.ps1  # 测试、PyInstaller 构建和 EXE 冒烟测试
│   ├── uninstall-state-service.ps1 # 卸载EXE自启动注册
│   └── verify-project.ps1       # 全仓库验证入口
├── audits/
│   ├── README.md                # 审计报告管理规则、编号和模板
│   └── 2026-07-23-business-implementation-audit.md # 已关闭的历史审计
├── requirements.txt             # 固定 PyInstaller、Pillow 和 Send2Trash 依赖
├── index.html                   # 实际网页，包含 HTML/CSS/JavaScript/SVG
├── CHANGELOG.md
├── TODO.md
└── README.md
```

仓库只分发兜底背景 `backgrounds/bg.jpg`，不分发其它背景、缩略图缓存、构建出的 EXE、JSON 配置、状态或日志。公开 Web 根目录只应部署 `index.html` 和 `backgrounds/`；不要复制 README、脚本、源码、EXE、配置、状态或 Git 文件。

## 运行架构

浏览器只访问 Homepage 同源 API；目标服务不再需要向 Homepage 开放 CORS：

```text
浏览器
├── GET /api/background-files             -> Python 状态服务生成/复用缩略图清单
├── GET /backgrounds/thumbnails/...        -> Zoraxy 静态缩略图
├── GET /backgrounds/<文件名>              -> 应用或查看当前壁纸时传输原图
├── GET /backgrounds/                      -> Zoraxy 静态目录列表（旧后端降级）
├── GET/PATCH /api/background-preferences  -> Zoraxy Virtual Directory
├── POST /api/background-files/...         -> 127.0.0.1:8787/api/...
├── GET/POST /api/bestdori/...              -> Python 状态服务
│                                               -> Bestdori 固定 HTTPS API/资源
└── POST /api/service-status               -> Python 状态服务
                                                -> 127.0.0.1:443 Zoraxy
                                                -> 11 个服务上游
```

API 与 Zoraxy 没有进程级启动依赖。API 初始化只读取背景目录、配置和状态文件；Zoraxy 只在浏览器请求到达时反向代理。因此两个当前用户 Startup 入口可并行启动，不需要设置顺序。

## 前端行为

### 多搜索引擎搜索

Hero 标题下方提供一枚默认收起的小胶囊，当前引擎的图标和名称在其中居中排列；点击后外层以约 300ms 向右展开为输入框，同一枚“图标 + 名称”胶囊同步缩小为引擎列表按钮，不使用元素替换或额外三角符号。列表只显示四个引擎的图标和名称，并可通过按钮、方向键、Home/End、Enter/Space 和 Escape 选择或关闭。桌面端以固定上限向右展开，窄屏时使用 Hero 的可用宽度；组件或窗口失去焦点时会收起，但不会丢弃尚未提交的内容。

首批引擎和固定请求格式如下：

| 引擎 | 搜索地址 | 关键词参数 | 用途 |
| --- | --- | --- | --- |
| Bing | `https://www.bing.com/search` | `q` | 默认，国内网络环境兜底 |
| Google | `https://www.google.com/search` | `q` | 国际网络 |
| DuckDuckGo | `https://duckduckgo.com/` | `q` | 隐私搜索 |
| 百度 | `https://www.baidu.com/s` | `wd` | 国内备用 |

搜索使用浏览器原生 GET 表单并在新标签页打开结果，不使用第三方搜索 API，也不调用 Dashboard Python 服务。输入期间不会发送联想或其它网络请求；页面不保存关键词、搜索记录或账号信息。`localStorage` 中只记录当前浏览器选择的引擎 ID，键名为 `homelab-search-engine-v1`；存储不可用、内容无效或 JavaScript 初始化失败时均回退到 Bing。静态 HTML 本身就是可用的 Bing 表单，因此关闭 JavaScript 后仍可搜索。空白提交不会导航，只会在当前组件内依次轮换八句轻量提示；轮换位置仅存在于当前页面会话，刷新后从第一句重新开始。

### 服务入口状态

当前 11 张 `.service-card` 提供导航，`href` 与可选 `data-probe-url` 是服务端探测目标的唯一来源。页面标题、检测中数量和卡片入场动画序号均根据实际卡片集合自动生成；每次刷新只发送一次同源批量请求。最终 HTTP 状态分类如下：

- 2xx/3xx：绿色“服务端可达”；
- 4xx：黄色“异常 · HTTP 状态码”；
- 5xx：红色“不可用 · HTTP 状态码”；
- DNS、TLS、超时或连接失败：红色“不可连接”；
- 状态 API 整体不可用或响应契约错误：灰色“状态未知”，不计为全部服务离线。

CLIProxy 点击根地址，但使用 `data-probe-url` 指向 `/management.html/`。卡片即使检测失败也保持可点击。

该状态验证入口节点经 Zoraxy 到上游的服务端链路，不等同于登录、业务功能或应用内部健康。如果显示服务端可达但当前设备打不开，应检查当前设备的 DNS、Tailscale、路由、证书缓存和浏览器环境。

### 背景选择和同步

页面打开背景选择器时优先请求 `GET /api/background-files`。后端扫描 `backgrounds/` 第一层的 `.jpg`、`.jpeg`、`.png`、`.webp` 和 `.avif` 原图，为每张图片生成或复用 480×270、质量 75 的 WebP 缩略图；选择器只加载这些缩略图，用户选中某张图片时才预加载并应用对应原图。目录必须包含 `bg.jpg`，原图继续平铺在第一层；后端管理的 `backgrounds/thumbnails/` 只保存可再生缓存。为保留旧后端降级能力，`backgrounds/` 仍不能包含会阻止目录列表的 index 文件。

清单响应只包含文件名、同源缩略图相对路径和本次缓存统计，不直接传输图片：

```json
{
  "version": 1,
  "backgrounds": [
    {
      "name": "bg.jpg",
      "thumbnail": "thumbnails/安全缓存键.webp?v=缓存版本"
    }
  ],
  "thumbnails": {
    "generated": 1,
    "reused": 20,
    "failed": 0
  }
}
```

首次升级后打开选择器会同步迁移已有图片，图片较多或磁盘较慢时可能等待约 120 秒；之后通常直接复用缓存。清单接口不可用、超时或响应非法时，页面会显示“已进入原图加载降级模式”，再回退到 `/backgrounds/` directory listing，因此旧后端仍可继续使用，但会恢复全量原图传输。单张缩略图生成或加载失败只让对应卡片回退到原图，不影响其它壁纸。

右键背景卡片时浏览器打开或保存的是 WebP 缩略图；需要原文件时，使用操作栏中的“查看原图”，页面会在新标签页打开当前横屏或竖屏标签已保存的同源原图，再通过浏览器右键保存。该入口不依赖壁纸 API 在线状态，也不会改变当前设置或发送偏好更新请求；如果原图已被外部删除，新标签页会显示静态资源的正常 404。

选择器按“普通壁纸”和“Bestdori 抽卡”分成两个可折叠栏目，两个栏目首次打开都展开，折叠状态只在当前页面会话中保留；状态文案同时显示总数及各栏目数量。

分类只发生在浏览器界面中，原图仍平铺保存在 `backgrounds/` 第一层；`thumbnails/` 子目录只承载后端缓存，不参与分类。前端通过抽卡服务生成的稳定文件名结构（角色、三星/四星/五星、标题、数字卡面 ID、`after_training`/`normal`，以及可选的重名序号）识别抽卡壁纸；`bg.jpg` 和其它名称都归入普通壁纸。手动重命名导致文件名不再符合该结构时，图片会显示在普通壁纸栏目。该分类不读取 `BestdoriCardDraws.json`，因此清理或重置去重记录不会改变已有图片所在的栏目。

选择图片后，前端发送现有请求：

```http
PATCH /api/background-preferences
Origin: https://home.c10h14n2o5.com
X-Requested-With: homelab-dashboard
Content-Type: application/json

{"mode":"landscape","background":"test.jpg"}
```

返回结构保持不变：

```json
{
  "version": 1,
  "landscape": "test.jpg",
  "portrait": "bg.jpg",
  "updatedAt": "2026-07-22T12:00:00Z"
}
```

本地 `homelab-background-state-cache-v1` 仅用于首次绘制和 API 故障降级。背景偏好与清单 API 不传输图片，只同步文件名和缓存地址；缩略图及选中的原图由 Zoraxy 静态服务传输。其它设备刷新后读取共享设置，没有实时推送；新原图只在用户主动使用上传或抽卡 API 时写入。

弹窗中的“上传图片”将 JPEG、PNG、WebP 或 AVIF 原始字节发送到状态服务。单个文件最多 50 MiB、解码后最多 100MP；服务端校验真实图片格式和扩展名，不转码。重名文件自动使用 `名称 (2).png`、`名称 (3).png` 等可用名称，不会覆盖现有图片或 `bg.jpg`。上传成功后服务端同步尝试生成缩略图，生成失败不撤销已经保存的原图；页面只把图片加入列表并聚焦缩略图，不会自动切换当前横屏或竖屏背景。

```http
POST /api/background-files/upload?name=wallpaper.png
Origin: https://home.c10h14n2o5.com
X-Requested-With: homelab-dashboard
Content-Type: application/octet-stream

<原始图片字节>
```

“删除当前背景”始终作用于当前编辑方向中带蓝框勾选的图片，并在操作前显示二次确认。`bg.jpg` 是不可删除的永久兜底；如果被删图片同时被横屏和竖屏槽位引用，两个槽位都会回退到 `bg.jpg`。后端首先使用 Windows 回收站；若回收站不可用且文件仍存在，接口返回一个有效期 60 秒、仅限该文件且只能使用一次的确认令牌，前端只有在用户再次确认永久删除后才会携带令牌重试。

```http
POST /api/background-files/remove
Origin: https://home.c10h14n2o5.com
X-Requested-With: homelab-dashboard
Content-Type: application/json

{"background":"wallpaper.png"}
```

回收站失败后的永久删除请求增加 `confirmationToken` 字段。成功响应会说明删除方式并返回更新后的完整背景偏好；直接伪造永久删除参数不能绕过首次回收站尝试。

#### Bestdori 卡面抽取

“我的回合，抽卡！”会打开独立参数弹窗，提供两种模式：

- 角色模式：从固定角色 ID 1–40 中选择一个角色，再从勾选的 3、4、5 星范围内随机抽取；
- 全随机模式：不指定角色，只在勾选的 3、4、5 星范围内随机抽取。

至少要选择一个星级。服务端先过滤尚未记录的卡面，再随机选择；优先下载训练后卡面，资源不存在时回退到普通卡面。抽取结果经过与上传相同的图片大小、像素、格式和原子发布校验后写入 `backgrounds/`，文件名保留角色、星级、标题和卡面 ID。成功后页面只刷新列表、展开“Bestdori 抽卡”栏目并聚焦新卡面，不自动改变横屏或竖屏当前背景；符合条件但没有未抽取卡面时会明确提示去重池已耗尽。

如果只有角色名称目录暂时不可用，角色模式会因无法选择角色而停用，但全随机模式仍可继续抽取，并在文件名与记录中使用“角色 ID”作为名称兜底；卡片目录或图片资源不可用时则会明确报错且不写入文件或去重记录。

```http
POST /api/background-files/draw
Origin: https://home.c10h14n2o5.com
X-Requested-With: homelab-dashboard
Content-Type: application/json

{"mode":"character","rarities":[3,4,5],"characterId":4}
```

全随机请求省略 `characterId`：

```json
{"mode":"random","rarities":[4,5]}
```

永久去重记录独立保存在非公开状态目录的 `HomeLabWallpaperState-data/BestdoriCardDraws.json`，记录卡面 ID、实际文件名、SHA-256、抽取时间和卡面元数据，不与浏览器缓存或其它抽卡工具共享。“清理失效记录”只删除背景文件已缺失或 SHA-256 不匹配的记录；“清空全部记录”需要二次确认。两项操作都只维护 JSON 记录，不会删除任何背景图片。记录文件损坏时，抽卡与历史维护 API 会安全失败，壁纸偏好、上传、删除、入口探测和健康检查仍可继续使用。

## Python 状态服务

`state-service/main.py` 使用标准库、Pillow 和 Send2Trash，提供：

- `GET /api/background-files`：扫描第一层原图、核对缩略图缓存并返回结构化清单；
- `GET /api/background-preferences`：读取并自愈状态；
- `PATCH /api/background-preferences`：更新横屏或竖屏槽位；
- `POST /api/background-files/upload?name=...`：流式接收、验证并保存一张图片；
- `POST /api/background-files/remove`：将图片移入回收站，必要时处理带短时令牌的永久删除；
- `GET /api/bestdori/characters`：读取固定角色 ID 1–40 的显示名称；
- `POST /api/background-files/draw`：按角色/星级或全随机参数抽取、验证并保存一张未记录卡面；
- `POST /api/bestdori/history/reconcile`：移除文件缺失或哈希不匹配的失效去重记录，不删除图片；
- `POST /api/bestdori/history/reset`：在前端二次确认后清空全部去重记录，不删除图片；
- `POST /api/service-status`：按输入顺序批量返回入口 HTTP 状态、耗时和稳定错误码；
- `GET /healthz`：返回状态、服务标识、版本和 PID；
- `POST /_control/shutdown`：仅供本机启动器使用的令牌控制端点，不得由 Zoraxy 代理。

安全约束：

- 强制监听 `127.0.0.1`；
- 所有写入 API 校验精确 Origin 和请求标记；JSON 与上传接口分别校验 Content-Type、Content-Length 和正文上限；
- 上传最多 50 MiB / 100MP，只接收真实 JPEG、PNG、WebP、AVIF，并以同目录临时文件验证后原子发布；
- 缩略图执行 EXIF 方向修正、居中裁切和 WebP 编码，经同目录临时文件、磁盘刷新和原子替换写入；缓存目录必须是真实目录而非符号链接；
- Bestdori 目录和图片下载只访问代码中固定的 HTTPS API 与资源地址，不接受客户端提供远程 URL；下载图片沿用上传的大小、像素、格式和原子发布约束；
- 抽卡使用独立的非阻塞互斥锁；永久去重记录只在卡面验证并发布成功后原子写入，符合条件的未记录池耗尽时不会回退到重复卡面；
- 拒绝未知字段、路径穿越、不支持的扩展名、符号链接和不存在文件；
- `bg.jpg` 永久禁止删除；永久删除必须使用回收站失败后签发的 60 秒单次令牌；
- 探测只接受配置域后缀的单层 HTTPS 子域，禁止凭据、fragment、非 443 端口、IP、外部域和多层子域；
- TCP 强制连接回环 Zoraxy，但 HTTP Host、TLS SNI 和证书 hostname 校验继续使用目标域名；不使用系统 DNS、代理或跳过 TLS 校验；
- 最多 8 个全局并发、32 个目标和 5 次重定向；同一批次共享超时截止时间，每一跳重新执行目标校验；
- 状态更新使用进程内互斥、`fsync` 和同目录 `os.replace`；
- EXE、配置和数据目录必须位于公开 Web 根之外；
- Zoraxy 继续负责 HTTPS 和首页 Basic Auth。

### 严格 v2 配置

新版只接受完整的 v2 配置，v1、缺失字段和未知字段都会拒绝启动。升级时删除旧配置并重新运行首次引导：

```json
{
  "version": 2,
  "backgroundDir": "D:\\Zoraxy\\www\\html\\backgrounds",
  "allowedOrigins": ["https://home.c10h14n2o5.com"],
  "listen": "127.0.0.1:8787",
  "stateDir": "HomeLabWallpaperState-data",
  "controlToken": "由首次引导生成的随机令牌",
  "probeConnectAddress": "127.0.0.1:443",
  "probeDomainSuffix": "home.c10h14n2o5.com",
  "probeTimeoutSeconds": 5
}
```

`probeConnectAddress` 只允许回环 IP，`probeTimeoutSeconds` 允许 1–5 秒。服务状态请求示例：

```http
POST /api/service-status
Origin: https://home.c10h14n2o5.com
X-Requested-With: homelab-dashboard
Content-Type: application/json

{"targets":["https://anon.home.c10h14n2o5.com/"]}
```

```json
{"results":[{"target":"https://anon.home.c10h14n2o5.com/","status":200,"latencyMs":38,"error":null}]}
```

稳定错误码为 `invalid_target`、`timeout`、`tls_error`、`redirect_rejected`、`connection_error` 和 `request_error`。重定向被拒绝时保留最后收到的真实 3xx，但前端显示红色失败状态。

## EXE 模式和首次运行

```text
HomeLabWallpaperState.exe
HomeLabWallpaperState.exe serve --config <path>
HomeLabWallpaperState.exe monitor --config <path>
HomeLabWallpaperState.exe configure
HomeLabWallpaperState.exe stop
HomeLabWallpaperState.exe restart
```

无参数双击行为：

1. 配置不存在时进入首次引导；
2. API 未运行时隐藏启动同一 EXE 的 `serve` 模式；
3. 等待 `/healthz` 确认身份和健康；
4. 仅在服务标识和接口版本完全匹配时复用已运行的 API，不创建第二个后端；
5. 进入可见监视器。

如果端口由其它 HTTP 服务或接口版本不兼容的旧后端占用，启动器会拒绝复用和启动第二个实例；应先停止占用进程，再重新启动当前版本。

监视器每 60 秒显示 API、PID、背景槽位、探测配置、目录和最近日志。按 `R` 立即刷新；按 `Q` 或关闭窗口只关闭监视器，后端继续运行；按 `X` 进入“大退”确认界面，只有精确输入大写 `Y` 并按 Enter 才会停止后端 API 并关闭监视器，其它输入会取消并返回默认界面。确认时如果 API 已经不可用，监视器会直接退出且不会操作未知进程；关闭请求被拒绝、超时或异常时则保留监视器并显示失败原因。连续三次检查失败时，监视器会自动尝试恢复一次。关闭监视器后不再守护，之后再次双击 EXE 即可检查并恢复。

`service.log` 只记录服务启动、停止和异常，不记录正常 HTTP 请求。日志按 48 KiB 轮转并只保留一个 `service.log.1` 备份，总量约 96 KiB。

首次引导默认将壁纸目录设为 `D:\Zoraxy\www\html\backgrounds`；配置和状态仍保存在 EXE 所在目录：

```text
<Zoraxy程序目录>/
├── HomeLabWallpaperState.exe
├── HomeLabWallpaperState.json
├── HomeLabWallpaperState-data/
│   ├── BestdoriCardDraws.json
│   ├── state.json
│   └── service.log

D:/Zoraxy/www/html/backgrounds/
├── bg.jpg
├── <其它原始壁纸>
└── thumbnails/                  # 后端自动生成，可随时重建且无需备份
```

引导会验证 `bg.jpg`、公开目录边界、回环探测入口、域名后缀和超时；最后询问是否在当前用户 `shell:startup` 中创建快捷方式。本次 v2 升级按全新配置处理，不导入旧状态。

## 构建

当前开发环境使用 Python 3.14。构建依赖固定为 PyInstaller 6.21.0、Pillow 12.3.0 和 Send2Trash 2.1.0；Bestdori HTTPS 请求使用 Python 标准库，不新增第三方依赖。三项固定依赖会打包进生产 EXE，部署机器仍不需要安装 Python 或虚拟环境。可以使用现有 Python，也可以创建仓库本地 `.venv`：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\scripts\build-state-service.ps1
```

没有 `.venv` 时，构建脚本使用 PATH 中的 `python`。构建流程为：

```text
py_compile
-> unittest
-> 依赖导入与 JPEG/PNG/WebP/AVIF 编解码能力预检
-> PyInstaller --onefile --console --noupx
-> 临时端口 EXE 冒烟测试
-> 健康检查、GET、PATCH、真实 PNG 上传与缩略图清单、离线抽卡历史维护、批量状态 API、控制关闭
-> SHA-256
-> dist/HomeLabWallpaperState.exe
```

构建脚本不会自动安装依赖。任一固定依赖或 Pillow 必需编解码器缺失时会给出明确安装命令。上传和抽卡历史维护冒烟测试只操作构建目录中的临时背景/状态文件并随构建临时目录清理，不会请求 Bestdori 公网，也不会向系统回收站写入测试文件。

## 部署和迁移

1. 记录当前提交、Zoraxy 版本、旧 EXE 哈希，并备份旧 EXE、JSON、状态、日志、`index.html` 和 Zoraxy 配置。
2. 停止旧后端，将新版 EXE 复制到非公开的 Zoraxy 程序目录。本次配置版本、背景状态版本和 Bestdori 抽卡历史版本均不变，不要删除现有 `HomeLabWallpaperState.json` 或 `HomeLabWallpaperState-data/`；只有仍在使用旧 v1 配置时才按“严格 v2 配置”一节重新引导。
3. 核对构建输出 SHA-256 后双击新版 EXE，确认 `/healthz` 的服务标识正确且接口版本为 `5`；接口版本不匹配时启动器会拒绝复用旧进程。
4. 先验证壁纸 GET/PATCH、`GET /api/background-files`、上传/删除、离线抽卡历史维护和 Homepage 同源 `/api/service-status`，再部署 V2.3.0 `index.html`。推荐按此顺序升级；若前端先上线，会自动回退旧目录列表并显示原图加载降级提示，不会获得缩略图流量优化。
5. 首次打开背景选择器时等待已有原图生成缩略图，确认后续打开复用缓存；再检查每次刷新只发送一次同源批量状态请求，以及全部卡片、壁纸同步、上传、回收站删除和两种抽卡模式。
6. 最后删除 11 个子服务 Host Rule 中旧的 CORS 响应头，并验证 AstrBot 插件 JS/CSS 和其它服务。

若回滚到旧前端，必须同时恢复兼容的旧 EXE、旧 JSON、旧 `index.html` 和 11 个 Host Rule 的 CORS 响应头。升级前应独立备份部署机 `backgrounds/` 第一层的原图和 `HomeLabWallpaperState-data/BestdoriCardDraws.json`；`backgrounds/thumbnails/` 是完全可再生的缓存，不需要备份，也不应作为用户数据恢复。旧版 EXE 不会使用去重记录，但不应因此删除它。从 Windows 回收站恢复的图片需保持原文件名；下次打开背景选择器时会重新生成缩略图，随后可在网页中重新选择对应槽位。已经永久删除的原图只能从独立备份恢复。

### Zoraxy Virtual Directory

现有代理保持不变：

```text
MatchingPath： /api/
Domain：       127.0.0.1:8787/api/
```

代理仍只需保留 GET/PATCH/POST、请求体、`Content-Type`、`Content-Length`、`Origin` 和 `X-Requested-With`，禁止缓存，并复用首页 Basic Auth。不要代理 `/_control/`，不要把 8787 暴露到局域网、Tailscale 或公网。

服务端探测不依赖本机 DNS，可在部署机上用等价命令诊断 Zoraxy 回环链路：

```powershell
curl.exe -sS -L --resolve "anon.home.c10h14n2o5.com:443:127.0.0.1" --connect-timeout 5 --max-time 10 -o NUL "https://anon.home.c10h14n2o5.com/"
```

### 卸载

```powershell
.\scripts\uninstall-state-service.ps1 `
  -ExePath '<Zoraxy程序目录>\HomeLabWallpaperState.exe'
```

脚本请求后端停止、删除当前用户 Startup 快捷方式，并在管理员权限下删除遗留 SYSTEM 任务。EXE、配置、状态、日志和背景默认保留。
如果 EXE 已经被手动删除，脚本会跳过停止请求并继续清理 Startup 快捷方式和遗留计划任务。

## 验证

```powershell
.\scripts\verify-project.ps1
.\scripts\verify-project.ps1 -Build
```

验证包括：PowerShell 语法、前端两段脚本、至少一张服务卡片、动态数量与动画序号、单次批量请求、状态分类与故障降级、Python 探测和 API 测试、Git 忽略规则以及维护契约。验证会拒绝手写 `--card-index`；`-Build` 额外执行 PyInstaller 和真实 EXE 冒烟测试。

人工部署后还应检查：

- 双击时 API 缺失会启动后端，API 已运行时不会重复启动；
- 关闭监视器后 `/healthz` 仍正常；
- 按 `X` 后只有精确输入大写 `Y` 并回车才会停止 API 和关闭监视器；其它输入取消，停机失败时监视器保持运行；
- `shell:startup` 登录后显示监视器；
- 网页可以读取和更新横竖屏背景；
- 打开背景选择器时默认只请求 WebP 缩略图，选择一张后才新增对应原图请求；清单故障时会提示原图加载降级模式；
- 网页可以上传真实图片但不会自动切换，重名时自动编号；
- 删除非默认背景会二次确认、优先进入回收站并让所有引用槽位回退；`bg.jpg` 无法删除；
- 角色模式和全随机模式只接受 3–5 星，抽卡成功会新增并聚焦图片但不会自动切换背景；
- 重复卡面不会再次抽取；清理失效记录和清空全部记录不删除背景图片，清空前会二次确认；
- Bestdori 不可访问或去重记录损坏时，已有背景、上传/删除、入口探测和 `/healthz` 仍正常；
- 一个 2xx/3xx、4xx 和 5xx/网络失败入口分别显示绿、黄、红；
- 停止状态 API 时全部卡片显示“状态未知”，而不是误报全部离线；
- 删除旧的 CORS 响应头后 Homepage 状态正常，AstrBot 插件资源恢复；
- EXE、JSON、状态和日志不能通过公开站点下载。

## 维护原则

- 修改后先运行统一验证，不要跳过失败项。
- 新增或删除服务卡片时只需维护卡片 HTML，并手动更新 README 中的当前数量和架构说明；页面数量文案、动画序号和前端验证会根据实际卡片自动适配。
- `backgrounds/bg.jpg` 作为兜底进入 Git；其它原图不进入 Git，应独立备份；`backgrounds/thumbnails/` 是可再生缓存，不备份、不手工管理。
- `BestdoriCardDraws.json` 位于非公开状态目录并应随背景图片一起备份；清理或重置记录不会清理对应图片。
- 从回收站恢复的背景不会自动恢复横竖屏选择；永久删除没有应用内撤销能力。
- `state.json` 格式与前端 API 是兼容边界，迁移或升级不得随意改变。
- `index.html`、状态服务和 Zoraxy 配置分层维护；服务状态 API 契约变化必须同步更新前端验证。
