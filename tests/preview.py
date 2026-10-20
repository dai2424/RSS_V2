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

settings = Settings(
    runtime_dir=ROOT / "runtime/e2e",
    frontend_dist=ROOT / "runtime/no-dist",
    # 仅夹具：资料准备要读取本机 /fixtures/article 页面；正式环境保持拒绝内网。
    article_allow_private_hosts=True,
    search_base_url="http://127.0.0.1:8877/fixtures",
)
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


@app.get("/fixtures/stage4-feed.xml")
def stage4_feed() -> Response:
    """三条消息的 RSS，供选题发现验收：前两条是同一件事、第三条独立。

    发布时间按当前时间生成：默认窗口是"最近 7 天"，写死日期会随时间过期。
    """

    base = datetime.now(tz=UTC) - timedelta(hours=6)
    rows = [
        ("尊界 V800 制动测试引争议", "懂车帝 100-0 km/h 实测中三台车支架断裂。" * 4, 3),
        ("尊界回应制动踏板问题", "厂商称按高于国标的要求验证，将免费升级。" * 4, 2),
        ("开源项目让安卓车机跑 CarPlay", "纯软件方案无需额外硬件。" * 4, 1),
    ]
    items = "".join(
        f"""<item>
  <guid>stage4-{index}</guid>
  <title>{title}</title>
  <link>https://example.test/stage4-{index}</link>
  <description><![CDATA[{text}]]></description>
  <pubDate>{(base - timedelta(hours=offset)).strftime("%a, %d %b %Y %H:%M:%S GMT")}</pubDate>
</item>
"""
        for index, (title, text, offset) in enumerate(rows, start=1)
    )
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Stage 4 选题</title>
<link>https://example.test/</link>
<description>Stage 4 discovery fixture</description>
<language>zh-cn</language>
{items}</channel></rss>"""
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


#: 发现任务的输入行以 "[消息ID] 时间 | 来源 | ..." 开头（服务端渲染的格式）。
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_DISCOVERY_MESSAGE = re.compile(rf"^\[({_UUID})\]", re.MULTILINE)


def _discovery_candidates(user_prompt: str) -> dict[str, object]:
    """把提示词里的消息按"前两条一个事件、其余各自成候选"分组。

    照实读输入再分组（而不是写死 ID）：服务端的越界过滤会把写死的 ID 全部丢掉，
    界面验收就看不到候选。第一条事件的推荐度最高，便于断言排序。
    """

    ids = _DISCOVERY_MESSAGE.findall(user_prompt)
    if not ids:
        return {"candidates": []}
    groups = [ids[:2]] + [[item] for item in ids[2:]]
    candidates: list[dict[str, object]] = []
    for index, members in enumerate(groups, start=1):
        candidates.append(
            {
                "member_message_ids": members,
                "title": f"夹具事件 {index}",
                "summary": f"夹具事件的概要 {index}：同一件事的多篇报道已合并。",
                "score": max(1, 5 - index),
                "reasons": [{"kind": "interesting", "text": f"夹具推荐理由 {index}"}],
            }
        )
    return {"candidates": candidates}


#: 资料整理的输入行：[材料 <id>] 标题（来源），正文在下一行。
_DIGEST_MATERIAL = re.compile(r"^\[材料 ([0-9a-f-]{36})\] ", re.MULTILINE)


def _digest_result(user_prompt: str) -> dict[str, object]:
    """按提示词里的材料与正文生成事实：引文从正文原样截取，服务端一定定位得到。"""

    materials = _DIGEST_MATERIAL.findall(user_prompt)
    if not materials:
        return {"facts": [], "conflicts": [], "gaps": []}
    facts: list[dict[str, object]] = []
    for material_id in materials[:2]:
        marker = f"[材料 {material_id}]"
        after = user_prompt.split(marker, 1)[1]
        body = after.split("正文：", 1)[1].strip()
        quote = body[:60].strip()
        facts.append(
            {
                "statement": f"验收事实：{quote[:24]}",
                "kind": "fact",
                "supports": [{"material_id": material_id, "quote": quote}],
            }
        )
    return {
        "facts": facts,
        "conflicts": [{"text": "验收分歧：厂商声明与测试结论不一致", "statement_indexes": [0]}],
        "gaps": [{"text": "验收缺口：缺少监管部门的一手通报", "blocking": True}],
    }


#: 提纲的输入行：[事实 <id>] 陈述（类型），引文在下一行；引文 id 不进提示词，
#: 因此夹具只引用事实 id，引文由服务端按事实归属核对（本来就是服务端的事）。
_OUTLINE_FACT = re.compile(r"^\[([0-9a-f-]{36})\] ", re.MULTILINE)


def _outline_result(user_prompt: str) -> dict[str, object]:
    """按提示词里的事实 id 生成两节提纲：id 原样引用，服务端一定核对得到。"""

    facts = _OUTLINE_FACT.findall(user_prompt)
    return {
        "title": "验收：夹具提纲",
        "angle": "夹具角度：从第三方测试与厂商声明两条线索切入。",
        "sections": [
            {
                "heading": "发生了什么",
                "talking_points": ["第三方测试出现异常", "厂商给出了自己的说法"],
                "fact_ids": facts[:1],
                "impact_notes": ["同类产品可能被要求补充验证"],
            },
            {
                "heading": "行业影响",
                "talking_points": ["检测标准可能被推动更新"],
                "fact_ids": facts[1:2],
            },
        ],
    }


def _fixture_content(system_prompt: str, user_prompt: str) -> dict[str, object]:
    """按任务类型标记返回对应结构；两种协议的假端点共用它。"""

    if "[task: prepare_materials" in system_prompt:
        return {"queries": [{"text": "验收检索式", "purpose": "夹具：中文检索"}]}
    if "[task: digest_materials" in system_prompt:
        return _digest_result(user_prompt)
    if "[task: generate_outline" in system_prompt:
        return _outline_result(user_prompt)
    if "[task: discover_events" in system_prompt:
        return _discovery_candidates(user_prompt)
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


@app.get("/fixtures/search")
def fixture_search(request: Request) -> dict[str, object]:
    """SearXNG 形状的检索结果：命中指向本机夹具页面，便于走通"检索→读取"整条链路。"""

    query = request.query_params.get("q", "")
    hits = [
        {
            "title": "验收：第三方测试报道",
            "url": "http://127.0.0.1:8877/fixtures/article?doc=test",
            "content": "夹具摘要：第三方独立测试的报道。",
            "engine": "fixture",
            "publishedDate": "None",  # 真实实例在缺失时返回字符串 "None"
        },
        {
            "title": "验收：厂商声明",
            "url": "http://127.0.0.1:8877/fixtures/article?doc=maker",
            "content": "夹具摘要：厂商声明页面。",
            "engine": "fixture",
        },
    ]
    return {
        "query": query,
        "results": hits,
        "unresponsive_engines": [["brave", "too many requests"]],
    }


@app.get("/fixtures/article")
def fixture_article(request: Request) -> Response:
    """夹具原文：正文足够长（正文抽取阈值 400 字），两篇内容不同以免被去重。"""

    doc = request.query_params.get("doc", "test")
    bodies = {
        "test": (
            "第三方测试机构在封闭场地对三台尊界 V800 进行了紧急制动测试，"
            "三台车的刹车踏板支架均在测试中出现断裂，断裂瞬间的踏板力读数约为 1612 牛。"
        ),
        "maker": (
            "尊界汽车回应称，其制动系统按照高于国家标准的要求完成全流程开发与验证，"
            "在用户实际使用场景中未发生制动踏板支架底座断裂，并将为该部件提供免费升级。"
        ),
    }
    body = bodies.get(doc, bodies["test"]) * 6
    html = f"""<!doctype html><html><head>
<meta property="og:title" content="验收原文 {doc}">
<meta property="og:site_name" content="验收站点">
</head><body><article><p>{body}</p></article></body></html>"""
    return Response(html.encode("utf-8"), media_type="text/html")


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
