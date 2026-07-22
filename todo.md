# TODO：将 Homepage 可达性检测迁移到状态服务

> 状态：仓库代码已于 2026-07-22 按严格 v2 配置方案完成，待在生产环境部署。状态服务已从 Go 重构为 Python；下文保留实施前的原始设计背景，当前文件、命令和部署契约以 `README.md` 为准。

## 1. 背景与已确认结论

当前 Homepage 由外部物理机 A 经 Tailscale 访问物理机 B。物理机 B 同时运行 Zoraxy、Homepage 和现有 Go 状态服务；部分目标服务可能运行在物理机 C。

现有可达性检测由 A 的浏览器直接跨子域请求各服务，并依赖每个 Zoraxy Host Rule 注入：

```text
Access-Control-Allow-Origin: https://home.c10h14n2o5.com
```

AstrBot 插件 WebUI 使用不含 `allow-same-origin` 的 sandbox iframe，其资源请求 Origin 为 `null`。固定为 Homepage Origin 的 CORS 响应头会阻止插件 JS/CSS 加载，表现为资源返回 `200 OK`，但浏览器报 CORS 和 `net::ERR_FAILED 200 (OK)`。删除该响应头后插件页面恢复，但旧 Homepage 无法读取子服务状态码。

最终决定将精确 HTTP 状态检测迁移到物理机 B 上现有 Go API 进程：

```text
A 浏览器
  → https://home.../api/service-status
  → B 上的现有 HomeLabWallpaperState 进程
  → 127.0.0.1:443（保留目标 URL、Host 和 TLS SNI）
  → B 上的 Zoraxy Host Rule
  → B 或 C 上的目标服务
  → 状态结果经同源 API 返回 A
```

该设计验证两段链路：

1. A 能调用 Homepage 同源 API，证明 A → Tailscale → B 可用；
2. B 经 Zoraxy 探测目标，证明 B → Zoraxy → 目标服务可用。

在本项目“所有 Homepage 服务均统一由 B 上的 Zoraxy 暴露”的约束下，两段组合可作为综合可用性判断。若服务端探测正常但 A 无法打开某个子域，则故障域收敛到 A 的 DNS、Tailscale、路由、证书缓存或浏览器环境，而不是 Zoraxy 上游或目标服务降级。

已经在物理机 B 验证以下回环方式可用：

```powershell
curl.exe -sS -L `
  --resolve "anon.home.c10h14n2o5.com:443:127.0.0.1" `
  --connect-timeout 5 `
  --max-time 10 `
  -D - `
  -o NUL `
  "https://anon.home.c10h14n2o5.com/"
```

`--resolve` 只改变 TCP 连接地址；URL hostname、HTTP Host、TLS SNI 和证书校验仍使用 `anon.home...`，所以 Zoraxy 会命中真实 Host Rule 并继续访问实际的 AstrBot 上游。`curl -I` 返回 405 是因为它发送 HEAD，而 AstrBot 根路径只允许 GET；GET 测试返回 200，证明完整代理链路可用。

## 2. 业务文件作用

- `index.html`：实际运行的单页 Homepage，包含服务卡片、状态展示、壁纸选择和全部前端逻辑。卡片的 `href` 与可选 `data-probe-url` 是探测目标的唯一来源。
- `state-service/main.go`：现有 `HomeLabWallpaperState` 后台进程，仅监听 `127.0.0.1:8787`；目前提供壁纸偏好 API 和自身 `/healthz`，本次扩展为通用 Dashboard 状态服务。
- `state-service/main_test.go`：Go 配置、壁纸状态、API 校验、并发和健康检查测试；需要加入目标验证、回环探测和批量接口测试。
- `scripts/install-state-service.ps1`：校验 EXE、注册 SYSTEM 计划任务、组装启动参数并执行健康检查；需要增加探测入口和域名后缀参数。
- `scripts/build-state-service.ps1`：执行 Go 测试、vet 和 Windows amd64 构建，主要流程保持不变。
- `scripts/verify-project.ps1`：检查 HTML、内联 JavaScript、状态分类、PowerShell、Git 规则和 Go 工程；需要替换旧 CORS 探测契约测试。
- `README.md`：项目结构、卡片维护、Zoraxy Virtual Directory、构建部署和排障说明；需要全面替换旧 CORS 流程。
- `CHANGELOG.md`：记录本次架构迁移。
- `index.txt`：Zoraxy 原始示例页，不参与业务运行，不修改。

