# Zoraxy HomeLab Service Dashboard

这是一个由 Zoraxy 静态网页服务器托管的 HomeLab 服务导航页。前端是无外部依赖的单页；横屏与竖屏壁纸偏好由一个仅监听回环地址的 Python 状态服务跨设备同步。开发时直接运行 Python，生产环境通过 PyInstaller 分发单文件 `HomeLabWallpaperState.exe`，不需要安装 Python 或虚拟环境。

## 项目概览

| 项目 | 当前实现 |
| --- | --- |
| 页面入口 | `index.html` |
| 背景图片 | `backgrounds/` 自动发现，部署机器本地保存，不进入 Git |
| 服务入口 | 11 个 HomeLab 子域名 |
| 状态服务 | Python 标准库实现；生产为 Windows x64 单文件 EXE |
| API 地址 | `127.0.0.1:8787`，只能监听本机回环地址 |
| 运行角色 | 同一 EXE 提供隐藏 API 后端和可见终端监视器 |
| 自动启动 | 当前用户 `shell:startup` 快捷方式，登录后显示监视器 |
| 状态文件 | Zoraxy 程序目录下的 `HomeLabWallpaperState-data/state.json` |
| 前端依赖 | 无 CDN、无框架、内联 SVG |

## 文件结构

```text
Zoraxy_StaticWebSite/
├── backgrounds/.gitignore       # 阻止部署机图片进入 Git
├── state-service/
│   ├── main.py                  # API、状态存储、启动器、监视器和首次引导
│   └── test_main.py             # Python 单元测试
├── scripts/
│   ├── build-state-service.ps1  # 测试、PyInstaller 构建和 EXE 冒烟测试
│   ├── install-state-service.ps1# 可选的哈希验证与首次启动辅助
│   ├── uninstall-state-service.ps1
│   └── verify-project.ps1       # 全仓库验证入口
├── requirements.txt             # 仅包含构建依赖 PyInstaller
├── index.html                   # 实际网页，包含 HTML/CSS/JavaScript/SVG
├── index.txt                    # Zoraxy 原始示例，不参与运行
├── CHANGELOG.md
└── README.md
```

仓库不分发背景图片、构建出的 EXE、JSON 配置、状态或日志。公开 Web 根目录只应部署 `index.html` 和本地维护的 `backgrounds/`；不要复制 README、脚本、源码、EXE、配置、状态或 Git 文件。

## 运行架构

浏览器继续使用原有接口，Python 移植不要求修改 `index.html`：

```text
浏览器
├── GET /backgrounds/                     -> Zoraxy 静态目录列表
├── GET/PATCH /api/background-preferences -> Zoraxy Virtual Directory
│                                             -> 127.0.0.1:8787/api/...
└── GET 11 个服务子域名                  -> 入口状态探测
```

API 与 Zoraxy 没有进程级启动依赖。API 初始化只读取背景目录、配置和状态文件；Zoraxy 只在浏览器请求到达时反向代理。因此两个当前用户 Startup 入口可并行启动，不需要设置顺序。

## 前端行为

### 服务入口状态

11 张 `.service-card` 同时承担导航和 CORS 状态探测。最终 HTTP 状态分类如下：

- 2xx/3xx：绿色“可连接”；
- 4xx：黄色“异常 · HTTP 状态码”；
- 5xx：红色“不可用 · HTTP 状态码”；
- DNS、TLS、超时、网络或 CORS 失败：红色“不可连接”。

CLIProxy 点击根地址，但使用 `data-probe-url` 指向 `/management.html/`，绕开根路径 301 缺少 CORS 头的问题。卡片即使检测失败也保持可点击。

首页位于 `https://home.c10h14n2o5.com`。11 个 Zoraxy Host Rule 必须设置：

```text
Direction:    Client << Zoraxy（Upstream to Downstream）
Mode:         Add Header
Header Key:   Access-Control-Allow-Origin
Header Value: https://home.c10h14n2o5.com
```

该状态只说明入口响应，不等同于登录、业务功能或应用内部健康。

### 背景选择和同步

页面扫描 `/backgrounds/` 的 directory listing，只接收第一层 `.jpg`、`.jpeg`、`.png`、`.webp` 和 `.avif`。目录必须包含 `bg.jpg`，不能包含会阻止目录列表的 index 文件。

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

本地 `homelab-background-state-cache-v1` 仅用于首次绘制和 API 故障降级。API 不传输图片，只同步文件名；其它设备刷新后读取共享设置，没有实时推送。

## Python 状态服务

`state-service/main.py` 只使用标准库，提供：

- `GET /api/background-preferences`：读取并自愈状态；
- `PATCH /api/background-preferences`：更新横屏或竖屏槽位；
- `GET /healthz`：返回状态、服务标识、版本和 PID；
- `POST /_control/shutdown`：仅供本机启动器使用的令牌控制端点，不得由 Zoraxy 代理。

安全约束：

