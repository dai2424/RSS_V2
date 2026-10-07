"""浏览器验收专用本机服务；fixture 端点不会进入生产应用。"""

import json
import os
from pathlib import Path

import uvicorn
from fastapi.responses import Response

from rss_v2.api.app import create_app
from rss_v2.api.static import FrontendFiles
from rss_v2.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
os.environ["RSS_LLM_KEY_E2E"] = "fake-e2e-secret"
settings = Settings(runtime_dir=ROOT / "runtime/e2e", frontend_dist=ROOT / "runtime/no-dist")
app = create_app(settings)


@app.get("/fixtures/feed.xml")
def feed() -> Response:
    """仅提供固定 RSS，实际 HTTPX 适配器仍会访问网络边界。"""
    return Response(
        (ROOT / "tests/fixtures/sample_feed.xml").read_bytes(), media_type="application/xml"
    )


@app.post("/fixtures/v1/chat/completions")
def completion() -> dict[str, object]:
    """模拟兼容协议，不调用真实模型。"""
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "title": "新计算平台",
                            "summary": "计算平台已发布。",
                            "content": "这是中文正文。",
                        },
                        ensure_ascii=False,
                    )
                }
            }
        ],
        "usage": {"total_tokens": 3},
    }


app.mount("/", FrontendFiles(directory=ROOT / "runtime/frontend", html=True))

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8877)
