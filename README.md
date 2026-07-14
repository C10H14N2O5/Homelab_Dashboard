# Zoraxy HomeLab Service Dashboard

这是一个由 Zoraxy 静态网页服务器托管的 HomeLab 服务导航页。页面主体仍是无外部依赖的单页；横屏与竖屏壁纸的跨设备偏好由一个仅监听本机回环地址的轻量 Go 状态服务保存，不需要用户数据库。

本 README 主要面向 Codex 和其他自动化 Agent，同时尽量使用非技术人员也能理解的语言。维护者应先阅读本文，再修改页面或执行部署。

## 项目概览

| 项目 | 当前实现 |
| --- | --- |
| 页面入口 | `index.html` |
| 背景图片 | `backgrounds/` 目录自动发现，资源由部署机器本地保存，不进入 Git |
| 壁纸同步 | 服务器保存横屏、竖屏两个共享槽位；其它设备刷新后同步 |
| 状态服务 | Windows x64 单文件 EXE，仅监听 `127.0.0.1:8787` |
| 服务数量 | 11 个 |
| 图标 | 内联 SVG，无外部依赖 |
| 桌面布局 | 背景主视觉 + 底部居中毛玻璃网格 |
| 移动布局 | 背景头图 + 深色服务列表 |
| 入场动画 | 每个标签页会话完整播放一次 |
| 状态检测 | 浏览器通过 CORS 读取入口最终 HTTP 状态，分为可连接、响应异常、不可用三档 |
| 部署方式 | Agent 克隆已合并仓库，再复制运行文件到用户指定的 Zoraxy 目录 |

## 文件结构

```text
Zoraxy_StaticWebSite/
├── .gitignore      # 忽略构建产物、运行状态和误放的日志
├── backgrounds/    # 部署机器本地保存的公开背景目录
│   ├── .gitignore  # 阻止目录内资源进入 Git
│   └── bg.jpg      # 本地默认背景；被 Git 忽略，不随仓库分发
├── state-service/  # Go 状态服务源码与测试
├── scripts/        # Windows 验证、构建、安装和卸载脚本
├── dist/           # 本地 EXE 构建目录，被 Git 忽略
├── index.html      # 实际运行的页面，包含 HTML、CSS、JavaScript 和 SVG
├── CHANGELOG.md    # 面向维护者的变更记录
├── README.md       # 项目结构、维护流程和 Agent 操作规范
└── index.txt       # Zoraxy 原始示例页面，不参与当前网站运行
```

仓库分发页面、状态服务源码和运维脚本，但不分发背景图片、构建出的 EXE 或部署机状态。更新网站时复制 `index.html`，并保留 Zoraxy 运行目录中已有的 `backgrounds/`；首次部署时需从独立的本地来源放入至少一张 `backgrounds/bg.jpg`。README、Changelog、`.git`、`.gitignore`、`scripts/`、`state-service/` 和 `index.txt` 不应复制到公开 Web 根目录。Zoraxy 必须开启 directory listing，且 `backgrounds/` 中不能存在任何 index 文件。

## `index.html` 代码结构

虽然所有代码都在一个 HTML 文件中，但它可以按下面五个模块理解。

### 1. 启动与动画模式判断

`<head>` 中的第一个内联脚本在页面绘制前运行，用于判断：

- 用户是否启用了“减少动态效果”；
- 本次标签页会话是否已经播放过完整动画；
- 当前方向对应的最近一次有效共享壁纸缓存；
- 应使用完整动画 `intro-first`，还是快速淡入 `intro-skip`。

会话标记保存在：

```js
sessionStorage['homelab-intro-seen']
```

需要重新测试完整动画时，在浏览器控制台执行：

```js
sessionStorage.removeItem('homelab-intro-seen')
```

然后刷新页面。

### 2. CSS 视觉系统

CSS 位于 `<style>` 中，主要分为：

