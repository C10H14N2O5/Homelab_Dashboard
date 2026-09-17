# Zoraxy HomeLab Service Dashboard

这是一个由 Zoraxy 静态网页服务器托管的 HomeLab 服务导航页。前端使用原生 ES 模块，第三方 Markdown 渲染库随站点部署，不依赖外部 CDN；仅监听回环地址的 Python Dashboard 状态服务负责跨设备同步卡片、壁纸与笔记，并经本机 Zoraxy 批量检测服务入口。开发时直接运行 Python，生产环境通过 PyInstaller 分发单文件 `HomeLabWallpaperState.exe`，不需要安装 Python 或虚拟环境。

> [!IMPORTANT]
> 本仓库是维护者基于真实 Zoraxy + Windows HomeLab 环境开发的参考实现，不是面向任意环境的一键安装产品。公开版使用保留示例域名 `*.home.example.com`，`D:\Zoraxy\...` 和服务卡片地址均为部署示例；使用前必须按“公开仓库定制清单”替换为自己的域名、路径、入口和认证策略。

## 项目概览

| 项目 | 当前实现 |
| --- | --- |
| 页面入口 | `index.html` |
| 背景图片 | `assets/default-background.svg` 导出为 `backgrounds/bg.jpg` 随仓库提供兜底；其余原图由部署机器本地保存，缩略图由后端生成 |
| 服务入口 | 首次升级从 `index.html` 原子导入现有卡片，之后可在网页中增删改、排序并跨设备同步 |
| 快捷入口 | Services / Quick Links 双频道；Quick Links 与 Services 分频道独立保存和版本冲突保护 |
| 联网搜索 | 纯前端 Bing、Google、DuckDuckGo、百度、哔哩哔哩搜索；默认 Bing，无 JavaScript 时仍可提交 |
| 状态服务 | Python 服务；提供卡片配置、壁纸偏好、图片管理、Bing 每日一图、Bestdori 卡面抽取与批量入口探测，生产为 Windows x64 单文件 EXE |
| API 地址 | `127.0.0.1:8787`，只能监听本机回环地址 |
| 运行角色 | 同一 EXE 提供隐藏 API 后端和可见终端监视器 |
| 自动启动 | 当前用户 `shell:startup` 快捷方式，登录后显示监视器 |
| 配置与状态 | 严格 v4 JSON 配置，兼容未启用笔记的完整 v3；卡片、背景及抽卡状态与笔记目录独立 |
| 前端依赖 | 无 CDN、无框架、浏览器原生 ES 模块、内联 SVG |
| 笔记 | 多篇 Markdown、任务清单、图片与动图、自动保存、本机恢复草稿、独立目录备份与手动对账 |

## 文件结构

```text
zoraxy-homelab-dashboard/
├── assets/
│   ├── default-background.svg   # 项目自有兜底壁纸源文件
│   ├── dashboard.css            # Homepage 样式，使用稳定部署路径
│   ├── icons/
│   │   └── homepage-badge.svg   # 页面图标默认金边徽章
│   └── js/
│       ├── app.js              # 原生 ES 模块入口与独立降级编排
│       ├── core-http.js        # 共用超时请求
│       ├── background-manager.js
│       ├── background-upload.js
│       ├── bing-daily.js
│       ├── bestdori.js
│       ├── search.js
│       ├── channel-switch.js
│       ├── service-status.js
│       ├── card-svg.js          # SVG 双重校验所需的前端白名单与安全 DOM 导入
│       ├── dialog-motion.js      # 卡片与图标对话框共用的开合动效控制器
│       ├── card-editor.js       # 当前频道卡片草稿、表单、删除和鼠标排序
│       ├── card-manager.js      # 首次导入、服务器/缓存降级和动态卡片渲染
│       ├── page-settings.js     # “网页设置”菜单的键盘、焦点和收起行为
│       └── favicon-manager.js   # favicon 与页面图标的独立管理、预览和默认回退
├── backgrounds/
│   ├── .gitignore               # 仅放行项目兜底导出图
│   ├── bg.jpg                   # SVG 导出的默认占位壁纸
│   └── thumbnails/              # 运行时生成的 WebP 缩略图缓存，不进入 Git
├── state-service/
│   ├── main.py                  # 稳定的薄入口；转交 homelab_state.launcher.main
│   ├── homelab_state/
│   │   ├── core.py              # 共享常量、异常、校验和原子 JSON 写入
│   │   ├── config.py            # 严格 v3 配置模型、解析和路径边界
│   │   ├── backgrounds.py       # 背景状态、上传、缩略图和删除
│   │   ├── cards.py             # 卡片模型、SVG 清洗、原子初始化和修订号存储
│   │   ├── favicon.py           # favicon 与页面图标共用的状态存储、哈希和资源读取
│   │   ├── bing.py              # Bing 固定源客户端、市场回退和每日图保存
│   │   ├── bestdori.py          # 固定源客户端、抽卡和去重历史
│   │   ├── probe.py             # 回环固定的 HTTPS 批量入口探测
│   │   ├── http_api.py          # HTTP 服务、请求安全边界和 API 路由
│   │   └── launcher.py          # 首次引导、后端生命周期和终端监视器
│   ├── tests/                   # 按上述模块划分的 Python 单元测试
│   └── test_main.py             # 薄入口兼容性测试
├── scripts/
│   ├── build-state-service.ps1  # 测试、PyInstaller 构建和 EXE 冒烟测试
│   ├── package-release.ps1      # 组装 Windows Release 包和 SHA-256
│   ├── uninstall-state-service.ps1 # 卸载EXE自启动注册
│   └── verify-project.ps1       # 全仓库验证入口
├── audits/
│   ├── README.md                # 审计报告索引、管理规则和模板
│   └── YYYY-MM-DD-*-audit.md    # 按日期归档的历次审计报告
├── requirements.txt             # Python 构建依赖及固定版本
├── index.html                   # 页面结构、首屏引导脚本和内联 SVG
├── licenses/                    # EXE 捆绑组件的完整许可证
├── LICENSE                      # 项目 MIT License
├── ASSETS.md                    # 自有、品牌和运行时资产边界
├── THIRD_PARTY_NOTICES.md       # EXE 第三方组件说明
├── CHANGELOG.md
├── TODO.md
└── README.md
```

历次代码、架构和安全审计结果请参见 [审计报告索引](audits/README.md)。

仓库分发兜底背景 `backgrounds/bg.jpg` 与默认页面徽章 `assets/icons/homepage-badge.svg`，不分发其它背景、缩略图缓存、构建出的 EXE、`DashboardCards.json`、`DashboardFavicon.json`、`DashboardPageIcon.json`、其它 JSON 状态或日志。公开 Web 根目录只应部署 `index.html`、`assets/` 和 `backgrounds/`；不要复制 README、其它脚本、后端源码、EXE、配置、状态或 Git 文件。`assets/` 使用稳定路径，部署时直接覆盖完整的 `assets/` 与 `index.html`，然后使用 `Ctrl+F5` 强制刷新验收；版本和回滚由 Git Tag/Release 管理。

项目代码、文档和项目自有占位资产按 [MIT License](LICENSE) 发布。品牌标识、用户壁纸以及运行时获取的第三方卡面不属于本项目许可范围，详见 [ASSETS.md](ASSETS.md)；二进制依赖及完整许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 和 `licenses/`。