## 3. 已锁定的产品与兼容性决策

- API 整合进现有 Go 子进程，不新增守护进程。
- 对外使用一次批量请求；后端内部仍使用原子化、可独立测试的单目标 `probeOne()`。
- 前端实时从 `.service-card` 提取目标，后端不保存服务清单，不维护“域名 → 内网 IP”映射。
- 新增服务仍只需添加 Zoraxy Host Rule 和 Homepage 卡片。
- 保留 `HomeLabWallpaperState.exe`、计划任务名、安装目录和状态目录名称，避免额外迁移；文档将其职责描述为 Dashboard 状态服务。
- API 整体不可用时显示“状态未知”，不能把监控进程故障误报为所有目标离线。
- 保留现有三档 HTTP 分类；绿色文案改为更准确的“服务端可达”。
- 保留 CLIProxy 的 `data-probe-url` 机制。
- 子服务不再为 Homepage 添加 CORS 响应头；AstrBot 等上游恢复自身原始响应头。

## 4. API 契约

### 4.1 请求

```http
POST /api/service-status
Content-Type: application/json
Origin: https://home.c10h14n2o5.com
X-Requested-With: homelab-dashboard
```

```json
{
  "targets": [
    "https://anon.home.c10h14n2o5.com/",
    "https://cliproxy.home.c10h14n2o5.com/management.html/"
  ]
}
```

Zoraxy 继续通过现有 Virtual Directory 转发：

```text
MatchingPath: /api/
Domain:       127.0.0.1:8787/api/
RequireTLS:   false
```

Homepage 的现有 Basic Auth 必须继续覆盖 `/api/`。

### 4.2 响应

结果必须保持输入顺序。单个目标失败不导致整个接口失败；合法批次返回 HTTP 200：

```json
{
  "results": [
    {
      "target": "https://anon.home.c10h14n2o5.com/",
      "status": 200,
      "latencyMs": 38,
      "error": null
    },
    {
      "target": "https://example.home.c10h14n2o5.com/",
      "status": null,
      "latencyMs": 5000,
      "error": "timeout"
    }
  ]
}
```

稳定错误码至少包括：

- `invalid_target`
- `timeout`
- `tls_error`
- `redirect_rejected`
- `connection_error`
- `request_error`

存在任何 HTTP 响应时必须返回真实状态码，包括目标应用的 4xx/5xx 和 Zoraxy 生成的 521。只有未获得 HTTP 响应时才返回 `status: null`。

### 4.3 请求级错误

- 非 POST：405，并返回 `Allow: POST`；
- Origin 不允许或缺少 Dashboard 请求标记：403；
- 非 JSON：415；
- 请求体超过 16 KiB：413；
- JSON 结构错误、目标为空或超过 32 个：400；
- 单个目标不合法：作为该目标的 `invalid_target` 结果返回，不阻止其它合法目标执行。

## 5. 后端实现计划

### 5.1 配置

在 `state-service/main.go` 增加：

- `--probe-connect-address`：默认 `127.0.0.1:443`，只允许回环地址；
- `--probe-domain-suffix`：默认 `home.c10h14n2o5.com`；
- `--probe-timeout`：默认 `5s`。

启动时完成全部配置校验；非法配置直接拒绝启动。现有监听地址继续强制为 `127.0.0.1:<port>`。

### 5.2 目标安全校验

每个目标必须满足：

- scheme 为 `https`；
- 端口为空或为 443；
- 无用户名、密码和 fragment；
- hostname 是 `home.c10h14n2o5.com` 的单层直接子域；
- 禁止 IP、根域、外部域和多层子域；
- 重定向最多 5 次，每一步重新执行同样校验。

无论 URL hostname 是什么，底层连接都必须强制发送到唯一的回环 Zoraxy 地址，禁止普通 DNS 解析和系统代理。日志不得记录 query、凭据或响应正文。

### 5.3 HTTP 探测器

