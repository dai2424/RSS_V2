"""浏览器验收专用本机服务；fixture 端点不会进入生产应用。"""

import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Request
from fastapi.responses import Response

# 直接跑工作区的源码：uv 会把项目装进环境，包快照可能落后于刚改过的文件，
# 那样 E2E 会在"旧代码"上通过。pytest 用 pythonpath 解决同一问题。
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rss_v2.api.app import create_app  # noqa: E402  必须在 sys.path 调整之后
from rss_v2.api.static import FrontendFiles  # noqa: E402
from rss_v2.settings import Settings  # noqa: E402

settings = Settings(runtime_dir=ROOT / "runtime/e2e", frontend_dist=ROOT / "runtime/no-dist")
app = create_app(settings)


@app.get("/fixtures/feed.xml")
def feed() -> Response:
    """仅提供固定 RSS，实际 HTTPX 适配器仍会访问网络边界。"""
    return Response(
        (ROOT / "tests/fixtures/sample_feed.xml").read_bytes(), media_type="application/xml"
    )


#: 每个 case 被请求过几次：同一来源的第二次采集起换一版正文，用来造第二个版本。
FEED_HITS: Counter[str] = Counter()


@app.get("/fixtures/two-items-feed.xml")
def two_items_feed(request: Request) -> Response:
    """两个条目的 RSS，供"关键词跳转"与"历史版本"两条验收使用。

    第一条消息第二次采集起改一处正文，于是它有了第二个版本；第二条消息的**正文**里
    出现"计算平台"，而它的结构化关键词是另一个词——用来证明词表的"查看消息"走的是
    关键词检索而不是全文搜索。发布时间按当前时间生成：搜索默认只看最近一周，写死
    日期会随时间过期。
    """

    key = request.query_params.get("case", "")
    FEED_HITS[key] += 1
    first = (
        "A new computing platform is available, now with more capacity."
        if FEED_HITS[key] > 1
        else "A new computing platform is available."
    )
    stamp = (datetime.now(tz=UTC) - timedelta(days=1)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Two Items</title>
<link>https://example.test/</link>
<description>Two Items</description>
<language>en-us</language>
<item>
  <guid>two-1</guid>
  <title>New computing platform</title>
  <link>https://example.test/two-1</link>
  <description><![CDATA[<p>{first}</p>]]></description>
  <pubDate>{stamp}</pubDate>
</item>
<item>
  <guid>two-2</guid>
  <title>Second platform update</title>
  <link>https://example.test/two-2</link>
  <description><![CDATA[<p>计算平台周边配件同步更新。</p>]]></description>
  <pubDate>{stamp}</pubDate>
</item>
</channel></rss>"""
    return Response(body.encode("utf-8"), media_type="application/xml")


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
        # 第二条 fixture 消息给一个与正文无关的关键词：正文里有"计算平台"，关键词里没有，
        # 于是"按关键词查"与"按全文查"在界面上能区分开。
        if "Second platform" in user_prompt:
            return {
                "title": "第二平台更新",
                "summary": "第二平台已上线，配件同步更新。",
                "keywords": [{"text": "量子芯片", "kind": "entity"}],
            }
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
        connection.execute("DELETE FROM keyword_suggestion_scanned")
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