GitHub 公共镜像保留私人 Gitea 历史的提交顺序、作者名、作者日期、说明和版本标签，并将历史邮箱映射为 GitHub noreply 地址。公开版额外使用项目自有占位背景、保留示例域名和公开发行门禁，因此两边的 Commit SHA 不同；功能开发仍以私人 Gitea 为权威来源，公共镜像通过人工审核同步。

前端直接使用浏览器原生 ES 模块，不需要 Node.js、npm 或打包器参与生产运行。模块必须经 Zoraxy 或其它 HTTP 静态服务器加载；直接双击 `index.html` 的 `file://` 预览不属于支持方式。开发时可在仓库根目录运行 `python -m http.server 8765 --bind 127.0.0.1`，再访问 `http://127.0.0.1:8765/`；此纯静态预览中的同源 `/api/` 请求失败属于预期，完整联调仍以 Zoraxy 环境为准。

## 运行架构

### 笔记

搜索栏下方的“笔记”胶囊在页面内展开，桌面采用列表／正文双栏，手机每次展开先显示列表。已有笔记默认预览，新建直接编辑；各浏览器独立记住选中笔记。列表的 ↑／↓ 用于排序，× 确认后删除；桌面另支持拖拽柄排序。底部“保存全部”保存所有修改，面板内 `Ctrl/Cmd+S` 执行相同操作。

笔记胶囊与搜索一起浮现，收起态共享搜索胶囊的透明度与玻璃效果。横向和纵向以相同缓动同步展开，统一采用较长的 300ms 时长，同时到位后再用 120ms 淡入内容；收起先淡出内容，再同步缩回胶囊。正文面板保留较深底色。减少动态效果模式直接切换，窗口缩放会结束当前过渡并适配新尺寸。

切换笔记时，侧栏的蓝色选中高光以 280ms 缓动滑向目标标题；正文区域先用 120ms 渐出，等目标正文读取、Markdown 渲染完成后用 160ms 渐入。快速连点会从当前动效位置接向最后一次选择，较早返回的请求不会覆盖当前正文；切换期间暂时禁止正文编辑，减少动态效果时直接呈现结果。手机保持每次展开先显示列表。

正文支持 Markdown、表格、删除线、可点击任务清单及命名语言的代码高亮。原始 HTML 不执行；外链图片仅从 HTTPS 原地址加载，不由后端抓取。markdown-it 15.0.2、Highlight.js 11.12.0、DOMPurify 3.4.15 的浏览器资源、许可证和来源记录保存在 `assets/vendor/`，部署时需要完整复制。后端新增固定版本 Markdown 解析依赖，用于独立核对附件引用。

“网页设置 → 笔记设置”提供自动保存开关与间隔（默认 60 秒、允许 10～3600 秒）、切换笔记时保存（默认开启）、失焦收起（默认关闭）。设置窗口复用“修改卡片”的淡入、轻微上移和缩放过渡；关闭按钮与 Esc 等待退场完成后恢复入口焦点，减少动态效果时直接切换。设置跨设备共享，在展开面板或返回页面时刷新。手动保存成功显示 toast，自动保存成功静默更新状态；主存储成功而备份失败时显示“备份待同步”，不把备份故障等同于文字保存失败。

点击面板 × 后锁定编辑并等待图片与正文保存完成，再自动收起；失败时保留面板。刷新或关闭标签页时，有未同步内容会尝试触发浏览器离开确认。IndexedDB 保留本机应急草稿，重新进入后由用户选择恢复或丢弃；不支持持续离线写作，检测到主存储故障后进入只读。本机草稿不等于服务器保存，也不保证浏览器异常退出后全部内容均可恢复。

每篇笔记使用稳定 ID、独立修订和原子 JSON 文件，排序及共享设置独立存储。不同设备修改同一篇发生冲突时保留草稿，选择另存为新笔记或载入服务器版本，不静默覆盖。默认上限为 500 篇、标题 200 个字符、单篇正文 256 KiB、文本合计 32 MiB。

图片选择或粘贴后立即上传，成功后才插入正式引用；上传中的所属笔记等待图片完成后保存，其他笔记正常保存。支持 JPEG、PNG/APNG、GIF、WebP、AVIF，原文件与动画保留；单张最多 20 MiB，合计最多 2,000 张／5 GiB，并限制像素、帧数与解码时间。网络或临时服务错误自动重试一次，其余失败可重试或移除。

笔记主目录位于本机磁盘，图片与最新文本备份位于另一条本地或 NAS UNC 路径。每篇内容单独备份；失败后按 30、60、120、300 秒退避，之后每 300 秒重试，关闭网页不停止后台任务。NAS 操作由有超时的独立隐藏进程执行，不在主存储锁内等待网络。两处均不保留旧正文历史。

正文保存或笔记删除后，无任何已保存笔记引用的图片及时清理，不设置长期草稿保留期。当前未提交上传按临时任务处理；旧草稿或旧备份中的图片引用可能已失效，恢复时显示缺图提示。NAS 不可用期间记录清理任务，恢复后重新核对引用再删除。

设置中的“检查／恢复备份”先只读展示差异，再逐项选择以主目录为准、从备份恢复或暂不处理；删除标记防止正常删除的笔记被默认恢复。未选择的冲突不会自动覆盖，检查后发生变化的项目需重新核对。初次切换到已有数据的备份目录不会自动合并。

单篇文件、顺序或设置文件损坏／丢失时进入只读状态，仍可在设置中检查并选择备份恢复。集合标识 `collection.json` 或同步目录记录 `sync.json` 损坏／丢失时，后端保留文件并停止初始化笔记；需要先保留原目录，再将主目录配置到新的本机空目录、重启后从原备份目录手动检查恢复，或由管理员修复原元数据。不会用空数据自动覆盖备份。

所有接口位于 `/api/notes`：列表／单篇读取与保存、顺序／设置、图片上传和读取、状态／备份重试、异步对账和应用。Zoraxy 应继续将整个 `/api/` 转发给同一后端，并对 HTML、API 和图片使用一致的访问认证；不要公开映射笔记数据目录。

本地视觉和端到端预览可执行：

```powershell
.\.venv\Scripts\python.exe scripts/preview-notes.py
```

默认打开 `http://127.0.0.1:8765/`，API 监听 `127.0.0.1:8788`，测试数据与日志保留在忽略目录 `output/notes-preview/`。首次启动附带可修改的示例笔记；`--empty` 跳过示例，`--port` 与 `--api-port` 可更换端口。预览只提供页面、静态资源和同源 API，不公开 README、源码、配置或测试数据目录；结束预览进程会停止其专用后端。

开发端具备 Node.js、Playwright 与 Edge 后，可对运行中的隔离实例执行 `node scripts/notes-e2e.mjs`，覆盖保存、上传、草稿恢复、冲突、慢响应及手机列表交互；脚本创建独立 QA 笔记并在结束后清理，恢复原设置和顺序。普通前端检查不依赖这套运行实例。

`node scripts/notes-motion-e2e.mjs` 另行逐帧检查入场、开合顺序、几何变化、减少动态效果与窗口缩放，仅执行笔记读取请求，截图与采样记录写入 `output/notes-motion-e2e/`。

`node scripts/notes-selection-e2e.mjs` 使用浏览器内拦截的笔记数据，检查正文渐变、高光缓动、快速切换、慢请求、列表滚动、手机与减少动态效果；不修改预览服务器数据，截图和采样写入 `output/notes-selection-e2e/`。

### 既有主页接口

浏览器只访问 Homepage 同源 API：