- 使用 Go 标准库 `http.Client`，不运行 curl 子进程，不增加第三方依赖。
- 单目标使用 GET；收到响应头后立即关闭正文。
- 自定义 `Transport.DialContext`，忽略解析出的目标地址并连接 `127.0.0.1:443`。
- URL hostname继续驱动 HTTP Host、TLS SNI和证书 hostname校验，禁止跳过 TLS 校验。
- 单目标总超时 5 秒，同时设置合理的连接、TLS握手和响应头超时。
- 单批最多 8 个并发探测，最多 32 个目标；并发完成后按请求原始顺序组装响应。
- `probeOne()` 独立于 HTTP handler，可注入 transport/clock 用于单元测试。

### 5.4 API handler

- 增加 `/api/service-status` 路由和 `handleServiceStatus()`。
- 将现有 Origin、`X-Requested-With`、Content-Type、正文限制和严格 JSON 解码提取为可复用 helper，避免与壁纸 PATCH 重复实现。
- API自身保留 `Cache-Control: no-store`、`Pragma: no-cache` 和 `X-Content-Type-Options: nosniff`。
- `/healthz` 不对外探测所有服务；它只验证进程、状态存储和探测客户端配置已成功初始化，避免安装脚本因某个上游离线而无法安装服务。

## 6. 前端替换计划

- `refreshStatuses()` 开始时把全部卡片设为 `checking`。
- 动态生成：

  ```js
  const targets = cards.map(card => card.dataset.probeUrl || card.href);
  ```

- 一次同源 POST `/api/service-status`，使用：
  - `Content-Type: application/json`
  - `X-Requested-With: homelab-dashboard`
  - `credentials: same-origin`
  - `cache: no-store`
  - 约 7–8 秒前端总超时
- 删除直接跨子域 `mode: cors` 请求和服务探测专用的响应体取消逻辑。
- 校验 API 返回的结果数量、顺序和 `target`；契约不一致视为 API 整体失败。
- 分类保持：
  - 200–399：`online`，显示“服务端可达”；
  - 400–499：`warning`，显示“异常 · HTTP n”；
  - 500–599：`offline`，显示“不可用 · HTTP n”；
  - `status: null`：`offline`，显示“不可连接”。
- 新增 `unknown` 状态样式。API 整体超时、返回非 2xx、JSON 不合法或契约不匹配时，所有卡片显示“状态未知”，汇总单独提示“状态检测服务不可用”，不计入目标离线数。
- 页面说明改为：状态表示入口节点经 Zoraxy 到上游的服务端链路；若显示正常但当前设备打不开，应检查本机 DNS、Tailscale、路由和浏览器环境。
- 卡片链接始终保持可点击。

## 7. 安装脚本与运维计划

`scripts/install-state-service.ps1` 增加对应参数并传入计划任务：

```powershell
-ProbeConnectAddress '127.0.0.1:443'
-ProbeDomainSuffix 'home.c10h14n2o5.com'
-ProbeTimeout '5s'
```

安装脚本必须：

- 校验探测地址是回环地址和合法端口；
- 校验域名后缀是无协议、端口、路径和通配符的规范域名；
- 校验 timeout 范围；
- 在成功输出中报告实际参数；
- 继续使用现有 EXE SHA-256、Program Files 路径、SYSTEM计划任务和 `/healthz` 连续健康检查流程。

## 8. 部署迁移顺序

生产环境必须按以下顺序迁移，避免旧前端和旧 CORS配置之间产生窗口故障：

1. 记录当前提交 SHA、Zoraxy版本、旧 EXE哈希和计划任务 XML。
2. 备份 `index.html`、EXE、`state.json`、计划任务和 Zoraxy配置。
3. 构建并安装新版 EXE，使用新增探测参数重新注册现有计划任务。
4. 验证：
   - `http://127.0.0.1:8787/healthz`
   - `http://127.0.0.1:8787/api/background-preferences`
   - Homepage同源 `/api/service-status`
5. 部署新版 `index.html`，确认 Network面板只出现一次同源批量状态请求。
6. 验证全部卡片状态和壁纸同步功能。
7. 最后删除 11 个子服务 Host Rule 中旧的 `Access-Control-Allow-Origin: https://home...`。
8. 验证 AstrBot插件 WebUI的 JS/CSS恢复加载，且其它服务不受影响。