- `:root`：颜色、卡片背景、文字、状态颜色和内容宽度；
- `.background-layer`：当前背景图片、统一居中裁切及切换淡入；
- `.background-dialog`、`::backdrop`：背景选择器的毛玻璃弹窗、遮罩和开合过渡；
- `.background-mode-switcher`：横屏、竖屏共享槽位切换；
- `.shade-layer`：保护可读性的径向和底部渐变遮罩；
- `.hero`：HomeLab 标题区；
- `.dashboard`、`.service-grid`：服务网格；
- `.service-card`：单个服务卡片；
- `intro-*` 和 `@keyframes`：入场动画；
- `@media`：平板、手机和减少动态效果规则。

当前关键响应式断点：

| 断点 | 作用 |
| --- | --- |
| `980px` | 调整中等宽度屏幕的网格和页面高度 |
| `767px` | 切换为移动端头图与内容区分离布局 |
| `520px` | 服务卡片改为单列 |
| `prefers-reduced-motion` | 关闭缩放、位移和长动画 |

### 3. 页面内容

`<body>` 的结构如下：

```text
背景层 background-layer
└── 遮罩层 shade-layer
    └── 页面 page-shell
        ├── 标题区 hero
        └── 导航区 dashboard
            ├── 状态说明和刷新按钮
            ├── 服务网格 service-grid
            │   └── 11 个 service-card
            └── 检测结果和辅助技术状态摘要
```

背景、遮罩、内容分别使用明确的层级，修改 `z-index` 时必须保持背景 < 遮罩 < 内容，否则可能出现背景消失或遮罩盖住按钮的问题。

### 4. 服务卡片

每个服务入口都是一个 `<a class="service-card">`。链接本身既负责导航，也被状态脚本用作检测目标。

卡片必须保留以下信息：

- `href`：服务的完整 HTTPS 地址；
- 可选的 `data-probe-url`：仅在入口重定向响应无法提供 CORS 头时使用，状态检测请求该地址，但点击卡片仍使用 `href`；
- `target="_blank"`；
- `rel="noopener noreferrer"`；
- `--card-index`：从 0 开始的动画顺序；
- `aria-label`：供屏幕阅读器使用的名称和用途；
- 内联 SVG 图标；
- 服务名称、说明和固定的状态 DOM 结构。

### 5. 运行时 JavaScript

文件末尾脚本负责四件事：

1. 在背景加载完成或安全超时后启动入场动画；
2. 页面打开时检测所有服务，并响应“刷新状态”按钮；
3. 扫描 `backgrounds/` 的 Zoraxy 目录列表，生成背景选择器；
4. 读取或更新横屏、竖屏共享壁纸状态，并在设备方向变化时切换对应图片。

背景选择器打开时会以约 220ms 淡入、轻微上浮和缩放，关闭时以约 180ms 反向播放。关闭按钮、Esc 和点击遮罩均经过同一关闭流程；减少动态效果模式下直接显示和关闭。目录扫描约 5 秒超时，图片预加载约 8 秒超时；新扫描会取消旧扫描，避免失败请求永久锁住选择控件。

#### 服务入口状态检测与 CORS

检测使用 `GET`、`mode: cors`、`credentials: omit`、`redirect: follow`、`cache: no-store` 和 5 秒超时。脚本读取跟随重定向后的最终 HTTP 状态，不读取页面正文：

- `200–399`：绿色“可连接”；
- `400–499`：黄色“异常 · HTTP 404”等实际状态；
- `500–599`：红色“不可用 · HTTP 521”等实际状态；
- DNS、TLS、网络、超时、混合内容或 CORS 失败：红色“不可连接”。

首页位于 `https://home.c10h14n2o5.com`，服务位于其它子域；浏览器只有在响应带有匹配的 CORS 头时才能把状态码交给脚本。在 11 个服务对应的 Zoraxy Host Rule 中，依次打开 `Headers → Custom Headers`，添加同一条规则：

```text
Direction:    Client << Zoraxy（Upstream to Downstream）
Mode:         Add Header
Header Key:   Access-Control-Allow-Origin
Header Value: https://home.c10h14n2o5.com
```