```text
浏览器
├── GET /api/background-files             -> Python 状态服务生成/复用缩略图清单
├── GET /backgrounds/thumbnails/...        -> Zoraxy 静态缩略图
├── GET /backgrounds/<文件名>              -> 应用或查看当前壁纸时传输原图
├── GET /backgrounds/                      -> Zoraxy 静态目录列表（旧后端降级）
├── GET/PATCH /api/background-preferences  -> Zoraxy Virtual Directory
├── GET/POST/PATCH /api/cards              -> Python 状态服务原子初始化及按频道保存
├── GET/PATCH /api/favicon                 -> Python 状态服务读取或保存私有 SVG 状态
├── GET /api/favicon.svg?v=<SHA-256>        -> Python 状态服务返回当前哈希对应 SVG
├── GET/PATCH /api/page-icon               -> Python 状态服务管理独立的页面图标
├── GET /api/page-icon.svg?v=<SHA-256>      -> Python 状态服务返回页面图标 SVG
├── POST /api/background-files/...         -> 127.0.0.1:8787/api/...
│                                               -> Bing 固定 HTTPS 首页图端点
├── GET/POST /api/bestdori/...              -> Python 状态服务
│                                               -> Bestdori 固定 HTTPS API/资源
└── POST /api/service-status               -> Python 状态服务
                                                -> 127.0.0.1:443 Zoraxy
                                                -> 各服务卡片对应的上游
```

API 与 Zoraxy 没有进程级启动依赖。API 初始化只读取背景目录、配置和状态文件；Zoraxy 只在浏览器请求到达时反向代理。因此两个当前用户 Startup 入口可并行启动，不需要设置顺序。

## 前端行为

### 多搜索引擎搜索

Hero 标题下方提供一枚默认收起的小胶囊，当前引擎的图标、名称和轻量 `Alt + ~` 键帽在其中居中排列；点击后外层以约 300ms 向右展开为输入框，同一枚“图标 + 名称”胶囊同步缩小为引擎列表按钮，键帽淡出且不再占位，不使用元素替换或额外三角符号。列表只显示五个引擎的图标和名称，并可通过按钮、方向键、Home/End、Enter/Space 和 Escape 选择或关闭。桌面端以固定上限向右展开，窄屏时使用 Hero 的可用宽度；组件或窗口失去焦点时会收起，但不会丢弃尚未提交的内容。

页面焦点不在输入、选择或可编辑控件中，且没有对话框打开时，按住 `Alt` 并按键盘左上角的物理 Backquote 键即可呼出搜索并聚焦输入框；实现依据 `KeyboardEvent.code === "Backquote"`，不受键盘字符映射影响。链接、卡片和普通按钮获得焦点时仍可呼出；裸 Backquote、IME 组词、AltGr、其它修饰键组合和长按重复事件不会触发。搜索输入框已聚焦时不会响应全局呼出组合键。

搜索输入框拥有焦点时，`Alt+J` 会循环切换到上一个引擎，`Alt+L` 会循环切换到下一个引擎；首尾相接，输入内容和焦点保持不变。连续按键会在约 90ms 内合并，并在动画退出或进入阶段继续累计逻辑目标；新按键不会把正在运行的图标轨道强制拉回起点，当前轨道结束后只向最新目标接续一次。每轮换轨都带有独立代次，已取消但迟到的旧动画回调不会覆盖或清除更新后的目标。图标和名称用每轮约 180ms 完成横向换轨，`Alt+J` 的新引擎从左侧进入，`Alt+L` 从右侧进入，外层搜索框不重新缩放。减少动态效果时保留 90ms 合并逻辑，但立即显示最终引擎。增强初始化成功后，输入框通过 `aria-keyshortcuts="Alt+J Alt+L"` 暴露快捷键，不增加额外的可见教学文案。

当前引擎和固定请求格式如下：

| 引擎 | 搜索地址 | 关键词参数 | 用途 |
| --- | --- | --- | --- |
| Bing | `https://www.bing.com/search` | `q` | 默认，国内网络环境兜底 |
| Google | `https://www.google.com/search` | `q` | 国际网络 |
| DuckDuckGo | `https://duckduckgo.com/` | `q` | 隐私搜索 |
| 百度 | `https://www.baidu.com/s` | `wd` | 国内备用 |
| 哔哩哔哩 | `https://search.bilibili.com/all` | `keyword` | 站内视频搜索 |

搜索使用浏览器原生 GET 表单并在新标签页打开结果，不使用第三方搜索 API，也不调用 Dashboard Python 服务。输入期间不会发送联想或其它网络请求；页面不保存关键词、搜索记录、账号信息或引擎选择。每次刷新、新标签页或重新打开浏览器都从 Bing 开始，切换结果只存在于当前页面内存中；增强初始化会尽力删除旧版本遗留的 `homelab-search-engine-v1` 本地存储项，清理失败不会影响搜索。静态 HTML 本身就是可用的 Bing 表单，因此关闭 JavaScript 或增强初始化失败后仍可使用 Bing。空白提交不会导航，只会在当前组件内依次轮换八句轻量提示；轮换位置仅存在于当前页面会话，刷新后从第一句重新开始。

### Services / Quick Links 双频道

服务卡片区域默认显示 Services，不保存频道状态。标题式玻璃按钮可切换到 Quick Links；五个快捷入口依次为 Bilibili、GitHub、Gmail、ChatGPT 和 Gemini，均使用内联 SVG、官方主页以及带 `noopener noreferrer` 的新标签页链接。Quick Links 不包含状态、副标题或探测目标，切换频道也不会新增服务状态请求；批量探测始终只读取现有 `.service-card`。

宽度不小于 768px 且未启用减少动态效果时，按钮先用 800ms 向左翻面，面板延迟 200ms 后执行同向 800ms 翻面，两面按 Services 与 Quick Links 两个网格中较高者保持相同尺寸。窄屏下不使用 3D：按钮先淡变，120ms 后旧网格整体淡出，新频道卡片再按 DOM 顺序以 25ms 间隔逐张淡入并轻微上移。切换期间按钮保持焦点，离场面立即停止接收焦点，入场完成后才恢复交互。

`prefers-reduced-motion` 下频道直接切换，不播放翻面、错峰或逐卡动画。静态 HTML 默认暴露可用的 Services 面并隐藏、隔离 Quick Links；JavaScript 不可用或频道初始化失败时仍可打开全部服务入口。

### 卡片配置与首次导入

“修改卡片”位于页脚“网页设置”菜单内，并按打开时所在频道编辑 Services 或 Quick Links。主编辑器及其放弃、删除和保存确认框沿用背景选择器的淡入、轻微上移和缩放过渡，关闭动画完成后才释放原生对话框并把焦点恢复到“网页设置”；系统启用减少动态效果时会立即完成。卡片选择区使用与主页相同思路的响应式网格：常规桌面宽度下，默认 11 张卡片和末尾新增卡排成 6 列 × 2 行；较窄窗口依次收缩列数，767px 以下两列、520px 以下一列，超出可见高度后只在选择区内纵向滚动，不再横向长距离滑动。编辑器会在内存中统一暂存连续新增、字段修改、删除和桌面鼠标拖动排序；将拖动卡放到任一目标卡片上时，会直接占用该卡片原本的位置，其间卡片依次顺移，不再区分目标卡片的左右半区。删除与最终保存分别确认，关闭或离开页面时会提醒放弃未保存草稿。每个频道最多 32 张，也允许明确清空；移动端可以增删改和保存，但排序需在桌面完成。

