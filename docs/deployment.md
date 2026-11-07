# 部署指南

本指南用于首次部署、配置和升级。项目概览见 [README](../README.md)，日常操作和故障处理见 [维护手册](maintenance.md)。

## 准备环境

- Windows x64 主机和已配置 HTTPS 的 Zoraxy。
- 首页域名，以及通过同一 Zoraxy 访问的服务域名。
- 一份配套的前端文件和 `HomeLabWallpaperState.exe`；EXE 可按[构建说明](maintenance.md#构建与验证)生成，部署机器无需安装 Python。
- 使用 Bing 国际版每日一图时，准备支持 HTTPS CONNECT 的本机 HTTP 代理。
- 启用笔记时，准备本机主目录和另一条图片／备份目录，后者可以使用 NAS。

## 放置文件

公开网站目录只放 `index.html`、完整的 `assets/` 和 `backgrounds/`。首次部署复制仓库内的 `backgrounds/bg.jpg`；已有部署保留原有壁纸，更新前端时覆盖 `index.html` 和完整的 `assets/`。

EXE、配置、状态和笔记数据放在网站目录之外。例如：

```text
D:/Zoraxy/
├── HomeLabWallpaperState.exe
├── HomeLabWallpaperState.json
├── HomeLabWallpaperState-data/
├── HomeLabNotes-data/
├── HomeLabNotes-backup/
└── www/html/
    ├── index.html
    ├── assets/
    └── backgrounds/
        ├── bg.jpg
        └── thumbnails/
```

在 Zoraxy 中把首页静态根目录设为 `www/html/`。`backgrounds/` 的目录列表用于壁纸清单故障时的备用读取，保持该目录可列出图片。

## 首次配置

进入 EXE 所在目录，双击 EXE 或运行：

```powershell
.\HomeLabWallpaperState.exe configure
```

按实际部署填写以下内容：

| 项目 | 填写方式 |
| --- | --- |
| 壁纸目录 | 与网站 `/backgrounds/` 对应的目录，包含 `bg.jpg` |
| 首页 Origin | 首页的协议、域名及必要端口，例如 `https://home.example.com` |
| 监听地址 | 默认 `127.0.0.1:8787` |
| 状态目录 | 默认 EXE 旁的 `HomeLabWallpaperState-data` |
| Bing 国际代理 | 默认 `127.0.0.1:7890`，填写回环 IP 和端口 |
| Zoraxy 探测入口 | 默认 `127.0.0.1:443`，对应本机 Zoraxy 的 HTTPS 入口 |
| 探测域名后缀 | 服务共用的域名后缀，例如 `home.example.com` |
| 探测超时 | 1～5 秒，默认 5 秒 |
| 笔记 | 选择是否启用，并填写已准备好的主目录和图片／备份目录 |

完成向导后，可选择创建当前用户的登录启动快捷方式。已有配置重新运行 `configure` 时会保留原值供修改。

### 配置文件

向导将配置保存为 EXE 旁的 `HomeLabWallpaperState.json`。下面用于核对字段；实际填写时保留向导生成的 `controlToken`，按自己的路径和域名调整其余值。

```json
{
  "version": 4,
  "backgroundDir": "D:\\Zoraxy\\www\\html\\backgrounds",
  "allowedOrigins": ["https://home.example.com"],
  "listen": "127.0.0.1:8787",
  "stateDir": "HomeLabWallpaperState-data",
  "controlToken": "<保留向导生成的随机令牌>",
  "bingInternationalProxy": "127.0.0.1:7890",
  "probeConnectAddress": "127.0.0.1:443",
  "probeDomainSuffix": "home.example.com",
  "probeTimeoutSeconds": 5,
  "notes": {
    "enabled": true,
    "primaryDir": "HomeLabNotes-data",
    "backupDir": "HomeLabNotes-backup",
    "password": ""
  }
}
```

相对路径以配置文件所在目录为基准。服务接受完整 v4 配置，也兼容关闭笔记的完整 v3 配置；通过 `configure` 可更新配置格式。手动修改配置后重启后端生效。

### 笔记配置

启用前先创建两条存储根目录，或建立指向已有目录的链接。主目录的真实目标应位于本机磁盘；图片／备份目录可为本地目录或 NAS UNC 路径，例如 JSON 中的 `"\\\\NAS\\share\\notes"`。SMB 访问权限由运行后端的 Windows 账号提供。

两条目录应彼此独立，并与公开网站、壁纸、状态目录及配置文件分开放置。目录符号链接、Windows 目录联接和多跳入口均可使用；选择最终目标明确的目录，保持存储内部为普通文件和目录，避开卷挂载点及循环链接。

需要独立密码时，在 `notes` 对象内填写字符串字段 `password`，随后重启后端。缺失或空字符串表示关闭独立验证，密码按原值比较，最多 1024 个 UTF-8 字节。向导保留已有密码，密码由管理员直接编辑配置文件。

链接目标切换和备份恢复操作见[维护手册的笔记章节](maintenance.md#笔记)。

### Bing 国际代理

`bingInternationalProxy` 使用 `IP:端口` 格式，接受回环 IP 和 1～65535 端口，代理支持 HTTPS CONNECT。Bing 中国版直连；国际版经该代理获取 ROW 图片，必要时经同一代理回退 US。代理故障会使国际版获取失败，中国版结果可继续保存。

## Zoraxy 转发

在首页 Host Rule 中添加 Virtual Directory：

| 字段 | 值 |
| --- | --- |
| MatchingPath | `/api/` |
| Domain | `127.0.0.1:8787/api/` |

首页、静态图片和 API 使用一致的 HTTPS 与访问认证。将整个 `/api/` 转发给同一后端，笔记图片也由这一入口提供。后端端口保持回环访问，`/_control/` 留给本机启动器使用。

代理需要保留 GET、POST、PUT、PATCH、DELETE、请求体，以及 `Content-Type`、`Content-Length`、`Origin`、`X-Requested-With`、`X-Notes-Token` 和 `X-Icon-Format-Version` 请求头；API 关闭缓存。

上传正文上限设为 50 MiB。后端接收带明确 `Content-Length` 的请求；分块传输或 HTTP/2 正文需由 Zoraxy 缓冲后转发。请求读取空闲超时最多 30 秒、总期限最多 300 秒。

服务探测通过本机 Zoraxy 的 HTTPS 入口访问配置后缀下的单层子域，例如 `app.home.example.com`。确认相应 Host Rule 和证书已配置，证书覆盖服务域名。

## 启动与检查

双击 EXE 会启动或复用匹配版本的后台 API，并打开监视器。配置过登录启动后，当前用户登录时会打开监视器。

1. 在部署机访问 `http://127.0.0.1:8787/healthz`，确认健康响应和服务版本。
2. 从首页域名打开页面，确认样式、搜索和服务卡片正常；首次连接会导入静态页面中的默认卡片。
3. 修改一张卡片，刷新页面确认保存；在背景选择器中选择壁纸，检查横竖屏设置。
4. 启用笔记后创建并保存一篇笔记，在笔记设置中检查两条存储路径和备份状态。

监视器中的 `R` 刷新状态；`Q` 或关闭窗口结束监视器，后台 API 继续运行。`X` 后输入大写 `Y` 并回车，会停止后台 API 和监视器。常用终端命令如下：

```powershell
.\HomeLabWallpaperState.exe monitor
.\HomeLabWallpaperState.exe restart
.\HomeLabWallpaperState.exe stop
```

端口被其他程序或旧版本服务占用时，先确认并停止占用进程，再启动新版。监视器运行期间会在连续检查失败后尝试一次恢复。

## 升级与回滚

1. 记录当前 Git 版本与 EXE 哈希，备份现有 EXE、配置、完整状态和笔记目录、壁纸原图、前端文件及 Zoraxy 配置。备份范围见[数据与备份](maintenance.md#数据与备份)。
2. 停止旧后端，替换 EXE，保留已有配置和用户数据。从公共 V3.0.0 升级时，先运行 `HomeLabWallpaperState.exe configure` 将 v2 配置写为 v4，核对向导给出的旧路径、Origin、探测设置和新增国际版代理。向导沿用原有控制令牌，笔记可按需启用。
3. 启动新版，检查 `/healthz` 的服务身份和接口版本为 15，再确认已保存的卡片和壁纸偏好仍可读取。
4. 覆盖配套的 `index.html` 和完整 `assets/`，保留已有壁纸。使用 `Ctrl+F5` 刷新，检查卡片、图标、壁纸和笔记。

回滚时恢复同一版本配套的 EXE、配置、前端及 Zoraxy 配置；涉及状态格式变化时，使用升级前的数据备份。已保存的用户卡片由状态文件管理，前端升级只更新静态默认卡片。

## 卸载启动注册

在仓库目录运行：

```powershell
.\scripts\uninstall-state-service.ps1 -ExePath 'D:\Zoraxy\HomeLabWallpaperState.exe'
```

脚本停止后端、删除当前用户 Startup 快捷方式，并在管理员权限下清理遗留计划任务。EXE、配置、数据、日志和壁纸保留，后续按需要归档或删除。