需要配置的 Host Rule 为：`nas`、`qbit`、`pbh`、`route`、`speed`、`anon`、`git`、`jellyfin`、`zoraxy`、`cliproxy`、`kvm`，域名后缀均为 `.home.c10h14n2o5.com`。

配置约束：

- 必须使用上面的精确 Origin，不使用 `*`，也不添加 `Access-Control-Allow-Credentials`；
- 因为响应值固定，不需要添加 `Vary: Origin`；
- 11 个 Host Rule 应先全部完成配置，再部署使用 CORS 检测的新 `index.html`；
- CORS 请求的重定向链不能在首个 301/302 响应上缺少允许首页 Origin 的响应头，否则浏览器会在跟随前终止请求；如果跳往另一个 hostname，最终 hostname 也必须返回同一 CORS 头；成功完成的 `301 → 200` 会按最终 200 显示绿色，脚本不会尝试读取原始 301；
- CLIProxy 根路径的 301 响应不包含 CORS 头，因此其卡片通过 `data-probe-url` 直接检测 `https://cliproxy.home.c10h14n2o5.com/management.html/`；卡片点击目标仍保留为根地址；
- Zoraxy 自己生成的 521 故障页不经过正常上游响应头注入时，浏览器会因缺少 CORS 头拒绝请求，卡片安全地显示红色“不可连接”；如果某个 5xx 响应带有正确 CORS 头，则显示其实际状态码；
- 漏配响应头只会使对应卡片显示红色，不影响其它卡片，也不会禁用链接；
- 卡片即使显示不可连接也保持可点击；
- “可连接”只说明入口最终返回了 2xx 或 3xx，不等于应用内部功能、登录或业务健康检查通过。

## 常见维护操作

### 添加一个服务

在 `.service-grid` 内复制现有卡片，并按模板修改：

```html
<a href="https://service.example.com/"
   class="service-card"
   target="_blank"
   rel="noopener noreferrer"
   style="--card-index: 11"
   aria-label="打开 Service Name，服务用途">
    <span class="service-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24">
            <!-- 在这里放入使用 currentColor 描边的 SVG path -->
        </svg>
    </span>
    <span class="service-copy">
        <span class="service-name">Service Name</span>
        <span class="service-meta">
            <span class="service-desc">简短中文用途</span>
            <span class="service-state" data-state="checking">
                <span class="state-dot"></span>
                <span class="state-label">检测中</span>
            </span>
        </span>
    </span>
</a>
```

添加后必须完成以下同步修改：

1. 确保所有 `--card-index` 从 0 开始连续递增；
2. 更新标题下方硬编码的“家庭服务入口”数量；
3. 不要在 SVG 中使用外部图片、字体或脚本；
4. 更新 `CHANGELOG.md`；
5. 执行本文的验证清单。

状态检测的总数来自 DOM，会自动随卡片数量变化，不需要修改 JavaScript 常量。

### 删除或调整顺序

- 删除完整的 `<a class="service-card">...</a>` 块；
- 按期望顺序移动完整卡片块；
- 重新排列所有 `--card-index`；
- 更新页面显示的服务总数和 Changelog；
- 不要只删除图标、状态或内部某个 `span`。

### 添加或删除背景图

将图片直接放入 `backgrounds/`，无需修改 HTML。打开页面底部的“切换背景”按钮时，页面会重新请求目录列表并发现新文件。

该目录的 `.gitignore` 会忽略其中所有文件和子目录，仅保留规则文件本身。因此背景不会出现在普通 `git status`、提交或推送中，也不能依靠 Git 进行备份和恢复；请在部署机器上单独维护背景资源备份。

支持的格式：

- `.jpg`、`.jpeg`；
- `.png`；
- `.webp`；
- `.avif`。

目录只扫描第一层；子目录、SVG、GIF 和其它文件会被忽略。所有图片使用 `cover + center` 居中裁切；横屏槽建议使用横向构图，竖屏槽建议使用主体居中的竖向或通用构图。