新后端尚无 `DashboardCards.json` 时，`GET /api/cards` 会明确返回未初始化状态。配套新前端随后从静态 HTML 提取现有 11 张 Services 与 5 张 Quick Links，在前后端分别验证字段、HTTPS URL 和内联 SVG，再通过 `POST /api/cards/initialize` 一次性写入两个频道。该导入只允许成功一次；并发首次访问中失败的一方会重新读取服务器结果，后续发布不会用新的静态默认值覆盖用户数据。状态文件损坏或版本不兼容不会被当成首次运行，也不会被自动重置。

正常加载以服务器为真源，并把最后一份完整有效配置缓存在当前浏览器。API 整体不可用时优先显示该缓存，无缓存时保留静态 HTML 卡片；缓存只负责导航降级，离线时不能保存。每个频道使用独立修订号，保存时若服务器已经被其它设备更新会返回冲突并要求重新载入，不会静默覆盖或自动合并。

卡片图标通过本地 `.svg` 文件导入；浏览器只读取文件文本，不向公开目录单独上传图标。为兼容常见图标导出格式，前后端允许一个位于根 `<svg>` 下的简单纯文本 `<title>`，也接受无命名空间的行内 `style` 属性，但二者都会在清洗时移除，不会进入保存结果或页面 DOM。真正的 `<style>` 样式表、`class`、脚本、事件属性、外部资源、嵌入图片、动画、DTD 和实体仍会被拒绝；元素数量、嵌套、图标与请求体大小也继续受限。渲染不会把服务器字符串直接赋给 `innerHTML`，内部渐变、遮罩和裁剪 ID 会加上卡片级前缀以免不同图标互相引用。

### 网页设置与图标

页脚“网页设置”按钮在上方打开包含“修改卡片”、“网站图标”和“页面图标”的菜单，支持方向键、Home、End、Escape、外部点击和焦点离开。网站图标编辑器只接受本地 `.svg` 文件，选择后先在当前页面形成经过清洗的安全预览草稿，点击保存后才写入服务器；移除已有图标需要单独二次确认。写入期间会锁定重复操作，关闭后焦点回到“网页设置”，减少动态效果时不等待过渡。

全站 favicon 独立保存在 Web Root 之外的 `DashboardFavicon.json`，不创建公开 `favicon.svg`，也不占用卡片修订号。页面加载时读取 `GET /api/favicon`，按完整 SHA-256 构造 `/api/favicon.svg?v=...` 并维护 `<link rel="icon" type="image/svg+xml">`；保存或移除所在页面立即更新，其它标签页和设备在下次加载时同步。旧后端、网络故障或尚未配置时保持无图标，主页和其它功能继续可用；网站图标编辑器仍可打开并说明 API 状态，但不可写入。

“页面图标”独立控制 HomeLab 标题左侧和宽屏左右下角的三个装饰图标，与 favicon 互不覆盖。两个编辑器复用 SVG 校验、草稿预览、保存和移除流程。页面图标通过 `GET/PATCH /api/page-icon` 管理，资源为 `/api/page-icon.svg?v=<SHA-256>`，私有状态独立保存在 `DashboardPageIcon.json`，不修改 `DashboardFavicon.json`。

未配置、移除自定义图标、API 不可用或 SVG 加载失败时，页面图标回退到版本管理中的 `assets/icons/homepage-badge.svg`。标题图标按大写字母高度和基线对齐，间距为 `0.22em`；两枚角落图标只在宽度至少 1440px 且横屏时显示，边距为 16px，默认宽度为 `2.65rem`。自定义 SVG 会按透明区域计算显示边界并等比例缩放，特别宽或高的图形会限制尺寸以免遮挡内容；SVG 母版不变。三个装饰不接收点击或焦点，不重复朗读。其它页面在刷新后读取共享的页面图标配置。

两枚角落图标同时播放缓慢悬浮和薄卡片式 3D 旋转：6 秒上下浮动一个来回、上浮 6px，16 秒绕竖直中轴转一圈，透视距离为 240px；背面自然显示镜像。右侧浮动和旋转分别错开 3 秒和 2 秒。弹窗打开时暂停，关闭后继续；系统开启“减少动态效果”时保持正面静止。标题图标只保留现有入场效果。

### 服务入口状态

每张动态或静态 `.service-card` 提供一个导航入口，`href` 与可选 `data-probe-url` 是服务端探测目标的唯一来源。页面标题、检测中数量和卡片入场动画序号均根据实际卡片集合自动生成；卡片保存后会作废旧请求、重新绑定当前集合并只发送一次新的同源批量请求。空 Services 频道不会发送非法的空目标请求。最终 HTTP 状态分类如下：

- 无探测错误的 2xx/3xx：仅显示绿灯，“服务端可达”文字在视觉上隐藏且不占布局空间，仍可由屏幕阅读器读取；
- 4xx：黄色“异常 · HTTP 状态码”；
- 5xx：红色“不可用 · HTTP 状态码”；
- DNS、TLS、超时或连接失败：红色“不可连接”；
- 状态 API 整体不可用或响应契约错误：灰色“状态未知”，不计为全部服务离线。

“检测中”“状态未知”及黄色、红色异常状态继续显示状态灯和文字；被拒绝的重定向仍显示红色失败文字。正常状态的绿灯保持在卡片副标题行右侧，底部数量统计保持不变。

CLIProxy 点击根地址，但使用 `data-probe-url` 指向 `/management.html/`。卡片即使检测失败也保持可点击。

该状态验证入口节点经 Zoraxy 到上游的服务端链路，不等同于登录、业务功能或应用内部健康。如果显示服务端可达但当前设备打不开，应检查当前设备的 DNS、Tailscale、路由、证书缓存和浏览器环境。

### 背景选择和同步

页面打开背景选择器时优先请求 `GET /api/background-files`。后端扫描 `backgrounds/` 第一层的 `.jpg`、`.jpeg`、`.png`、`.webp` 和 `.avif` 原图，为图片生成或复用 480×270、质量 75 的 WebP 缩略图；选择器只加载这些缩略图，用户选中某张图片时才预加载并应用对应原图。清单最多返回 512 项、最多检查 2048 个目录项，每次请求最多新生成或修复 16 张缩略图；默认背景、当前背景和横竖屏已选背景优先保留在结果中。目录必须包含 `bg.jpg`，原图继续平铺在第一层；后端管理的 `backgrounds/thumbnails/` 只保存可再生缓存。缩略图缓存名使用 24 位文件名分组标识和 32 位源版本标识，将名称、大小和纳秒修改时间映射为固定 72 字符的安全文件名，避免深层 Windows 路径触及传统长度限制；旧版 64+64 位缓存会在完整清单扫描或删除对应壁纸时自动清理并按需重建。为保留旧后端降级能力，`backgrounds/` 仍不能包含会阻止目录列表的 index 文件。

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
    "failed": 0,
    "deferred": 0
  },
  "total": 21,
  "truncated": false
}
```

`total` 在完整扫描时是合法原图总数；目录超过扫描上限时为 `null`。`truncated` 表示当前只展示前 512 项或扫描未完成；`deferred` 表示本次因生成预算而延后的缩略图，下次打开会继续处理。前端对 API JSON 和目录列表降级响应都设置 512 KiB 硬上限。清单接口不可用、超时、超限或响应非法时，页面会显示“已进入原图加载降级模式”，再尝试 `/backgrounds/` directory listing；该降级响应同样受 512 KiB 和 512 项上限约束。单张缩略图生成或加载失败只让对应卡片回退到原图，不影响其它壁纸。

右键背景卡片时浏览器打开或保存的是 WebP 缩略图；需要原文件时，使用操作栏中的“查看原图”，页面会在新标签页打开当前横屏或竖屏标签已保存的同源原图，再通过浏览器右键保存。该入口不依赖壁纸 API 在线状态，也不会改变当前设置或发送偏好更新请求；如果原图已被外部删除，新标签页会显示静态资源的正常 404。

选择器按“普通壁纸”、“Bing 每日一图”和“Bestdori 抽卡”分成三个可折叠栏目，三个栏目首次打开都展开，折叠状态只在当前页面会话中保留；状态文案同时显示总数及各栏目数量。

分类只发生在浏览器界面中，原图仍平铺保存在 `backgrounds/` 第一层；`thumbnails/` 子目录只承载后端缓存，不参与分类。Bing 图片通过 `bing_cn_<YYYYMMDD>_<OHR..._ZH-CN...>.jpg`、`bing_row_..._ROW...jpg` 或 `bing_us_..._EN-US...jpg` 的前缀与市场标记配对识别；抽卡壁纸则通过角色、三星/四星/五星、标题、数字卡面 ID、`after_training`/`normal` 及可选重名序号识别。`bg.jpg` 和其它名称都归入普通壁纸；手动重命名导致结构失配时也会回到普通壁纸栏目。分类不读取额外状态文件。

选择图片后，前端发送现有请求：

```http
PATCH /api/background-preferences
Origin: https://home.example.com
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

