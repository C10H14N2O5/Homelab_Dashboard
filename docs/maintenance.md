# 使用与维护手册

首次安装、配置及升级见[部署指南](deployment.md)。本手册按日常操作、数据维护和开发验证组织。

## 日常使用

### 导航与卡片

Services 提供服务入口和状态检测，Quick Links 提供常用网站快捷入口。在当前频道打开“网页设置 → 修改卡片”，可新增、修改、删除并统一保存；桌面支持拖动排序，每频道最多 32 张。

卡片从静态页面首次导入后，以服务器保存的数据为准。多个设备修改同一频道发生冲突时，保留当前草稿，重新载入后再处理修改。后端离线时显示浏览器缓存或静态卡片，恢复服务后再保存。

服务状态的含义：

| 显示 | 含义 |
| --- | --- |
| 绿灯 | 经 Zoraxy 返回 HTTP 2xx／3xx |
| 黄色异常 | HTTP 4xx |
| 红色不可用或不可连接 | HTTP 5xx，或连接、TLS、超时等故障 |
| 灰色状态未知 | 状态 API 不可用或响应无效 |

检测反映部署机经 Zoraxy 到上游的入口链路。设备打不开绿灯入口时，继续检查该设备的 DNS、路由和证书。Services 卡片的探测地址需符合[部署指南中的域名配置](deployment.md#zoraxy-转发)。

### 搜索

搜索支持 Bing、Google、DuckDuckGo、百度和哔哩哔哩，每次打开页面默认使用 Bing，结果在新标签页打开。常用快捷键：

| 按键 | 操作 |
| --- | --- |
| Backquote（键盘左上角的键） | 呼出搜索并聚焦输入框 |
| Alt+J／Alt+L | 输入框中切换上一个／下一个引擎 |
| Esc | 关闭菜单或收起搜索 |

### 壁纸

横屏、竖屏各保存一张共享壁纸，其他设备刷新后读取。选择器按普通壁纸、Bing 每日一图和 Bestdori 抽卡分组；“查看原图”打开当前槽位的原文件，列表卡片加载缩略图。

上传支持多选 JPEG、PNG、WebP、AVIF，逐张显示进度，失败项可按提示重试。Bing 每日一图分别保存中国版与国际版；Bestdori 可按角色和 3～5 星范围抽取，使用独立去重记录。新增图片加入列表，选中后才成为当前背景。

删除优先进入 Windows 回收站，回收站失败时可再次确认永久删除。删除正在使用的图片后，相应槽位回到兜底 `bg.jpg`。从回收站恢复时保持原文件名，再到网页重新选择。

### 图标

“网页设置”分别管理网站图标（favicon）和页面内装饰图标，支持 SVG、PNG/APNG、JPEG、WebP、GIF、ICO。选择文件后预览，保存后写入共享配置；其他页面刷新后读取。

网站图标使用静态画面，页面图标保留动画与完整比例。弹窗打开或系统启用减少动态效果时，页面动图显示首帧。移除页面图标后恢复默认金边徽章。

### 笔记

笔记支持 Markdown、任务清单、表格、代码高亮，以及图片上传或粘贴。桌面显示列表和正文双栏，手机先显示列表；列表可拖动或使用 ↑／↓ 排序。已有笔记打开预览，新建笔记进入编辑。

使用“保存全部”或面板内 `Ctrl/Cmd+S` 保存修改。“网页设置 → 笔记设置”可调整自动保存间隔、切换时保存和失焦收起；默认每 60 秒保存、切换时保存、失焦时保持展开。关闭面板会等待上传和保存，失败时保留当前内容。

发生修订冲突时，可以另存为新笔记或载入服务器版本。本机 IndexedDB 保存应急草稿，重新进入时可选择恢复或丢弃；看到恢复提示后，确认内容并再次保存到服务器。主存储故障时笔记进入只读。

图片上传成功后插入正文。支持 JPEG、PNG/APNG、GIF、WebP、AVIF，保留原文件和动画；正文支持 HTTPS 外链图片。无已保存笔记引用的图片会及时清理，因此恢复旧草稿或旧备份时可能需要补充图片。

#### 密码与锁定

配置密码后，每个标签页独立验证。收起面板保留解锁；普通刷新支持一次性交接，复制或恢复标签页时重新验证。浏览器无法确认刷新类型时也会要求验证。后端重启后需重新验证。

“锁定”会等待上传并保存全部，成功后撤销当前标签页授权；保存失败或冲突时保留草稿。连续输错 5 次会暂停新的验证 30 秒。密码设置方式见[笔记配置](deployment.md#笔记配置)。

密码功能隔离网页访问。本机配置、笔记 JSON 和 IndexedDB 草稿仍以明文保存，文件保护由操作系统权限和管理员备份措施承担。

#### 备份检查与恢复

文字先保存到主目录，再同步最新版本到图片／备份目录。显示“备份待同步”表示主目录已保存，备份仍待完成；在设置中检查路径和错误，恢复访问后重试备份。后台备份在关闭网页后继续运行。

“检查／恢复备份”先展示差异，再逐项选择以主目录为准、从备份恢复或暂不处理，最后应用。检查后数据发生变化时重新检查。正常删除的笔记有删除标记，恢复时按展示的差异选择。

单篇文件、顺序或设置损坏时，可在只读状态下检查备份并选择恢复。`collection.json` 或 `sync.json` 损坏／丢失时，先停止服务并保存两处完整目录及绑定记录，由管理员修复元数据；需要从备份重建时，另存并移走 `NotesStorageBindings.json`，配置一个已创建的本机空主目录，重启后手动检查原备份并恢复。

#### 更换链接目标

笔记设置显示“入口 → 真实目标”。主目录链接可重指到完整的同一笔记集合，后端校验后热切换；切换前准备完整数据，并确认目标未被另一实例使用。

图片／备份链接重指后，会显示 `backup_target_changed`，图片和常规备份操作暂停，文字仍可保存。进入“检查／恢复备份”，核对差异后“应用并重新绑定”；即使两处内容一致，也通过该操作确认新目标。配置字符串变更则需要重启后端。

## 数据与备份

定期保存以下数据。制作完整文件备份时先停止后端，再复制目录，以便配置和状态对应同一时间点。

| 位置 | 保存内容 |
| --- | --- |
| EXE 旁的 `HomeLabWallpaperState.json` | 路径、域名、控制令牌和可选笔记密码 |
| `stateDir`（默认 `HomeLabWallpaperState-data/`） | 完整状态目录，含壁纸偏好 `state.json`、卡片 `DashboardCards.json`、图标 `DashboardFavicon.json`／`DashboardPageIcon.json`、抽卡记录 `BestdoriCardDraws.json` 和笔记绑定 `NotesStorageBindings.json` |
| `backgroundDir` | 第一层壁纸原图；`thumbnails/` 可重新生成 |
| 笔记主目录 | 正文、顺序、设置、集合与同步记录 |
| 笔记图片／备份目录 | 附件和最新文本备份 |

笔记自动备份保存最新文本，历史版本需要另行备份。Bestdori 的“清理失效记录”和“清空全部记录”只维护去重记录，图片单独管理。

### 常用容量限制

| 内容 | 限制 |
| --- | --- |
| 壁纸 | 最多 512 张，单张 50 MiB、100MP |
| 笔记文字 | 最多 500 篇，标题 200 字符，单篇正文 256 KiB，文本合计 32 MiB |
| 笔记图片 | 最多 2,000 张，单张 20 MiB，合计 5 GiB |
| SVG 图标 | 输入最多 64 KiB |
| 位图图标 | 输入最多 5 MiB、单边 4096px；动画最多 200 帧，保存后最长边不超过 512px |

## 常见问题

| 现象 | 检查与处理 |
| --- | --- |
| 页面样式或功能缺失 | 检查完整 `assets/` 已部署，CSS／JS 返回 200 和正确类型；用 `Ctrl+F5` 刷新 |
| 全部服务状态未知、编辑无法保存 | 在部署机检查 `/healthz`，再检查 Zoraxy `/api/` 转发、认证和请求头 |
| 写入被拒绝 | 核对 `allowedOrigins` 与实际首页 Origin，以及代理传递的请求体和请求头 |
| 端口冲突或版本不匹配 | 确认占用者，停止旧后端后启动配套 EXE；升级步骤见部署指南 |
| Bing 中国版成功、国际版失败 | 检查配置的本机 HTTP 代理及 HTTPS CONNECT 支持 |
| 壁纸目录改到 NAS 后页面图片 404 | 核对 `/backgrounds/` 静态映射与 `backgroundDir` 指向同一处原图；检查部署账号的 SMB 权限 |
| 壁纸列表进入原图加载模式 | 检查清单 API 和 `backgrounds/` 目录列表，选图功能通过原图继续工作 |
| 笔记不可用或只读 | 在笔记设置中检查入口、真实目标与错误，确认目录存在、权限和数据完整；按笔记章节恢复 |
| 文字已保存、备份待同步 | 检查 NAS、账号权限和备份路径；修复后重试备份 |
| 笔记图片无法读取 | 先验证笔记密码，检查备份目录及重绑定状态，再检查正文引用 |

启动和异常记录位于 `stateDir/service.log`，日志自动轮转。服务探测的回环链路可在部署机诊断，将示例域名换成自己的服务域名：

```powershell
curl.exe -sS -L --resolve "app.home.example.com:443:127.0.0.1" --connect-timeout 5 --max-time 10 -o NUL "https://app.home.example.com/"
```

## 组件与开发

浏览器经 Zoraxy 加载静态页面，并通过同源 `/api/` 访问 Python 状态服务。壁纸原图和缩略图由 Zoraxy 提供；笔记附件和自定义图标由后端提供。

| 位置 | 职责 |
| --- | --- |
| `index.html`、`assets/dashboard.css` | 页面结构、首屏引导和样式 |
| `assets/js/app.js` | 功能初始化，以及卡片加载后协调频道和状态检测 |
| `assets/js/` | 背景、上传、Bing、Bestdori、搜索、频道、卡片和图标控制器；`notes-*` 负责笔记请求、授权、草稿、渲染和交互 |
| `assets/vendor/` | 随站点部署的 Markdown 渲染、高亮和清洗资源，含许可证及来源记录 |
| `state-service/main.py` | 源码运行与 PyInstaller 的统一入口 |
| `state-service/homelab_state/` | 配置、存储、HTTP 路由、服务探测和启动管理；`notes*` 负责笔记、授权、路径、绑定及 NAS 工作进程，`icon_images.py` 处理图标图片 |
| `state-service/tests/`、`scripts/` | 后端测试、前端检查、浏览器回归、预览与构建 |

### 构建与验证

在仓库根目录准备 Python，当前开发环境使用 Python 3.14，依赖固定版本见 `requirements.txt`：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\scripts\build-state-service.ps1
```

构建脚本执行 Python 测试、依赖及图片编解码检查、PyInstaller 打包和隔离 EXE 冒烟测试，成功后生成 `dist/HomeLabWallpaperState.exe` 并输出 SHA-256。依赖安装由上面的 pip 命令完成。

修改后运行统一验证；前端检查需要 Node.js：

```powershell
.\scripts\verify-project.ps1
```

验证覆盖文档、PowerShell、前端模块与行为、Python 测试、Git 忽略规则和差异空白。`-Build` 追加 EXE 构建；`-SkipPython` 或 `-SkipFrontend` 用于环境受限时的部分检查。部署前使用完整验证及配套构建结果。

公开版从干净检出的提交运行完整验证和构建，再执行 `./scripts/package-release.ps1 -Version V3.5.0`。脚本核对本地 vendored 依赖的 SHA-256，输出 Windows x64 ZIP、包内 `SHA256SUMS.txt` 和 ZIP 的独立 `.sha256` 文件。发行包中的 EXE 与前端资源应来自同一提交。

### 本地预览与浏览器回归

笔记完整联调使用隔离实例：

```powershell
.\.venv\Scripts\python.exe scripts/preview-notes.py
```

默认页面为 `http://127.0.0.1:8765/`，测试后端使用 8788 端口，数据保存在 `output/notes-preview/`。结束预览会停止专用后端；可用 `--empty` 跳过示例，或用 `--port`、`--api-port`、`--data-root` 指定独立实例。

具备 Node.js、Playwright 和 Edge 后，对运行中的预览执行 `node scripts/notes-e2e.mjs`。动效、切换、链接目录及图标另有对应的 `*-e2e.mjs` 脚本，按本次修改选择回归范围。

密码验证使用独立实例，将测试密码原文保存在忽略目录内的 UTF-8 文件，保留原值且不加末尾换行：

```powershell
.\.venv\Scripts\python.exe scripts/preview-notes.py --port 8766 --api-port 8789 --data-root output/notes-auth-preview --password-file output/notes-auth-preview/password.txt
node scripts/notes-auth-e2e.mjs http://127.0.0.1:8766/ output/notes-auth-preview/password.txt
```

浏览器会话恢复和真实手机交互需在目标浏览器验证。只检查静态页面时可用 `python -m http.server 8765 --bind 127.0.0.1`，同源 API 联调用上面的隔离预览。

## 文档维护

README 保持项目介绍和最短部署流程；操作细节归入本手册或部署指南。新增功能先更新用户能使用的行为，再补充必要配置和维护步骤。变更历史进入 [Changelog](../CHANGELOG.md)，正式审计按[审计索引](../audits/README.md)归档。