回滚时恢复旧 EXE、任务参数和 `index.html`。如果回滚到旧前端，还必须同步恢复 11 个 Host Rule 的旧 CORS响应头。

## 9. 自动测试计划

### Go 测试

- 新配置默认值及合法覆盖；
- 非回环连接地址、非法域名后缀和非法 timeout 拒绝；
- POST方法、Origin、Dashboard标记、Content-Type、正文大小及严格 JSON校验；
- 0、1、11、32和33个目标；
- HTTP、非443端口、userinfo、fragment、IP、根域、外部域和多层子域拒绝；
- 结果顺序在并发完成顺序不同的情况下仍与输入一致；
- 部分目标失败不影响其它结果；
- 2xx、3xx、4xx、5xx及521状态原样返回；
- 连接拒绝、超时、TLS错误和拒绝重定向返回稳定错误码；
- 使用本地测试 TLS证书验证 TCP实际连接回环地址，同时 Host和TLS SNI保持目标域名；
- `/healthz` 不因普通上游离线而失败；
- 现有壁纸读取、写入、恢复和并发测试全部继续通过。

### 前端与验证脚本

- 保持11张卡片和现有链接安全属性检查；
- 验证一次刷新只发送一次批量 API 请求；
- 验证目标由卡片动态生成并优先使用 `data-probe-url`；
- 验证同源 credentials、请求标记、JSON正文和 no-store；
- 验证200、301、401、404、500、521及空状态分类和文案；
- 验证部分失败、结果顺序、契约异常和 API整体故障；
- 验证 API失败时刷新按钮恢复、卡片显示“状态未知”且不误计全部离线；
- 删除旧的 CORS Header和 `mode: cors` 契约检查；
- 更新 README维护契约，要求存在新 API、回环探测参数和 CORS移除说明。

### 完整命令

```powershell
.\scripts\verify-project.ps1
.\scripts\verify-project.ps1 -Build
```

当前工作环境静态前端和 PowerShell基线已通过：

```powershell
.\scripts\verify-project.ps1 -SkipGo
```

当前终端未安装 Go，因此完整验证在此处因找不到 Go 失败。生产开发环境必须安装 Go 1.22+，完整运行 `gofmt`、`go test -count=1 ./...`、`go vet ./...` 和 Windows amd64构建；`-SkipGo` 不能作为发布验收。

## 10. 人工验收矩阵

| 条件 | 预期结果 |
| --- | --- |
| 正常服务返回2xx/3xx | 绿色“服务端可达” |
| 服务要求认证或路径不存在 | 黄色并显示真实4xx |
| 停止一个上游 | 红色并显示 Zoraxy 5xx/521或“不可连接” |
| 单目标连接超时 | 仅该卡片红色，其它结果正常 |
| 停止状态 API | 全部卡片“状态未知”，不报告全部上游离线 |
| B 无法正常解析子域名 | 探测仍通过回环 Zoraxy完成，不依赖 DNS |
| 服务端探测正常但 A 打不开子域 | 指向 A 的 DNS/Tailscale/路由/浏览器故障域 |
| 删除子服务 CORS头 | Homepage状态仍正常，AstrBot插件页面恢复 |
| 新增一张服务卡片 | 无需修改 Go配置即可自动加入批量探测 |
| 壁纸读写与跨设备刷新 | 行为与改造前一致 |

## 11. 文档更新清单

- README项目概览：状态检测改为现有 Go API服务端批量探测。
- 文件结构：现有进程职责从壁纸服务扩展为 Dashboard状态服务。
- 卡片维护：`href`/`data-probe-url`继续作为唯一目标来源。
- 删除全部逐Host Rule添加 CORS头的说明、FAQ和部署前置条件。
- 增加回环 Zoraxy原理、`curl --resolve`诊断命令、安全限制和故障域矩阵。
- 更新构建、安装、升级、回滚和物理机 Agent部署顺序。
- 更新验证清单和常见故障。
- CHANGELOG新增2026-07-22条目，记录API、前端、部署与安全变化。