本地 `homelab-background-state-cache-v1` 仅用于首次绘制和 API 故障降级。背景偏好与清单 API 不传输图片，只同步文件名和缓存地址；缩略图及选中的原图由 Zoraxy 静态服务传输。其它设备刷新后读取共享设置，没有实时推送；新原图只在用户主动使用上传、Bing 每日一图或抽卡 API 时写入。

弹窗中的“上传图片”允许 Windows、Android 和 iOS 文件选择器一次选择一张或多张 JPEG、PNG、WebP 或 AVIF，并按选择顺序逐张将原始字节发送到现有单文件状态服务接口。单个文件最多 50 MiB、解码后最多 100MP，背景目录最多保存 512 张合法原图；服务端校验真实图片格式和扩展名，不转码。重名文件自动使用 `名称 (2).png`、`名称 (3).png` 等可用名称，不会覆盖现有图片或 `bg.jpg`；串行队列保证重名编号稳定，并避免并发争用目录配额和缩略图生成资源。

选择文件后页面自动打开上传进度窗口，显示整体进度、已处理数量和每张图片的等待、上传、服务端验证、成功、跳过、失败或取消状态。不支持的扩展名、空文件和超过 50 MiB 的文件会在客户端预检时跳过；单张图片无效或单项上传失败不会阻塞后续文件。用户可显式停止当前及剩余队列；服务无法连接、请求超时、认证或代理/API 不兼容以及 512 张配额已满也会停止剩余项目。全部成功时窗口显示最终汇总约 1.2 秒后自动返回选择器；存在跳过、失败或取消时继续保留结果汇总，并允许重试网络、超时、临时服务端错误或取消的有效文件。

每批队列结束后页面只刷新一次背景清单。返回选择器时会展开相关栏目、按上传成功顺序滚动并聚焦第一张，同时为本批全部成功图片显示绿色发光描边、“新增”角标和一次轻微摆动；描边与角标保留到关闭选择器，系统启用减少动态效果时只保留静态提示。上传不会自动切换当前横屏或竖屏背景。服务端仍为每张成功图片同步尝试生成缩略图，生成失败不会撤销已经保存的原图；配额已满或目录扫描不完整时继续返回 409。批量能力没有新增后端 API，也没有改变配置、状态文件或 Zoraxy Virtual Directory 契约。

```http
POST /api/background-files/upload?name=wallpaper.png
Origin: https://home.example.com
X-Requested-With: homelab-dashboard
Content-Type: application/octet-stream

<原始图片字节>
```

“删除当前背景”始终作用于当前编辑方向中带蓝框勾选的图片，并在操作前显示二次确认。`bg.jpg` 是不可删除的永久兜底；如果被删图片同时被横屏和竖屏槽位引用，两个槽位都会回退到 `bg.jpg`。后端首先使用 Windows 回收站；若回收站不可用且文件仍存在，接口返回一个有效期 60 秒、仅限该文件且只能使用一次的确认令牌，前端只有在用户再次确认永久删除后才会携带令牌重试。

```http
POST /api/background-files/remove
Origin: https://home.example.com
X-Requested-With: homelab-dashboard
Content-Type: application/json

{"background":"wallpaper.png"}
```

回收站失败后的永久删除请求增加 `confirmationToken` 字段。成功响应会说明删除方式并返回更新后的完整背景偏好；直接伪造永久删除参数不能绕过首次回收站尝试。

#### Bing 每日一图

操作栏中的“每日一图”位于“查看原图”和“我的回合，抽卡！”之间。点击一次后，本地状态服务分别读取 Bing 中国版当前首页图（`zh-CN`）和国际版当前首页图；国际版优先要求 ROW（`en-WW`），ROW 不可用、返回错误市场或图片无效时回退 US（`en-US`）。图片优先下载 UHD，只有该资源明确返回 404 时才回退 1920×1080；请求不携带 Bing 的 `rf=LaDigue...` 默认图参数。

中国版的元数据与图片显式直连，不读取系统代理环境变量；ROW 与 US 的元数据和图片只通过严格 v3 配置中的 `bingInternationalProxy` HTTP 代理建立 HTTPS CONNECT 隧道。ROW 内容或市场校验失败时，US 仍通过同一代理回退；代理本身无法连接或建立隧道时，international 立即失败且不会改走直连，已成功的中国版结果继续保留。

```http
POST /api/background-files/bing-daily
Origin: https://home.example.com
X-Requested-With: homelab-dashboard
Content-Type: application/json

{}
```

接口独立返回 CN 与 international 两项结果，因此某一市场失败不会撤销另一市场已保存的图片。文件名使用 Bing 元数据中的 `startdate` 和图片 ID，不使用服务器本地日期；同日期、同 ID 重复触发时复用已有文件，US 回退成功后视为当天国际槽位已满足。中国版与国际版即使字节相同也各自保留。图片沿用 50 MiB、100MP、512 张目录配额、真实格式校验和原子发布约束，历史图片只由用户手动删除。

操作完成后前端只刷新一次清单；新保存的 CN 与 international 图片会作为同一批次同时高亮，并按 CN、international 的结果顺序聚焦第一张。若两张都已存在，则只展开栏目并定位第一张已有图片，不显示“新增”提示。操作不会自动更改横屏或竖屏当前背景。功能不保存标题、版权文本或 Bing 元数据状态文件。旧版后端返回 400、404 或 405 的已知不兼容结果时，页面会提示先升级后端。

#### Bestdori 卡面抽取

“我的回合，抽卡！”会打开独立参数弹窗，提供两种模式：

- 角色模式：从固定角色 ID 1–40 中选择一个角色，再从勾选的 3、4、5 星范围内随机抽取；
- 全随机模式：不指定角色，只在勾选的 3、4、5 星范围内随机抽取。

至少要选择一个星级。服务端先过滤尚未记录的卡面，再随机选择；优先下载训练后卡面，资源不存在时回退到普通卡面。抽取结果经过与上传相同的图片大小、像素、格式和原子发布校验后写入 `backgrounds/`，文件名保留角色、星级、标题和卡面 ID。成功后页面只刷新列表，关闭抽卡窗口后展开“Bestdori 抽卡”栏目，并使用与批量上传相同的新增描边、角标、轻微摆动和键盘焦点提示新卡面；不会自动改变横屏或竖屏当前背景。符合条件但没有未抽取卡面时会明确提示去重池已耗尽。

