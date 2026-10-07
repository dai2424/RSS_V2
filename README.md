# RSS v2

RSS 来源管理、采集、版本保存和英文内容中文化的模块化单体应用。

## 后端开发

```powershell
uv sync
uv run python -m rss_v2.main migrate
uv run uvicorn rss_v2.main:app --reload
uv run python -m rss_v2.main worker --once
uv run pytest
```

默认数据库位于运行目录。真实模型密钥使用 `RSS_LLM_KEY_<KEY_REF>` 环境变量提供，测试默认使用 fake provider。

## 前端开发

```powershell
cd frontend
npm install
npm run dev
```