维护要求：

- 保留 `backgrounds/bg.jpg` 作为默认背景；
- 不要在 `backgrounds/` 中放置 `index.html`，否则 Zoraxy 不会返回目录列表；
- 目录文件名会对导航页访问者可见，不得放置隐私图片、说明文档或认证资料；
- 删除用户已经选择的图片是安全的，页面下次加载时会清除旧记录并恢复默认背景；
- 替换同名图片后如果看到缓存旧图，可强制刷新浏览器；
- 大尺寸 PNG 会增加加载和缩略图流量，优先使用合理压缩的 JPG、WebP 或 AVIF。

服务器返回的最近一次有效状态会缓存在：

```js
localStorage['homelab-background-state-cache-v1']
```

该值只用于首次绘制和 API 暂时不可用时的降级，不是本机覆盖。服务器成功响应后会覆盖缓存并清除旧的 `homelab-background`。需要清除离线缓存时可执行：

```js
localStorage.removeItem('homelab-background-state-cache-v1')
```

然后刷新页面；只要状态服务可用，页面仍会重新取得服务器共享设置。

## 横屏与竖屏壁纸跨设备同步

系统共享 `landscape` 与 `portrait` 两个图片文件名。图片仍只保存在部署机器的 `backgrounds/` 中；API 不传输图片，Git 也不保存图片。

请求流程：浏览器从 `/backgrounds/` 发现候选图片，选择后向同源 `PATCH /api/background-preferences` 写入目标槽位；Zoraxy 应先应用首页已有的 Basic Auth，再将请求反向代理到回环状态服务。其它设备刷新时通过 GET 读取完整状态，并按当前视口方向选择图片。已经打开的页面不会接收实时推送。

以电脑把横屏背景从 `bg.jpg` 改为 `test.jpg` 为例：

1. 浏览器打开选择器，从 `/backgrounds/` 目录列表发现并预加载 `test.jpg`；
2. 浏览器向同源 `/api/background-preferences` 发送：

   ```json
   {"mode":"landscape","background":"test.jpg"}
   ```

3. Zoraxy 的 Virtual Directory 在完成首页 Basic Auth 后，将请求转发到 `127.0.0.1:8787`；
4. 状态服务确认 Origin、请求标记、文件名及物理文件存在，再写入 `state.json` 并返回完整状态：

   ```json
   {
     "version": 1,
     "landscape": "test.jpg",
     "portrait": "bg.jpg",
     "updatedAt": "2026-07-14T12:00:00Z"
   }
   ```

5. 另一台电脑刷新页面后执行 GET，读取同一状态并显示 `test.jpg`；手机竖屏仍读取 `portrait`，所以继续显示 `bg.jpg`。

API 不可用时，页面继续使用最近缓存或 `bg.jpg`，服务导航和链接不受影响。所选图片会在写入前先预加载；PATCH 中断时页面会立即通过 GET 对账。若立即读取仍是旧值，原 PATCH 仍可能稍后完成，因此页面会提示稍后刷新核对；只有 PATCH 与对账 GET 都失败时才标记同步服务离线。

### 构建 Windows 状态服务

构建要求为 Windows x64 和 Go 1.22 或更高版本。在仓库根目录运行：

```powershell
.\scripts\build-state-service.ps1
```

脚本会输出 Go 版本，依次执行 `go test -count=1 ./...` 和 `go vet ./...`，再以 `GOOS=windows`、`GOARCH=amd64`、`GOAMD64=v1`、`CGO_ENABLED=0` 构建并验证临时 EXE，成功后才替换 `dist\HomeLabWallpaperState.exe`，最后输出文件大小和 SHA-256。`dist/` 与所有 EXE 均被 Git 忽略。

### 安装到 Windows 11 Zoraxy 主机

安装脚本不会复制 EXE。先记录构建脚本输出的 SHA-256，再把产物手动复制到 `C:\Program Files\HomeLabWallpaperState\HomeLabWallpaperState.exe`，在目标机用 `Get-FileHash` 核对一致后，以管理员身份运行：