如果只有角色名称目录暂时不可用，角色模式会因无法选择角色而停用，但全随机模式仍可继续抽取，并在文件名与记录中使用“角色 ID”作为名称兜底；卡片目录或图片资源不可用时则会明确报错且不写入文件或去重记录。

```http
POST /api/background-files/draw
Origin: https://home.example.com
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

`state-service/main.py` 是稳定的薄入口，实际实现按职责位于 `state-service/homelab_state/`。这种拆分只改变源码组织：开发命令、PyInstaller 的 `main.py` 单文件构建入口、生成的 `HomeLabWallpaperState.exe`、配置与状态格式以及 Dashboard API 均保持不变。服务使用标准库、Pillow 和 Send2Trash，提供：

- `GET /api/background-files`：扫描第一层原图、核对缩略图缓存并返回结构化清单；
- `GET /api/background-preferences`：读取并自愈状态；
- `PATCH /api/background-preferences`：更新横屏或竖屏槽位；
- `GET /api/cards`：读取卡片初始化状态、两个频道的数据和独立修订号；
- `POST /api/cards/initialize`：只在状态文件缺失时一次性原子导入静态 Services 与 Quick Links；
- `PATCH /api/cards`：携带预期修订号整批替换一个频道，冲突时拒绝静默覆盖；
- `GET /api/favicon`：读取是否已配置、更新时间和当前 SVG 的完整内容哈希；
- `PATCH /api/favicon`：上传、替换或以 `iconSvg: null` 移除全站 SVG favicon；
- `GET /api/favicon.svg?v=<完整 SHA-256>`：仅在哈希与当前配置一致时返回清洗后的 SVG 资源；
- `POST /api/background-files/upload?name=...`：流式接收、验证并保存一张图片；
- `POST /api/background-files/remove`：将图片移入回收站，必要时处理带短时令牌的永久删除；
- `POST /api/background-files/bing-daily`：获取、验证并分别保存 Bing 中国版与国际版当前首页图；
- `GET /api/bestdori/characters`：读取固定角色 ID 1–40 的显示名称；
- `POST /api/background-files/draw`：按角色/星级或全随机参数抽取、验证并保存一张未记录卡面；
- `POST /api/bestdori/history/reconcile`：移除文件缺失或哈希不匹配的失效去重记录，不删除图片；
- `POST /api/bestdori/history/reset`：在前端二次确认后清空全部去重记录，不删除图片；
- `POST /api/service-status`：按输入顺序批量返回入口 HTTP 状态、耗时和稳定错误码；
- `GET /healthz`：返回状态、服务标识、版本和 PID；
- `POST /_control/shutdown`：仅供本机启动器使用的令牌控制端点，不得由 Zoraxy 代理。

安全约束：

- 强制监听 `127.0.0.1`；
- HTTP/1.1 请求在路由前拒绝任何 `Transfer-Encoding`、重复/歧义 `Content-Length` 和不应携带的正文；正文必须按唯一长度完整读取后才允许复用连接；
- 每条连接读取空闲时间最多 30 秒，请求行、请求头和正文的总读取时间最多 300 秒；最多同时处理 16 条持久连接，满载时返回 503；
- 所有写入 API 校验精确 Origin 和请求标记；JSON 与上传接口分别校验 Content-Type、Content-Length 和正文上限；
- 卡片每频道最多 32 张，只接受 HTTPS 跳转；Services 的有效探测 URL 继续服从现有回环探测目标限制；
- 导入 SVG 在前后端分别执行标签、属性、命名空间、本地引用、元素数、深度和大小白名单校验；兼容输入中的简单根级 `<title>` 与行内 `style` 属性会被验证后丢弃，不信任浏览器缓存或客户端预检；
- favicon 复用同一 SVG 清洗器并由后端再次验证，私有状态损坏、版本不兼容、符号链接或非普通文件会稳定返回 503，不会自动重置；资源只接受完整匹配的内容哈希，并设置 `nosniff`、ETag 与 `private, max-age=31536000, immutable`；
- 上传最多 50 MiB / 100MP，只接收真实 JPEG、PNG、WebP、AVIF，并以同目录临时文件验证后原子发布；
- 缩略图执行 EXIF 方向修正、居中裁切和 WebP 编码，经同目录临时文件、磁盘刷新和原子替换写入；缓存目录必须是真实目录而非符号链接；
- Bing 元数据与图片只访问代码中固定的 `https://www.bing.com` 路径，拒绝重定向、其它主机/端口、额外查询参数、市场标记不匹配、异常响应类型与超限正文；中国版显式禁用代理，ROW/US 只使用配置的回环 HTTP 代理，TLS 仍端到端验证；两市场操作共享 90 秒总截止时间；
- Bestdori 目录和图片下载只访问代码中固定的 HTTPS API 与资源地址，不接受客户端提供远程 URL；下载图片沿用上传的大小、像素、格式和原子发布约束；
- 抽卡使用独立的非阻塞互斥锁；永久去重记录只在卡面验证并发布成功后原子写入，符合条件的未记录池耗尽时不会回退到重复卡面；
- 拒绝未知字段、路径穿越、不支持的扩展名、符号链接和不存在文件；
- `bg.jpg` 永久禁止删除；永久删除必须使用回收站失败后签发的 60 秒单次令牌；
- 探测只接受配置域后缀的单层 HTTPS 子域，禁止凭据、fragment、非 443 端口、IP、外部域和多层子域；
- TCP 强制连接回环 Zoraxy，但 HTTP Host、TLS SNI 和证书 hostname 校验继续使用目标域名；不使用系统 DNS、代理或跳过 TLS 校验；
- 最多 8 个全局并发、32 个目标和 5 次重定向；同一批次共享超时截止时间，每一跳重新执行目标校验；
- 状态更新使用进程内互斥、`fsync` 和同目录 `os.replace`；卡片初始化、按频道修订号比较和写入处于同一临界区；
- EXE、配置和数据目录必须位于公开 Web 根之外；
- Zoraxy 继续负责 HTTPS 和首页 Basic Auth。

### 公开仓库定制清单

克隆后至少完成以下修改和检查，再部署到自己的环境：

1. 修改 `index.html` 中全部 Services 与 Quick Links 地址、名称和探测目标；卡片数量和动画序号会自动生成。
2. 使用首次引导或严格 v4 配置设置自己的 `backgroundDir`、`allowedOrigins`、`probeDomainSuffix`、回环监听地址和探测超时。
3. 在 Zoraxy 中为自己的首页域配置同源 `/api/...` 转发，并只把 `index.html`、当前版本的 `assets/` 和 `backgrounds/` 放入公开 Web 根。
4. 按实际暴露范围配置 Basic Auth 或等价访问控制；Origin、请求头和 CORS 不是身份认证。
5. 确认 EXE、JSON 配置、状态、日志、去重记录、源代码和 `.git/` 均无法通过站点下载。
6. 替换或新增壁纸时自行核对授权；不要把私人、来源不明或仅允许个人使用的素材提交到公开仓库。

### 严格 v4 配置与 v3 兼容

状态服务接受完整的 v4 配置，也兼容完整的 v3 配置（笔记禁用）；v1、v2、缺失字段和未知字段仍拒绝启动。运行新版 `HomeLabWallpaperState.exe configure` 可保留已有值与控制令牌、写入 v4，并选择是否启用笔记。启动不会自动改写旧配置或合并数据目录：

