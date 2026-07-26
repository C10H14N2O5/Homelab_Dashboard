# 业务实现审计记录

审计结论：核心业务链路实现正确，未发现会阻断本次 CORS 重构或服务状态探测的严重问题。审计时工作区干净，仓库统一验证通过，32 个 Python 测试全部成功。

## 审计发现-已修复与 2026-07-25

### P2：实际 EXE 目录未被正确检查

- 文件：`state-service/main.py`
- 位置：`run_configuration_wizard()`，原审计位置约第 1133 行。
- 现象：`app_dir` 来自 `config_path.parent`，检查的是配置文件目录，并非实际 EXE 所在目录。
- 影响：使用 `--config` 指向 Web 根之外的配置时，即使 EXE 位于公开 Web 根目录，也可能绕过“EXE 必须位于公开 Web 根之外”的约束。
- 建议：改用 `application_directory()` 获取实际程序目录，并在配置向导以及普通 `serve`、`monitor` 等启动路径加载配置后统一执行检查。

### P2：健康检查没有验证服务版本

- 文件：`state-service/main.py`
- 位置：`health()`，原审计位置约第 914–918 行。
- 现象：`/healthz` 已返回 `version`，但启动器只验证 `status` 和 `service`。
- 影响：同名但接口契约版本不同的进程占用 8787 端口时，可能被 `ensure_backend()` 误认为可复用后端，造成新版后端不启动，随后出现 `/api/service-status` 缺失或响应契约不匹配。
- 建议：同时要求 `payload.get("version") == SERVICE_VERSION`，并增加版本不匹配时拒绝复用的回归测试。

### P3：EXE 不存在时卸载脚本会提前中断

- 文件：`scripts/uninstall-state-service.ps1`
- 位置：原审计位置约第 13–14 行。
- 现象：脚本在 `Test-Path` 之前调用 `Resolve-Path`；由于启用了 `$ErrorActionPreference = 'Stop'`，路径不存在会直接终止脚本。
- 影响：如果 EXE 已被手动删除，后续 Startup 快捷方式及遗留计划任务将无法继续清理。
- 建议：先调用 `Test-Path`，确认文件存在后再执行 `Resolve-Path`；EXE 缺失时仍继续完成其余卸载步骤。

### P3：README 文件树包含已删除文件

- 文件：`README.md`
- 位置：原审计位置约第 35 行。
- 现象：文件结构仍列出已经从仓库删除的 `index.txt`。
- 影响：文档与实际仓库结构不一致，可能误导后续部署或维护人员。
- 建议：从 README 文件树中删除对应条目。

## 已验证内容

执行了：

```powershell
.\scripts\verify-project.ps1
```

验证结果：

- PowerShell 语法检查通过；
- 前端 HTML 与 JavaScript 检查通过；
- 11 张服务卡片、单次批量请求、状态分类和 API 故障降级契约通过；
- Python 语法检查通过；
- 32 个 Python 单元测试全部通过；
- 探测目标限制、回环连接、Host、TLS SNI、证书校验、重定向和共享超时测试通过；
- API 输入校验、状态文件并发写入和自愈测试通过；
- Git whitespace 与运行文件排除规则检查通过。

本轮未运行：

```powershell
.\scripts\verify-project.ps1 -Build
```

因此没有重新生成或执行 PyInstaller 单文件 EXE 的完整打包冒烟测试。

## 2026-07-25 修复复核

本节保留上方原始审计内容作为历史快照，并记录当前工作树对四项发现的修复结果。

### 已修复

- P2“实际 EXE 目录未被正确检查”：新增统一的实际程序目录校验，配置向导和所有普通启动模式均拒绝程序目录位于公开 Web 根内。
- P2“健康检查没有验证服务版本”：启动器现在只接受 `status`、`service` 和 `version` 均完全匹配的后端；其它或不兼容监听器不会被复用。
- P3“EXE 不存在时卸载脚本会提前中断”：卸载脚本先检查原始路径，EXE 缺失时继续清理 Startup 快捷方式和遗留计划任务。
- P3“README 文件树包含已删除文件”：README 已删除不存在的 `index.txt` 条目，统一验证会阻止该条目再次出现。

### 验证结果

执行并通过：

```powershell
.\scripts\verify-project.ps1
.\scripts\verify-project.ps1 -Build
```

- PowerShell 语法、缺失 EXE 卸载行为、前端 HTML 与 JavaScript、Git 空白和运行文件排除检查通过。
- 47 个 Python 单元测试全部通过，覆盖程序目录边界、精确服务版本、接口契约和启动器拒绝不兼容监听器。
- PyInstaller Windows x64 单文件 EXE 构建成功，配置向导 UTF-8、健康检查、GET、PATCH、真实 PNG 上传、批量状态 API 和控制关闭冒烟测试通过。
- 构建产物为 `17,042,634` 字节，SHA-256 为 `02E7C811825FF83D602809E306DEB8662FDC9FB70697AC1959DA2F63BB463518`。

