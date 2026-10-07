# RSS v2

RSS 来源管理 → 独立 worker 采集 → 消息版本 → 英文内容中文化。当前阶段仅供本机使用，不支持局域网部署，也不导入 v1 数据。

## 首次运行

需要 Python 3.12、uv、Node.js 22 和 npm。在项目根目录执行：

```powershell
uv sync --locked
uv run python -m rss_v2.main migrate
Push-Location frontend
npm ci
npm run build
Pop-Location
uv run python -m rss_v2.main api
```

打开 <http://127.0.0.1:8000/sources>。另开终端启动 worker：

```powershell
uv run python -m rss_v2.main worker
```

只执行一个已排队任务：`uv run python -m rss_v2.main worker --once`。API 只创建任务；没有 worker 时，任务会保持排队。

操作顺序：创建行业分类 → 新增来源 → 测试来源 → 立即采集 → 查看消息 → 配置 Provider 与 Key 引用 → 生成中文。

## 模型与配置

Web 页只填写兼容 API 的 Base URL、模型名、非敏感请求头和 Key 引用名。例如引用名为 `MAIN`，需要在启动 **API 和 worker 的两个终端**分别设置：

```powershell
$env:RSS_LLM_KEY_MAIN = '<你的密钥>'
```

不要把真实密钥写入命令示例、仓库配置、请求头配置、截图或提交。引用名中的连字符转换为下划线，再统一转为大写匹配环境变量。可选会话头名称示例：`x-opencode-session`，每次调用自动生成会话 ID。

可复制 `config/settings.example.toml` 为被忽略的 `config/settings.toml`；也可用 `RSS_CONFIG` 指向仓库外的 TOML。配置优先级：显式参数 > `RSS_` 环境变量 > TOML > 默认值。常用变量：

| 变量 | 默认值 / 用途 |
| --- | --- |
| `RSS_RUNTIME_DIR` | `runtime`，数据库与构建产物根目录 |
| `RSS_DATABASE_PATH` | `<runtime>/db/rss_v2.db`，可单独覆盖 |
| `RSS_FRONTEND_DIST` | `<runtime>/frontend`；自定义时构建和 API 使用同一绝对路径 |
| `RSS_API_PORT` | `8000` |
| `RSS_WORKER_LEASE_SECONDS` | `300` |
| `RSS_LLM_RETRY_COOLDOWN_SECONDS` | `60` |

来源的时间偏移是对解析后的 UTC 发布时间追加分钟数，例如 `+60` 表示向后调整一小时；未知发布时间保持未知。Feed 原始更新时间作为来源元数据保留。

## 开发与检查

后端热更新：`uv run uvicorn rss_v2.main:app --host 127.0.0.1 --reload`。前端在 `frontend/` 执行 `npm run dev`，默认代理本机 8000 端口。

```powershell
uv run python -m rss_v2.main schema
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
Push-Location frontend
npm run generate:api
npm run typecheck
npm run test
npm run build
npm run test:e2e
Pop-Location
```

OpenAPI 导出不会创建数据库，`src/api/generated.ts` 不手工编辑。浏览器测试默认使用本机 Edge；也可执行 `npx playwright install chromium` 后设置 `PLAYWRIGHT_CHANNEL=chromium`。验收服务只使用固定 RSS 和模拟模型，自动化测试不调用真实模型。

## 备份与恢复

```powershell
uv run python -m rss_v2.main backup runtime/db/backup-20261007.db
```

使用 SQLite 在线备份 API，并检查完整性；目标已存在时拒绝覆盖。恢复时先停止 API 和 worker，再用 `RSS_DATABASE_PATH` 指向备份副本启动。不要覆盖仍在运行的数据库。

详细边界见 [实现说明](docs/阶段目标/1-RSS订阅_实现_AI.md)、[技术选型](docs/项目说明/技术选型.md)和[开发规范](docs/项目说明/开发规范.md)。