```json
{
  "version": 4,
  "backgroundDir": "D:\\Zoraxy\\www\\html\\backgrounds",
  "allowedOrigins": ["https://home.example.com"],
  "listen": "127.0.0.1:8787",
  "stateDir": "HomeLabWallpaperState-data",
  "controlToken": "由首次引导生成的随机令牌",
  "bingInternationalProxy": "127.0.0.1:7890",
  "probeConnectAddress": "127.0.0.1:443",
  "probeDomainSuffix": "home.example.com",
  "probeTimeoutSeconds": 5,
  "notes": {
    "enabled": true,
    "primaryDir": "HomeLabNotes-data",
    "backupDir": "HomeLabNotes-backup"
  }
}
```

笔记相对路径以配置文件目录为基准，`backupDir` 可设为 `\\\\NAS\\share\\notes` 形式的 JSON 字符串，SMB 权限由运行后端的 Windows 账号提供，网页不保存 NAS 密码。两目录不得相同、相互包含或位于公开 Web Root；主目录不得使用网络驱动器。路径修改需重启，网页只读展示。未启用或旧后端时笔记入口提示不可用，不影响其他主页功能。

`bingInternationalProxy` 只接受无协议、无凭据、无路径的回环 IP 与 1–65535 端口，默认 `127.0.0.1:7890`；代理必须支持 HTTPS CONNECT。`probeConnectAddress` 只允许回环 IP，`probeTimeoutSeconds` 允许 1–5 秒。服务状态请求示例：

```http
POST /api/service-status
Origin: https://home.example.com
X-Requested-With: homelab-dashboard
Content-Type: application/json

{"targets":["https://anon.home.example.com/"]}
```

```json
{"results":[{"target":"https://anon.home.example.com/","status":200,"latencyMs":38,"error":null}]}
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

监视器每 60 秒显示 API、PID、背景槽位、Bing 国际代理、探测配置、目录和最近日志。按 `R` 立即刷新；按 `Q` 或关闭窗口只关闭监视器，后端继续运行；按 `X` 进入“大退”确认界面，只有精确输入大写 `Y` 并按 Enter 才会停止后端 API 并关闭监视器，其它输入会取消并返回默认界面。确认时如果 API 已经不可用，监视器会直接退出且不会操作未知进程；关闭请求被拒绝、超时或异常时则保留监视器并显示失败原因。连续三次检查失败时，监视器会自动尝试恢复一次。关闭监视器后不再守护，之后再次双击 EXE 即可检查并恢复。

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

引导会验证 `bg.jpg`、公开目录边界、Bing 国际版回环代理、回环探测入口、域名后缀和超时，并提供笔记启用与两条目录配置；最后询问是否在当前用户 `shell:startup` 中创建快捷方式。旧配置可通过向导升级到 v4，现有状态文件继续使用。

## 构建

当前开发环境使用 Python 3.14；依赖及其固定版本以 `requirements.txt` 为准。Bing 与 Bestdori HTTPS 请求使用 Python 标准库，不新增第三方依赖。构建依赖会打包进生产 EXE，部署机器仍不需要安装 Python 或虚拟环境。可以使用现有 Python，也可以创建仓库本地 `.venv`：

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
-> 健康检查、GET、PATCH、favicon 与页面图标上传/资源/移除持久化、真实 PNG 上传与缩略图清单、Bing 路由离线校验、离线抽卡历史维护、批量状态 API、控制关闭
-> SHA-256
-> dist/HomeLabWallpaperState.exe
```

构建脚本不会自动安装依赖。任一固定依赖或 Pillow 必需编解码器缺失时会给出明确安装命令。卡片初始化/修订冲突、favicon 与页面图标上传/资源读取/移除/重启持久化、图片上传、Bing 路由和抽卡历史维护冒烟测试只操作构建目录中的临时背景/状态文件并随构建临时目录清理；Bing 烟测使用非法空对象字段确认路由存在，不请求 Bing 或 Bestdori 公网，也不会向系统回收站写入测试文件。

公开发布的 EXE 必须与项目许可证和捆绑组件的完整许可证一起分发。构建完成后可生成带文件清单与 SHA-256 的 Windows x64 发布包：

```powershell
.\scripts\package-release.ps1 -Version V3.0.0
```

脚本读取 `dist/HomeLabWallpaperState.exe`，输出 `dist/release/zoraxy-homelab-dashboard-V3.0.0-windows-x64.zip` 和压缩包自身的 `.sha256` 文件。压缩包包含 `index.html`、完整的 12 个前端运行资源、占位背景、项目许可证、资产边界、第三方声明、完整依赖许可证和包内文件的 `SHA256SUMS.txt`。

## 部署和迁移

1. 记录当前提交、Zoraxy 版本、旧 EXE 哈希，并备份旧 EXE、JSON、状态、日志、`index.html`、完整 `assets/` 和 Zoraxy 配置；已经存在时单独备份 `HomeLabWallpaperState-data/DashboardCards.json`、`DashboardFavicon.json` 与 `DashboardPageIcon.json`。
2. 停止现有后端，将待部署 EXE 复制到非公开的 Zoraxy 程序目录。不要删除现有 `HomeLabWallpaperState.json` 或 `HomeLabWallpaperState-data/`；若配置仍为 v1/v2，按“严格 v3 配置”一节运行新版 `configure` 完成迁移。
3. 核对构建输出 SHA-256 后双击新版 EXE，确认 `/healthz` 的服务标识和接口版本与当前构建匹配；接口版本不匹配时启动器会拒绝复用旧进程。
4. 先验证卡片未初始化 GET、favicon 状态/哈希资源、壁纸 GET/PATCH、`GET /api/background-files`、上传/删除、Bing 路由、离线抽卡历史维护和 Homepage 同源 `/api/service-status`；确认代理运行时 international 可获取，代理停止时 CN 仍成功且 international 不直连。部署前端时完整覆盖 `assets/` 与配套的 `index.html`，逐个确认 CSS/JavaScript 返回 200 和正确的 `Content-Type`，再使用 `Ctrl+F5` 强制刷新。若前端先于后端上线，favicon 保持无图标，卡片与壁纸功能各自进入静态或缓存降级，不会向旧后端写入新状态。
5. 新部署第一次打开首页时，确认现有 11+5 张卡片一次性导入、`DashboardCards.json` 位于非公开状态目录，随后刷新不重复初始化；通过“网页设置”上传、替换并移除一次 SVG favicon，确认 `DashboardFavicon.json` 仍只位于非公开状态目录；再打开背景选择器等待已有原图生成缩略图，并检查批量状态请求、卡片编辑、壁纸同步、上传、Bing 中国版/国际版每日图、回收站删除和两种抽卡模式。
6. 最后删除各子服务 Host Rule 中旧的 CORS 响应头，并验证 AstrBot 插件 JS/CSS 和其它服务。

若回滚到旧前端，必须从目标 Git Release 同时恢复与其兼容的 EXE、JSON、`index.html`、完整 `assets/` 和相关 Host Rule 配置，随后强刷浏览器缓存。升级前应独立备份部署机 `backgrounds/` 第一层原图、`HomeLabWallpaperState-data/DashboardCards.json`、`DashboardFavicon.json`、`DashboardPageIcon.json` 和 `BestdoriCardDraws.json`；`backgrounds/thumbnails/` 是完全可再生的缓存，不需要备份，也不应作为用户数据恢复。旧版本即使不读取卡片、favicon 或去重记录，也不得删除它们。从 Windows 回收站恢复的图片需保持原文件名；下次打开背景选择器时会重新生成缩略图，随后可在网页中重新选择对应槽位。已经永久删除的原图只能从独立备份恢复。

