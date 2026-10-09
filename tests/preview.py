"""浏览器验收专用本机服务；fixture 端点不会进入生产应用。"""

import json
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Request
from fastapi.responses import Response

from rss_v2.api.app import create_app
from rss_v2.api.static import FrontendFiles
from rss_v2.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
settings = Settings(runtime_dir=ROOT / "runtime/e2e", frontend_dist=ROOT / "runtime/no-dist")
app = create_app(settings)


@app.get("/fixtures/feed.xml")
def feed() -> Response:
    """仅提供固定 RSS，实际 HTTPX 适配器仍会访问网络边界。"""
    return Response(
        (ROOT / "tests/fixtures/sample_feed.xml").read_bytes(), media_type="application/xml"
    )


def _system_prompt(body: dict[str, Any]) -> str:
    """读取请求里的系统提示，用于区分翻译与内容加工两条链路。"""

    messages = body.get("messages")
    if not isinstance(messages, list):
        return ""
    return " ".join(
        str(item.get("content", ""))
        for item in messages
        if isinstance(item, dict) and item.get("role") == "system"
    )


@app.post("/fixtures/v1/chat/completions")
async def completion(request: Request) -> dict[str, object]:
    """模拟兼容协议，按任务类型返回对应结构，不调用真实模型。"""
    payload: object = await request.json()
    body = payload if isinstance(payload, dict) else {}
    if "[task: enrich_message]" in _system_prompt(body):
        content = {
            "title": "精简标题",
            "summary": "计算平台已经发布，适合检索归档。",
            "keywords": ["计算平台", "发布"],
        }
    else:
        content = {
            "title": "新计算平台",
            "summary": "计算平台已发布。",
            "content": "这是中文正文。",
        }
    return {
        "choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}],
        "usage": {"total_tokens": 3},
    }


app.mount("/", FrontendFiles(directory=ROOT / "runtime/frontend", html=True))

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8877)
