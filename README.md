# Zoraxy HomeLab Service Dashboard

一个由 Zoraxy 托管的 HomeLab 服务导航页，将服务入口、快捷网站、搜索、壁纸和笔记集中在同一个页面中，适配桌面与手机。

前端使用 HTML、CSS 和原生 JavaScript 模块。Python 状态服务负责跨设备保存卡片、壁纸偏好与笔记，生产环境打包为单文件 `HomeLabWallpaperState.exe`。

> 这是面向 Zoraxy + Windows HomeLab 的公开参考实现。默认入口使用保留示例域名 `*.home.example.com`，请按部署文档替换为自己的域名、路径和认证策略。

## 主要功能

| 功能 | 可以做什么 |
| --- | --- |
| 服务导航 | 在 Services 与 Quick Links 间切换，通过网页编辑卡片、图标、链接和顺序 |
| 入口状态 | 批量检测服务经 Zoraxy 的可达情况，显示正常、异常或未知状态 |
| 联网搜索 | 使用 Bing、Google、DuckDuckGo、百度和哔哩哔哩搜索，支持键盘快捷操作 |
| 壁纸管理 | 分别设置横屏与竖屏背景，批量上传图片，获取 Bing 每日一图和 Bestdori 卡面 |
| Markdown 笔记 | 编辑多篇笔记、任务清单和图片，自动保存，恢复本机草稿，检查与恢复备份 |
| 页面个性化 | 独立设置网站图标与页面图标，支持常见图片格式和页面动图 |

卡片和壁纸偏好由服务器保存，其他设备刷新后读取。笔记使用独立存储目录，可设置共享密码，并将图片与文本备份放在本机或 NAS。

## 快速部署

### 开始前

准备一台 Windows x64 主机，并在 Zoraxy 中配置首页的 HTTPS 域名和静态网站目录。

- 首页和各服务的域名应有对应的 Zoraxy Host Rule 与有效证书。
- 使用 Bing 国际版每日一图时，准备支持 HTTPS CONNECT 的本机 HTTP 代理。
- 启用笔记时，先创建本机主目录和独立的图片／备份目录。

完整配置说明见[部署指南](docs/deployment.md)。

### 1. 准备 EXE

准备与前端配套的 `HomeLabWallpaperState.exe`。仓库中的 `dist/` 用于本地构建产物，可按[构建说明](docs/maintenance.md#构建与验证)生成 EXE。

将 EXE 放在网站目录之外，例如 `D:\Zoraxy\`。部署机器使用 EXE 运行服务，构建依赖只需安装在开发机器上。

### 2. 部署静态页面

将 `index.html`、完整的 `assets/` 和 `backgrounds/` 复制到 Zoraxy 的静态网站目录。首次部署使用仓库提供的兜底壁纸 `bg.jpg`：

```text
D:/Zoraxy/www/html/
├── index.html
├── assets/
└── backgrounds/
    └── bg.jpg
```

在 Zoraxy 中将首页静态根目录设为上面的 `html/`。更新已有部署时，保留原有壁纸，只覆盖配套的 `index.html` 和完整 `assets/`。

### 3. 配置状态服务

进入 EXE 所在目录，运行配置向导：

```powershell
.\HomeLabWallpaperState.exe configure
```

按实际环境填写路径与域名，常用配置如下：

| 配置项 | 示例或默认值 |
| --- | --- |
| 壁纸目录 | `D:\Zoraxy\www\html\backgrounds` |
| 首页 Origin | `https://home.example.com`，换成自己的首页地址 |
| 监听地址 | `127.0.0.1:8787` |
| 状态目录 | `HomeLabWallpaperState-data` |
| Zoraxy 探测入口 | `127.0.0.1:443` |
| 探测域名后缀 | `home.example.com`，与自己的服务域名对应 |

向导还会询问 Bing 国际代理、笔记目录及当前用户的登录启动选项。笔记目录与密码设置见[笔记配置](docs/deployment.md#笔记配置)。

配置与状态默认保存到 EXE 旁，和公开网站目录分开。

### 4. 转发同源 API

在首页 Host Rule 中添加 Virtual Directory：

| 字段 | 值 |
| --- | --- |
| MatchingPath | `/api/` |
| Domain | `127.0.0.1:8787/api/` |

首页的 HTTPS 与访问认证同时用于静态资源和 API。后端保持回环监听；请求头、上传大小等设置见[转发说明](docs/deployment.md#zoraxy-转发)。

### 5. 启动并检查

双击 EXE，后台 API 会启动，并打开终端监视器。在部署机访问 `http://127.0.0.1:8787/healthz` 检查服务，再通过首页域名打开页面。

- 确认页面样式、搜索和服务卡片正常。
- 修改一张卡片，刷新后确认保存；选择壁纸，检查横竖屏设置。
- 启用笔记后，保存一篇笔记，并在设置中检查存储路径和备份状态。

关闭监视器窗口后，后台 API 继续运行。启动管理、升级与回滚见[部署指南](docs/deployment.md#启动与检查)。

## 目录概览

```text
Zoraxy_StaticWebSite/
├── index.html            # 页面入口和静态默认卡片
├── assets/               # 样式、前端模块、图标与第三方资源
├── backgrounds/          # 壁纸原图和缩略图缓存
├── state-service/        # Python 状态服务与测试
├── scripts/              # 验证、预览、构建和卸载工具
├── docs/                 # 部署与维护手册
├── audits/               # 历次审计报告
├── requirements.txt      # Python 构建依赖
├── CHANGELOG.md          # 版本变更
└── TODO.md               # 任务与验收记录
```

详细组件职责见[组件与开发](docs/maintenance.md#组件与开发)。用户壁纸、运行状态和构建产物保存在部署机或本地工作目录中；仓库提供兜底壁纸与默认页面图标。

## 进一步阅读

| 文档 | 内容 |
| --- | --- |
| [部署指南](docs/deployment.md) | 首次安装、配置、Zoraxy 转发、启动管理、升级与卸载 |
| [使用与维护手册](docs/maintenance.md) | 日常操作、备份恢复、常见问题、构建和验证 |
| [Changelog](CHANGELOG.md) | 各版本的重要变更 |
| [TODO](TODO.md) | 功能计划与验收记录 |
| [审计索引](audits/README.md) | 历史审计的范围、基线和结果 |

## 公开仓库定制清单

1. 调整 Services 和 Quick Links 的名称、地址与探测目标。已初始化的实例通过网页卡片编辑器更新。
2. 运行配置向导，填写自己的公开 Web Root、域名、回环监听和探测设置。
3. 配置 Zoraxy 的同源 API 转发及 HTTPS 访问控制。Origin、CORS 和请求标记不能替代身份认证。
4. 网站目录只部署 HTML、运行资源和壁纸；后端、配置、状态、笔记数据及 Git 文件放在网站目录之外。
5. 替换壁纸和图标时确认再分发权限。

公开版搜索使用 `Alt+Backquote` 呼出，搜索框获得焦点后用 `Alt+J` 或 `Alt+L` 切换引擎。

## 许可与发行

项目代码和自有素材按 [MIT License](LICENSE) 发布。品牌标识和运行时下载的壁纸适用各自的权利边界，见 [ASSETS.md](ASSETS.md)。第三方组件及许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

公开版保留 GitHub 的既有历史，通过经过审阅的增量提交同步。作者邮箱使用 GitHub noreply，公私仓库的提交 SHA 分别维护。