```powershell
.\scripts\install-state-service.ps1 `
  -ExePath 'C:\Program Files\HomeLabWallpaperState\HomeLabWallpaperState.exe' `
  -BackgroundDir '<Zoraxy-Web-Root>\backgrounds' `
  -ExpectedSha256 '<build-output-sha256>' `
  -AllowedOrigin 'https://home.c10h14n2o5.com' `
  -Listen '127.0.0.1:8787' `
  -StateDir 'C:\ProgramData\HomeLabWallpaperState'
```

`BackgroundDir` 必须传入真实路径且包含 `bg.jpg`，`ExpectedSha256` 必须使用构建脚本输出的 64 位 SHA-256。为防止普通用户替换 SYSTEM 任务执行文件，安装脚本强制 EXE 位于 `Program Files\HomeLabWallpaperState`，并在注册任务前核对哈希；`StateDir` 应保留在 `ProgramData`。脚本同时拒绝把 EXE 或状态目录放进首页所在的公开 Web 根目录，并强制监听 `127.0.0.1:<端口>`。覆盖旧定义前会等待旧任务停止；新任务启动后会连续两次检查任务状态与 `/healthz`，15 秒内未就绪则停止失败任务并报出任务结果和日志路径。状态位于 `C:\ProgramData\HomeLabWallpaperState\state.json`，日志位于同目录的 `service.log`。

先在物理机验证：

```powershell
Get-ScheduledTask -TaskName 'HomeLabWallpaperState'
Invoke-RestMethod 'http://127.0.0.1:8787/healthz'
Invoke-RestMethod 'http://127.0.0.1:8787/api/background-preferences'
```

### Zoraxy Virtual Directory

在首页所在 HTTPS Host Proxy 中增加 Virtual Directory。Zoraxy 的字段和界面会随版本变化，部署前先记录实际版本并备份配置；当前官方配置结构使用以下含义：

```text
MatchingPath： /api/
Domain：       127.0.0.1:8787/api/
RequireTLS：   false
```