### Zoraxy Virtual Directory

现有代理保持不变：

```text
MatchingPath： /api/
Domain：       127.0.0.1:8787/api/
```

代理仍只需保留 GET/PATCH/POST、请求体、`Content-Type`、唯一且明确的 `Content-Length`、`Origin` 和 `X-Requested-With`，禁止缓存，并复用首页 Basic Auth。后端不接受 `Transfer-Encoding`；若前端连接使用分块传输或 HTTP/2，Zoraxy 必须先缓冲正文并转成一个正确的 `Content-Length`。Zoraxy 的请求行/请求头/正文空闲超时不应超过 30 秒，完整请求读取期限不应超过 300 秒，上传正文上限应设为 50 MiB；后端满载返回的 503 不应自动无限重试。不要代理 `/_control/`，不要把 8787 暴露到局域网、Tailscale 或公网。

服务端探测不依赖本机 DNS，可在部署机上用等价命令诊断 Zoraxy 回环链路：

```powershell
curl.exe -sS -L --resolve "anon.home.example.com:443:127.0.0.1" --connect-timeout 5 --max-time 10 -o NUL "https://anon.home.example.com/"
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

验证包括：PowerShell 语法、前端首屏内联脚本、稳定路径的外部 CSS 与原生 ES 模块入口、逐模块语法和本地 import 依赖、现有 11+5 张静态卡片的稳定 ID/顺序/字段/SVG、一次性原子导入、服务器/缓存/静态三级来源、网页设置菜单键盘与焦点、favicon 草稿/保存/移除/link/旧后端降级、编辑草稿与必填阻塞、删除/保存确认、空频道、鼠标排序、动态频道重绑、陈旧状态探测隔离、SVG 白名单、双向频道状态与动效锁、宽窄屏与减少动态效果、壁纸批量上传、批量新增图片的展开/高亮/聚焦/清理、Bing 每日一图的固定源、CN 直连、国际代理、市场回退、幂等和部分失败契约、Python favicon/卡片/探测/API 测试、Git 运行态文件排除以及暂存/未暂存空白差异。验证会拒绝手写 `--card-index`；`-Build` 额外执行 PyInstaller 和真实 EXE 的 favicon 上传/资源读取/移除重启持久化、卡片初始化/重启读取、PATCH/409 冲突及既有 API 冒烟测试。

人工部署后还应检查：

- 双击时 API 缺失会启动后端，API 已运行时不会重复启动；
- 关闭监视器后 `/healthz` 仍正常；
- 按 `X` 后只有精确输入大写 `Y` 并回车才会停止 API 和关闭监视器；其它输入取消，停机失败时监视器保持运行；
- `shell:startup` 登录后显示监视器；
- 网页可以读取和更新横竖屏背景；
- Services / Quick Links 在桌面、平板和手机尺寸下均可双向切换；Quick Links 不触发额外状态请求，键盘焦点不会进入非活动面；
- 第一次加载新版时现有 11+5 张卡片完整保留且只初始化一次；两台设备同时首次打开不会产生半份配置或互相覆盖；
- Services 与 Quick Links 可以分别连续新增、修改、删除和保存；桌面鼠标排序可用，手机端隐藏排序但其它编辑可用；
- 导入合法的单色和彩色 SVG 能正确预览，常见的简单根级 `<title>` 与行内 `style` 包装信息不会误杀且不会保留，脚本、事件、外链、嵌入图片和 `<style>` 样式表会被阻止；
- “网页设置”可通过鼠标和完整键盘操作；上传 favicon A 后当前标签立即显示，替换为 B 不残留 A，刷新及新标签读取 B，移除后当前页和刷新后均保持无图标；
- 分别上传不同的网站图标和页面图标，保存、刷新及移除时互不覆盖；移除页面图标后恢复默认金边徽章，窄屏和竖屏隐藏两枚角落装饰；
- 另一设备先保存同频道时当前草稿收到修订冲突并保留，确认重新载入前不会静默丢失或覆盖数据；
- 系统启用减少动态效果后，频道切换应立即完成且保持可操作；
- 打开背景选择器时默认只请求 WebP 缩略图，选择一张后才新增对应原图请求；清单故障时会提示原图加载降级模式；
- Windows、Android 和 iOS 可以一次选择多张真实图片；队列逐张显示进度，非法文件被跳过、单项失败继续、全局错误停止，取消与重试可用；全成功窗口约 1.2 秒后自动返回，异常批次等待用户关闭，随后全部新增项同时高亮且第一张获得焦点；
- “每日一图”按钮位于“查看原图”和抽卡按钮之间；代理运行时首次点击应直连保存中国版、经代理保存 ROW（必要时 US）并同时高亮新增项，代理停止时中国版仍可成功且国际版提示检查代理、不得直连；再次点击只定位已存在图片，均只刷新一次且不自动切换当前背景；
- 删除非默认背景会二次确认、优先进入回收站并让所有引用槽位回退；`bg.jpg` 无法删除；
- 角色模式和全随机模式只接受 3–5 星，抽卡成功会以新增角标、描边、轻微摆动和键盘焦点提示图片，但不会自动切换背景；
- 重复卡面不会再次抽取；清理失效记录和清空全部记录不删除背景图片，清空前会二次确认；
- Bestdori 不可访问或去重记录损坏时，已有背景、上传/删除、入口探测和 `/healthz` 仍正常；
- 一个 2xx/3xx、4xx 和 5xx/网络失败入口分别显示绿、黄、红；
- 停止状态 API 时全部卡片显示“状态未知”，而不是误报全部离线；
- 删除旧的 CORS 响应头后 Homepage 状态正常，AstrBot 插件资源恢复；
- EXE、JSON、状态和日志不能通过公开站点下载。

## 维护原则

- 修改后先运行统一验证，不要跳过失败项。
- 已初始化部署的卡片只通过网页编辑和 `DashboardCards.json` 持久化；静态卡片仅作为新安装首次导入、旧后端和无 JavaScript 降级，版本升级不得覆盖用户状态。
- `backgrounds/bg.jpg` 作为兜底进入 Git；其它原图不进入 Git，应独立备份；`backgrounds/thumbnails/` 是可再生缓存，不备份、不手工管理。
- `BestdoriCardDraws.json` 位于非公开状态目录并应随背景图片一起备份；清理或重置记录不会清理对应图片。
- `DashboardCards.json` 位于非公开状态目录并应独立备份；旧版本前端或 EXE 不读取它时也不得删除或覆盖。
- `DashboardFavicon.json` 与 `DashboardPageIcon.json` 分别保存网站图标和页面图标，位于同一非公开状态目录并应独立备份；移除图标会保留 `iconSvg: null` 的状态，旧版本不得删除、覆盖或搬到公开 Web Root。
- 从回收站恢复的背景不会自动恢复横竖屏选择；永久删除没有应用内撤销能力。
- `state.json`、`DashboardCards.json`、`DashboardFavicon.json`、`DashboardPageIcon.json` 格式与前端 API 是兼容边界，迁移或升级不得随意改变。
- `index.html`、稳定路径的前端 `assets/`、状态服务和 Zoraxy 配置分层维护；服务状态 API 契约变化必须同步更新前端验证。
