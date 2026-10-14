"""浏览器验收专用本机服务；fixture 端点不会进入生产应用。"""

import json
import re
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


def _user_prompt(body: dict[str, Any]) -> str:
    """读取请求里的用户提示；合并建议要从里面取本批词表。"""

    messages = body.get("messages")
    if not isinstance(messages, list):
        return ""
    return " ".join(
        str(item.get("content", ""))
        for item in messages
        if isinstance(item, dict) and item.get("role") == "user"
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


#: 探测请求的标记与固定回复：真实模型对"回一句连接正常"只会回普通文本，
#: 这里照实模拟，避免假端点比真实上游"更配合"，掩盖连接测试的判断口径。
PROBE_MARKER = "[task: probe"
PROBE_REPLY = "The connection works."


def _probe_text(system_prompt: str) -> str | None:
    """探测请求返回普通文本；业务请求返回 None 交给下面的结构化分支。"""

    return PROBE_REPLY if PROBE_MARKER in system_prompt else None


#: 建议组里至少要两个词；不够两个就返回空建议。
_BATCH_LINE = re.compile(r"^- (?P<text>.+)（(?P<kind>.+?)）$", re.MULTILINE)


def _suggestion_groups(user_prompt: str) -> list[dict[str, object]]:
    """从注入的词表里取前两个写法组成一组。

    固定写死两个词会在词表变化时被服务端的越界过滤丢掉，这里照实"读词表再建议"，
    与真实模型的行为一致。
    """

    terms = _BATCH_LINE.findall(user_prompt)
    first = [item[0].strip() for item in terms]
    if len(first) < 2:
        return []
    return [
        {
            "terms": first[:2],
            "target": first[0],
            "rationale": "固定样例：两条写法疑似同指",
        }
    ]


def _fixture_content(system_prompt: str, user_prompt: str) -> dict[str, object]:
    """按任务类型标记返回对应结构；两种协议的假端点共用它。"""

    if "[task: keyword_merge_suggest" in system_prompt:
        return {"groups": _suggestion_groups(user_prompt)}
    if "[task: enrich_message" in system_prompt:
        return {
            "title": "精简标题",
            "summary": "计算平台已经发布，适合检索归档。",
            "keywords": [
                {"text": "计算平台", "kind": "entity"},
                {"text": "发布", "kind": "event"},
            ],
        }
    return {
        "title": "新计算平台",
        "summary": "计算平台已发布。",
        "content": "这是中文正文。",
    }


@app.post("/fixtures/suggestions/reset")
def reset_suggestions() -> dict[str, int]:
    """浏览器验收专用：清掉合并建议与批次台账，让用例可重复执行。

    产品里没有"清空建议"的操作——批次台账是防重复付费的；只有夹具需要反复重放同一条流程。
    """

    with app.state.container.database.transaction() as connection:
        cleared = int(connection.execute("SELECT COUNT(*) FROM keyword_suggestions").fetchone()[0])
        connection.execute("DELETE FROM keyword_suggestions")
        connection.execute("DELETE FROM keyword_suggestion_batches")
    return {"cleared": cleared}


@app.post("/fixtures/v1/chat/completions")
async def completion(request: Request) -> dict[str, object]:
    """模拟兼容协议，按任务类型返回对应结构，不调用真实模型。"""
    payload: object = await request.json()
    body = payload if isinstance(payload, dict) else {}
    prompt = _system_prompt(body)
    text = _probe_text(prompt)
    reply = (
        text
        if text is not None
        else json.dumps(_fixture_content(prompt, _user_prompt(body)), ensure_ascii=False)
    )
    return {
        "choices": [{"message": {"content": reply}}],
        "usage": {"total_tokens": 3},
    }


@app.post("/fixtures/v1/messages")
async def messages(request: Request) -> dict[str, object]:
    """模拟 Anthropic Messages 协议：系统提示在请求顶层，文本在 content 数组里。"""
    payload: object = await request.json()
    body = payload if isinstance(payload, dict) else {}
    prompt = str(body.get("system") or "")
    user_prompt = _user_prompt({"messages": body.get("messages")}) or str(
        body.get("messages") or ""
    )
    text = _probe_text(prompt)
    reply = (
        text
        if text is not None
        else json.dumps(_fixture_content(prompt, user_prompt), ensure_ascii=False)
    )
    return {
        "content": [{"type": "text", "text": reply}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 5, "output_tokens": 3},
    }


app.mount("/", FrontendFiles(directory=ROOT / "runtime/frontend", html=True))

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8877)