官方配置示例见 [Add Routing Via Config File](https://github-wiki-see.page/m/tobychui/zoraxy/wiki/Add-Routing-Via-Config-File)。不要假定 UI 存在方法白名单字段；配置后应实际验证 GET 与 PATCH 都能原样通过。代理必须保留方法、请求体、`Content-Type`、`Origin` 和 `X-Requested-With`，禁止缓存，并复用首页 Basic Auth；确认 Basic Auth exception 没有排除 `/api/`。不要重定向浏览器到回环 HTTP 地址，也不要通过防火墙、局域网端口转发或 Tailscale 暴露 8787。

### 升级、卸载与状态备份

升级前先记录旧 EXE 哈希并导出任务定义：

```powershell
$backup = Join-Path $env:ProgramData ("HomeLabWallpaperState-backup-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $backup
Export-ScheduledTask -TaskName 'HomeLabWallpaperState' |
  Out-File (Join-Path $backup 'task.xml') -Encoding Unicode
Copy-Item 'C:\Program Files\HomeLabWallpaperState\HomeLabWallpaperState.exe' $backup
Copy-Item 'C:\ProgramData\HomeLabWallpaperState\state.json' $backup
Get-FileHash 'C:\Program Files\HomeLabWallpaperState\HomeLabWallpaperState.exe' |
  Out-File (Join-Path $backup 'exe-sha256.txt')
```

然后停止任务并等待实例退出：

```powershell
$taskName = 'HomeLabWallpaperState'
Stop-ScheduledTask -TaskName $taskName
$deadline = (Get-Date).AddSeconds(15)
do {
  $taskState = (Get-ScheduledTask -TaskName $taskName).State
  if ($taskState -ne 'Running') { break }
  Start-Sleep -Milliseconds 250
} while ((Get-Date) -lt $deadline)
if ($taskState -eq 'Running') { throw "Task $taskName did not stop within 15 seconds." }
```

确认实例不再运行后，手动替换并核对新 EXE 哈希，使用相同参数重新运行安装脚本；只有回环 API 验证成功后才部署新 `index.html`。若健康检查失败，不要反复覆盖。确认失败任务已停止后，可按备份恢复旧 EXE 和任务：

```powershell
Copy-Item (Join-Path $backup 'HomeLabWallpaperState.exe') `
  'C:\Program Files\HomeLabWallpaperState\HomeLabWallpaperState.exe' -Force
$taskXml = Get-Content -LiteralPath (Join-Path $backup 'task.xml') -Raw
Register-ScheduledTask -TaskName 'HomeLabWallpaperState' -Xml $taskXml -Force
Start-ScheduledTask -TaskName 'HomeLabWallpaperState'
```

`backgrounds/`、EXE、`state.json`、任务 XML 和 Zoraxy 配置都需要在 Git 之外单独备份。

同一个 `StateDir` 只能由一个服务实例使用。计划任务已设置为忽略自身的重复实例，但不要再手工启动第二份 EXE。Windows 计划任务停止可能直接终止进程，因此升级和关机前不能用“已执行优雅退出”替代状态备份。

状态写入会先同步临时文件再同目录替换，但 Windows 不保证该替换在强制终止时严格原子，当前版本也不会自动从损坏 JSON 恢复。壁纸偏好不是业务数据，但仍应备份 `state.json`；若启动日志报告状态解码失败，应先保留损坏文件用于排查，再恢复备份，或在用户确认后移走主文件让服务以两个 `bg.jpg` 槽位重新初始化。不要绕过安装脚本把 `StateDir` 指向任何公开目录。

管理员运行 `.\scripts\uninstall-state-service.ps1` 只会停止并注销计划任务，故意保留 EXE、状态和背景图片。除非用户明确授权，不得删除这些部署机本地数据。

### 调整配色和卡片

优先修改 `:root` 中的 CSS 变量，不要在每张卡片内写不同颜色。常用变量包括：

- `--page-bg`：页面深色底；
- `--panel`、`--panel-hover`：卡片默认和悬停背景；
- `--text`、`--muted`、`--subtle`：文字层级；
- `--accent`：图标和焦点高亮；
- `--online`、`--warning`、`--offline`、`--checking`：状态颜色。

### 调整动画

动画由 `background-settle`、`shade-settle`、`copy-reveal` 和 `card-reveal` 组成。

- 完整流程应控制在约 1.4 秒；
- 卡片延迟由 `--card-index` 计算；
- 优先动画 `transform` 和 `opacity`；
- 不要持续动画 4K 背景的 `filter`、`blur` 或 `background-size`；
- 修改后必须验证 `prefers-reduced-motion` 分支仍能立即显示内容；
- 不得创建无法跳过的欢迎页或要求点击后才能进入。

## 物理机 Agent 部署规范

部署目标路径与机器环境有关，仓库不保存固定路径。执行前用户必须提供或确认：

- 上游仓库 URL；
- 要部署的分支或提交 SHA；
- Zoraxy 静态网站目标目录；
- 已安装状态服务 EXE、预期 SHA-256、状态目录和 Zoraxy API 代理的实际配置；
- 11 个服务 Host Rule 的下游 CORS 响应头是否已经按本文配置；
- Zoraxy 运行账户是否有读取权限；
- 是否允许创建备份和替换现有文件。

Agent 不得猜测部署路径，也不得因为“上次路径可能相同”而跳过确认。

### 部署步骤

1. 使用专用部署工作副本；若工作副本已存在，先确认没有本地改动，再获取远端并检出用户指定的已合并提交；工作区不干净时必须停止；
2. 输出并记录实际部署的提交 SHA；
3. 检查工作副本干净，确认来源是上游已合并分支；
4. 比较目标目录中的现有 `index.html` 与仓库版本，并确认本地 `backgrounds/bg.jpg`、状态服务和 API 代理仍然存在；
5. 如果目标 `index.html` 包含仓库中没有的人工修改，停止并报告差异；
6. 在 Web 根目录之外创建带时间戳的备份，同时单独备份 `backgrounds/`、已安装 EXE、`state.json` 和计划任务参数；
7. 先复制到同一文件系统的临时位置，再替换目标文件，降低复制中断造成半部署的风险；
8. 首次启用同步时必须按“状态服务 → Zoraxy API 代理 → index.html”顺序部署；启用三档入口检测时必须按“11 个 Host Rule 的 CORS 响应头 → index.html”顺序部署；常规前端更新只替换 `index.html`，不得覆盖部署机器上的背景目录；
9. 检查文件存在、大小非零、Zoraxy 账户可读；
10. 访问实际导航页进行冒烟测试；
11. 向用户报告提交 SHA、源路径、目标路径、代码备份路径、背景备份路径、部署文件和验证结果。

命令中的路径必须作为参数传入，并使用适合当前操作系统的安全引用方式。不要把环境特定路径提交到 README 或脚本中。

### 部署验证

至少确认：

- 页面返回成功且标题显示为 HomeLab；
- `backgrounds/bg.jpg` 正常加载；
- `/backgrounds/` 返回目录列表，并且选择器能够发现图片；
- 回环 `/healthz`、同源 `/api/background-preferences` 均返回成功，API 受与首页相同的 Basic Auth 保护；
- 横屏和竖屏槽位可以分别保存，第二个浏览器刷新后获得共享选择；
- 11 个服务卡片全部出现；
- 11 个服务 Host Rule 的正常上游响应都包含精确的 `Access-Control-Allow-Origin: https://home.c10h14n2o5.com`；
- 桌面端和手机端没有明显横向溢出；
- 刷新状态按钮可用，汇总能分别统计可连接、响应异常和不可用；
- 至少验证一个 2xx/3xx 为绿色、一个 4xx 为黄色、一个 5xx 或网络失败为红色；停止一个测试后端时，Zoraxy 521 不得计入可连接数量；
- 至少抽查一个服务链接能在新标签页打开；
- 浏览器控制台没有阻止页面使用的 JavaScript 错误。

### 回滚

若部署后出现严重问题：

1. 停止继续覆盖或尝试未经验证的临时修复；
2. 恢复上一个版本的 `index.html`；
3. 如需回滚同步架构，禁用 API 代理并运行卸载脚本移除计划任务；
4. 保留 EXE、`state.json` 和整个 `backgrounds/`，除非用户明确要求删除；
5. 重新访问页面验证，并报告失败提交 SHA、错误现象、回滚内容和当前运行版本。

## 修改后的验证清单

### 自动检查

```powershell
.\scripts\verify-project.ps1
.\scripts\verify-project.ps1 -Build
```

验证脚本默认要求 Node.js 和 Go，检查 PowerShell 语法、内联 JavaScript、入口请求参数、HTTP 状态分类、超时行为、11 个服务卡片、Git 空白与忽略规则，并运行 `gofmt`、Go 单元测试和 `go vet`；只有 `-Build` 才生成 EXE。没有 Go 的机器只能运行静态子集：

```powershell
.\scripts\verify-project.ps1 -SkipGo
```

`-SkipGo` 不能作为发布构建的完整验证结果；发布前仍必须在带 Go 1.22+ 的 Windows x64 环境运行完整检查和构建。

如果 Node.js 也不可用，可再增加 `-SkipFrontend`，但这只验证 PowerShell、Git 和文档契约，不能证明页面脚本可解析。`-Build` 禁止与任何跳过参数组合。

提交前还应确认：

- 工作区中没有无关文件；
- 每个卡片链接均为预期地址；
- 每个新标签页链接都有 `noopener noreferrer`；
- 没有新增外部 CDN、令牌、密码、Cookie 或机器专用路径；
- README 与 Changelog 已同步。

### 人工检查

- 1920×1080 或常用桌面分辨率下检查背景主体与卡片遮挡；
- 约 390px 宽手机视图下检查头图、单列卡片和横向滚动；
- 首次打开验证完整动画，刷新验证快速淡入；
- 开启操作系统“减少动态效果”后确认内容立即可用；
- 点击“刷新状态”验证按钮忙碌状态和三档结果汇总；
- 临时制造 4xx、5xx 和连接失败，确认颜色、HTTP 文案和计数正确，且超时不会卡死整个页面；
- 临时停止一个测试后端，确认 Zoraxy 521 显示红色且不计入可连接数量；
- 使用两个独立浏览器配置验证横屏、竖屏共享设置在刷新后同步；
- 临时停止状态服务，确认缓存或 `bg.jpg` 降级且服务导航仍可使用。

## 常见问题

### 完整动画不再播放

这是会话级设计，不是故障。清除 `homelab-intro-seen` 的 `sessionStorage` 值后刷新。

### 所有服务都显示不可连接

先确认 11 个服务 Host Rule 都按本文设置了 `Client << Zoraxy` 的 `Access-Control-Allow-Origin`，且值与地址栏中的首页 Origin 完全一致。再打开浏览器开发者工具检查响应头和“入口检测失败”错误，依次排查旧版 `index.html`、DNS、TLS、HTTPS/HTTP 跳转、超时和浏览器安全策略。不要立即把前端状态改成固定绿色。

### 服务可以打开，但状态显示不可连接

顶层页面跳转不受 CORS 限制，跨子域 `fetch` 则必须收到正确响应头，因此“能点开”不能证明探测请求可读。检查该 Host Rule 的响应头方向和值；如果发生跨域重定向，还要检查最终 hostname。HTTPS 页面重定向或请求 HTTP 资源也会被混合内容策略拦截。状态只是提示，链接始终保持可用。

### 后端宕机后为什么不显示具体的 HTTP 521

Zoraxy 自己生成的故障页可能没有经过 Host Rule 的下游响应头注入，所以浏览器在把 521 状态交给脚本前就因 CORS 缺失而拒绝响应。此时页面显示红色“不可连接”而不是“不可用 · HTTP 521”，这是预期的安全降级；关键是它不再计入绿色可连接数量。

### 背景不显示

检查 `backgrounds/bg.jpg` 是否存在、目录层级和文件名大小写是否一致，以及 Zoraxy 是否有读取权限。如果默认背景正常但选择器只显示一项，确认 `backgrounds/` 已开启 directory listing 且目录中没有 index 文件。

### 共享壁纸 API 返回 401、403 或无法连接

401 表示 API 路径没有复用正确的 Basic Auth 会话。PATCH 返回 403 时，检查 `AllowedOrigin` 是否与页面来源完全一致，并确认 Zoraxy 保留 `Origin` 和 `X-Requested-With`。无法连接时依次检查计划任务、`service.log`、回环 `/healthz` 和路径代理优先级。不得把 Basic Auth 凭据写进 HTML。

### 新图标不显示

检查 SVG 是否有正确的 `viewBox`，路径是否放在 `<svg>` 内。现有样式使用 `stroke: currentColor`；只有填充路径的图标需要单独处理 `fill`。

### 页面在手机上横向滚动

优先检查新卡片中是否出现不可换行的长字符串、固定像素宽度或超长 URL 文本。不要通过隐藏整个页面的溢出来掩盖卡片尺寸错误。

## Agent 工作原则

1. 修改前完整阅读 `README.md`、`CHANGELOG.md` 和相关 `index.html` 模块；
2. 保持页面为无外部依赖的单页；状态服务只使用 Go 标准库，不得引入数据库或用户系统；
3. 不读取、提交或输出任何认证信息；
4. 小修改走分支和 PR，大型重构由用户本机人工主导；
5. 未得到用户明确的合并与部署确认，不执行物理机覆盖；
6. 部署路径、来源提交和备份策略必须明确，不能依赖猜测；
7. 保留无障碍、响应式、减少动态效果和失败降级能力；
8. 每次提交只包含确认过的文件，更新 Changelog，并报告验证结果。