- 强制监听 `127.0.0.1`；
- PATCH 校验精确 Origin、请求标记、JSON Content-Type 和 4 KB 上限；
- 拒绝未知字段、路径穿越、不支持的扩展名、符号链接和不存在文件；
- 状态更新使用进程内互斥、`fsync` 和同目录 `os.replace`；
- EXE、配置和数据目录必须位于公开 Web 根之外；
- Zoraxy 继续负责 HTTPS 和首页 Basic Auth。

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
4. API 已运行时不创建第二个后端；
5. 进入可见监视器。

监视器每 60 秒显示 API、PID、背景槽位、目录和最近日志。按 `Q` 或关闭窗口只关闭监视器，后端继续运行；按 `R` 立即刷新。连续三次检查失败时，监视器会自动尝试恢复一次。关闭监视器后不再守护，之后再次双击 EXE即可检查并恢复。

首次引导默认将壁纸目录设为 `D:\Zoraxy\www\html\backgrounds`；配置和状态仍保存在 EXE 所在目录：

```text
<Zoraxy程序目录>/
├── HomeLabWallpaperState.exe
├── HomeLabWallpaperState.json
├── HomeLabWallpaperState-data/
│   ├── state.json
│   └── service.log

D:/Zoraxy/www/html/backgrounds/
```

引导会验证 `bg.jpg` 和公开目录边界；检测到旧的 `C:\ProgramData\HomeLabWallpaperState\state.json` 时可选择导入；最后询问是否在当前用户 `shell:startup` 中创建快捷方式。

## 构建

当前开发环境使用 Python 3.14。PyInstaller 是唯一的额外构建依赖，生产环境不需要它。可以使用现有 Python，也可以创建仓库本地 `.venv`：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\scripts\build-state-service.ps1
```

没有 `.venv` 时，构建脚本使用 PATH 中的 `python`。构建流程为：

```text
py_compile
-> unittest
-> PyInstaller --onefile --console --noupx
-> 临时端口 EXE 冒烟测试
-> 健康检查、GET、PATCH、控制关闭
-> SHA-256
-> dist/HomeLabWallpaperState.exe
```

构建脚本不会自动安装依赖。PyInstaller 缺失时会给出明确安装命令。

## 部署和迁移

1. 构建并记录输出 SHA-256。
2. 如果旧 Go SYSTEM 任务仍存在，以管理员身份运行：

   ```powershell
   .\scripts\uninstall-state-service.ps1
   ```

   该操作保留旧 EXE、状态和日志。
3. 将新 EXE 复制到 Zoraxy 程序目录，而不是公开的 `web/html`。
4. 可直接双击，或先校验哈希后启动：

   ```powershell
   .\scripts\install-state-service.ps1 `
     -ExePath '<Zoraxy程序目录>\HomeLabWallpaperState.exe' `
     -ExpectedSha256 '<构建输出SHA256>' `
     -PublicWebRoot '<Zoraxy程序目录>\web\html'
   ```

5. 完成首次引导并选择导入旧状态、创建 Startup 快捷方式。
6. 验证监视器、回环 API 和网页背景切换。

### Zoraxy Virtual Directory

现有代理保持不变：

```text
MatchingPath： /api/
Domain：       127.0.0.1:8787/api/
```

代理必须保留 GET/PATCH、请求体、`Content-Type`、`Origin` 和 `X-Requested-With`，禁止缓存，并复用首页 Basic Auth。不要代理 `/_control/`，不要把 8787 暴露到局域网、Tailscale 或公网。

### 卸载

```powershell
.\scripts\uninstall-state-service.ps1 `
  -ExePath '<Zoraxy程序目录>\HomeLabWallpaperState.exe'
```

脚本请求后端停止、删除当前用户 Startup 快捷方式，并在管理员权限下删除遗留 SYSTEM 任务。EXE、配置、状态、日志和背景默认保留。

## 验证

```powershell
.\scripts\verify-project.ps1
.\scripts\verify-project.ps1 -Build
```

验证包括：PowerShell 语法、前端两段脚本、11 张卡片、状态探测分类、超时行为、Python 语法与测试、Git 忽略规则以及维护契约。`-Build` 额外执行 PyInstaller 和真实 EXE 冒烟测试。

人工部署后还应检查：

- 双击时 API 缺失会启动后端，API 已运行时不会重复启动；
- 关闭监视器后 `/healthz` 仍正常；
- `shell:startup` 登录后显示监视器；
- 网页无需修改即可读取和更新横竖屏背景；
- 一个 2xx/3xx、4xx 和 5xx/网络失败入口分别显示绿、黄、红；
- EXE、JSON、状态和日志不能通过公开站点下载。

## 维护原则

- 修改后先运行统一验证，不要跳过失败项。
- 新增服务卡片时同步更新硬编码总数、动画序号、README 和前端验证契约。
- 背景文件不进入 Git，应独立备份。
- `state.json` 格式与前端 API 是兼容边界，迁移或升级不得随意改变。
- `index.html`、状态服务和 Zoraxy 配置分层维护；后端重构不应要求修改前端。
