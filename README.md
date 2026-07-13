# Zoraxy HomeLab Service Dashboard

这是一个由 Zoraxy 静态网页服务器托管的 HomeLab 服务导航页。项目采用单页结构，运行时只需要 `index.html` 和 `bg.jpg`，不需要 Node.js、构建工具、数据库或外部图标 CDN。

本 README 主要面向 Codex 和其他自动化 Agent，同时尽量使用非技术人员也能理解的语言。维护者应先阅读本文，再修改页面或执行部署。

## 项目概览

| 项目 | 当前实现 |
| --- | --- |
| 页面入口 | `index.html` |
| 背景图片 | `bg.jpg`，通过相对路径加载 |
| 服务数量 | 11 个 |
| 图标 | 内联 SVG，无外部依赖 |
| 桌面布局 | 背景主视觉 + 底部居中毛玻璃网格 |
| 移动布局 | 背景头图 + 深色服务列表 |
| 入场动画 | 每个标签页会话完整播放一次 |
| 状态检测 | 浏览器端入口可连接性检测，不是应用健康检查 |
| 部署方式 | Agent 克隆已合并仓库，再复制运行文件到用户指定的 Zoraxy 目录 |

## 文件结构

```text
Zoraxy_StaticWebSite/
├── index.html      # 实际运行的页面，包含 HTML、CSS、JavaScript 和 SVG
├── bg.jpg          # 页面背景图；文件名和相对位置不能随意改变
├── CHANGELOG.md    # 面向维护者的变更记录
├── README.md       # 项目结构、维护流程和 Agent 操作规范
└── index.txt       # Zoraxy 原始示例页面，不参与当前网站运行
```

部署网站时通常只需要复制 `index.html` 和 `bg.jpg`。README、Changelog、`.git` 目录和 `index.txt` 不应复制到公开 Web 根目录，除非用户明确要求。

## `index.html` 代码结构

虽然所有代码都在一个 HTML 文件中，但它可以按下面五个模块理解。

### 1. 启动与动画模式判断

`<head>` 中的第一个内联脚本在页面绘制前运行，用于判断：

- 用户是否启用了“减少动态效果”；
- 本次标签页会话是否已经播放过完整动画；
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
- `.background-layer`：背景图片及其位置；
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
- `target="_blank"`；
- `rel="noopener noreferrer"`；
- `--card-index`：从 0 开始的动画顺序；
- `aria-label`：供屏幕阅读器使用的名称和用途；
- 内联 SVG 图标；
- 服务名称、说明和固定的状态 DOM 结构。

### 5. 运行时 JavaScript

文件末尾脚本负责两件事：

1. 在背景加载完成或安全超时后启动入场动画；
2. 页面打开时检测所有服务，并响应“刷新状态”按钮。

检测使用 `HEAD`、`mode: no-cors`、手动重定向和 5 秒超时。由于跨域响应对浏览器不可读，脚本只能判断请求是否建立，不能读取实际 HTTP 状态码或应用内部状态。

因此：

- “可连接”可能是正常页面、登录页、重定向、404 或 405 响应；
- “不可连接”通常表示 DNS、TLS、网络、混合内容策略或超时失败；
- 卡片即使显示不可连接也保持可点击；
- 如需真正的健康状态，必须另行提供同源健康检查 API，不能只修改前端文案。

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

### 更换背景图

最安全的方式是保持文件名 `bg.jpg` 不变并直接替换图片。

更换后检查：

- 图片是否可以由浏览器直接读取；
- 文件名大小写是否完全一致；
- 桌面端人物或主体是否位于无遮挡区域；
- 手机端 `.background-layer` 的 `background-position` 是否仍合适；
- 图片大小是否会明显拖慢首次加载。

如需修改焦点，分别调整桌面和移动端的 `background-position`，不要通过改动 HTML 复制第二张背景图。

### 调整配色和卡片

优先修改 `:root` 中的 CSS 变量，不要在每张卡片内写不同颜色。常用变量包括：

- `--page-bg`：页面深色底；
- `--panel`、`--panel-hover`：卡片默认和悬停背景；
- `--text`、`--muted`、`--subtle`：文字层级；
- `--accent`：图标和焦点高亮；
- `--online`、`--offline`、`--checking`：状态颜色。

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
- Zoraxy 运行账户是否有读取权限；
- 是否允许创建备份和替换现有文件。

Agent 不得猜测部署路径，也不得因为“上次路径可能相同”而跳过确认。

### 部署步骤

1. 使用专用部署工作副本；若工作副本已存在，先确认没有本地改动，再获取远端并检出用户指定的已合并提交；工作区不干净时必须停止；
2. 输出并记录实际部署的提交 SHA；
3. 检查工作副本干净，确认来源是上游已合并分支；
4. 比较目标目录中的现有 `index.html`、`bg.jpg` 与仓库版本；
5. 如果目标目录包含仓库中没有的人工修改，停止并报告差异；
6. 在 Web 根目录之外创建带时间戳的备份；
7. 先复制到同一文件系统的临时位置，再替换目标文件，降低复制中断造成半部署的风险；
8. 只部署 `index.html` 和 `bg.jpg`；
9. 检查文件存在、大小非零、Zoraxy 账户可读；
10. 访问实际导航页进行冒烟测试；
11. 向用户报告提交 SHA、源路径、目标路径、备份路径、部署文件和验证结果。

命令中的路径必须作为参数传入，并使用适合当前操作系统的安全引用方式。不要把环境特定路径提交到 README 或脚本中。

### 部署验证

至少确认：

- 页面返回成功且标题显示为 HomeLab；
- `bg.jpg` 正常加载；
- 11 个服务卡片全部出现；
- 桌面端和手机端没有明显横向溢出；
- 刷新状态按钮可用；
- 至少抽查一个服务链接能在新标签页打开；
- 浏览器控制台没有阻止页面使用的 JavaScript 错误。

### 回滚

若部署后出现严重问题：

1. 停止继续覆盖或尝试未经验证的临时修复；
2. 使用本次部署前的备份恢复 `index.html` 和 `bg.jpg`；
3. 确认 Zoraxy 可以读取恢复文件；
4. 重新访问页面验证；
5. 报告失败提交 SHA、错误现象、回滚文件和当前运行版本。

## 修改后的验证清单

### 自动检查

```powershell
git diff --check
git status -sb
```

如果本机有 Node.js，可检查两个内联脚本能否解析：

```powershell
node -e "const fs=require('fs');const h=fs.readFileSync('index.html','utf8');const s=h.split('<script>').slice(1).map(x=>x.split('</script>')[0]);s.forEach(x=>new Function(x));console.log('Inline scripts parsed:',s.length)"
```

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
- 点击“刷新状态”验证按钮忙碌状态和结果汇总；
- 临时阻断一个地址时确认超时不会卡死整个页面。

## 常见问题

### 完整动画不再播放

这是会话级设计，不是故障。清除 `homelab-intro-seen` 的 `sessionStorage` 值后刷新。

### 所有服务都显示不可连接

依次检查浏览器网络、DNS、TLS 证书、HTTPS/HTTP 跳转和安全策略。不要立即把前端状态改成固定绿色。

### 服务可以打开，但状态显示不可连接

顶层页面跳转和跨域 `fetch` 的浏览器策略不同，特别是 HTTPS 跳转到 HTTP 时。状态只是提示，链接仍应保持可用。

### 背景不显示

检查 `bg.jpg` 是否和 `index.html` 位于同一部署目录，文件名大小写是否一致，以及 Zoraxy 是否有读取权限。

### 新图标不显示

检查 SVG 是否有正确的 `viewBox`，路径是否放在 `<svg>` 内。现有样式使用 `stroke: currentColor`；只有填充路径的图标需要单独处理 `fill`。

### 页面在手机上横向滚动

优先检查新卡片中是否出现不可换行的长字符串、固定像素宽度或超长 URL 文本。不要通过隐藏整个页面的溢出来掩盖卡片尺寸错误。

## Agent 工作原则

1. 修改前完整阅读 `README.md`、`CHANGELOG.md` 和相关 `index.html` 模块；
2. 保持项目为静态、无构建依赖的单页结构，除非用户明确批准架构变更；
3. 不读取、提交或输出任何认证信息；
4. 小修改走分支和 PR，大型重构由用户本机人工主导；
5. 未得到用户明确的合并与部署确认，不执行物理机覆盖；
6. 部署路径、来源提交和备份策略必须明确，不能依赖猜测；
7. 保留无障碍、响应式、减少动态效果和失败降级能力；
8. 每次提交只包含确认过的文件，更新 Changelog，并报告验证结果。
