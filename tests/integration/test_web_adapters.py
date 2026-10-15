"""外部读取适配器测试：SearXNG 检索与网页正文抽取（respx 固定网络响应）。

适配器不抛业务异常、失败也返回结果，因此这里逐项验证"什么情况归到哪个状态/错误码"，
以及真实实例上的两个坑：`publishedDate` 返回字符串 "None"、接口没有 limit 参数。
"""

from __future__ import annotations

import ipaddress
from typing import Any

import httpx
import pytest
import respx

from rss_v2.adapters.web.reader import HTTPXArticleReader
from rss_v2.adapters.web.searxng import HTTPXSearchClient
from rss_v2.domain import ExternalServiceError, MaterialStatus

PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """把域名解析固定成公网地址：respx 拦得住请求，但拦不住 socket.getaddrinfo。

    字面量 IP 原样返回，这样"重定向跳到内网"的用例仍然走真实的网段判断。
    """

    def resolve(host: str) -> list[str]:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return [PUBLIC_IP]
        return [host]

    monkeypatch.setattr("rss_v2.adapters.web.reader._resolve", resolve)


def _reader(max_bytes: int = 2_000_000) -> HTTPXArticleReader:
    return HTTPXArticleReader(timeout_seconds=5.0, max_bytes=max_bytes)


def _html(body: str, head: str = "") -> str:
    return f"""<!doctype html><html><head>{head}</head><body>{body}</body></html>"""


#: 正文长度要超过"仅摘要"的判定阈值（400 字符），否则会被归为 summary_only。
PARAGRAPH = "这是一段正文，用来验证段落抽取与空行分隔。" * 24


@respx.mock
def test_reader_extracts_article_metadata_and_drops_noise() -> None:
    """正文取 article、去掉导航与脚本，元信息按 og/meta 取。"""

    html = _html(
        f"""
<nav>导航内容不该出现</nav>
<article>
  <h1>尊界 V800 制动争议</h1>
  <p>{PARAGRAPH}</p>
  <p>{PARAGRAPH}</p>
  <div class="share-bar">分享按钮</div>
  <script>console.log("不该出现")</script>
</article>
<footer>页脚内容不该出现</footer>
""",
        head=(
            '<meta property="og:title" content="尊界 V800 制动争议 - 测试站">'
            '<meta property="og:site_name" content="测试站">'
            '<meta property="article:published_time" content="2026-10-08T02:41:00+00:00">'
        ),
    )
    respx.get("https://news.test/a").mock(return_value=httpx.Response(200, text=html))

    read = _reader().read("https://news.test/a")
    assert read.status == MaterialStatus.READ.value
    assert read.title == "尊界 V800 制动争议 - 测试站"
    assert read.publisher == "测试站"
    assert read.published_at == 1791427260  # 2026-10-08 02:41 UTC
    assert "导航内容不该出现" not in read.text
    assert "页脚内容不该出现" not in read.text
    assert "不该出现" not in read.text
    assert "\n\n" in read.text
    assert read.content_hash and read.byte_size > 0 and not read.truncated


@respx.mock
def test_reader_falls_back_to_main_and_short_page_becomes_summary() -> None:
    """没有 article 时用 main；正文不足 400 字且只有描述时归为"仅摘要"。"""

    html = _html(
        "<main><p>太短</p></main>",
        head='<meta name="description" content="这是页面描述，可作为摘要线索。">',
    )
    respx.get("https://short.test/a").mock(return_value=httpx.Response(200, text=html))
    read = _reader().read("https://short.test/a")
    assert read.status == MaterialStatus.SUMMARY_ONLY.value
    assert read.text == "这是页面描述，可作为摘要线索。"
    assert read.content_hash


@respx.mock
def test_reader_marks_unparsable_page() -> None:
    """既没有正文也没有描述：明确记 article_unparsable，不返回空"成功"。"""

    html = _html("<div><span>只有壳</span></div>")
    respx.get("https://empty.test/a").mock(return_value=httpx.Response(200, text=html))
    read = _reader().read("https://empty.test/a")
    assert read.status == MaterialStatus.FAILED.value
    assert read.error_code == "article_unparsable"


@respx.mock
def test_reader_follows_redirects_and_keeps_final_url() -> None:
    respx.get("https://go.test/x").mock(
        return_value=httpx.Response(302, headers={"location": "https://news.test/final"})
    )
    respx.get("https://news.test/final").mock(
        return_value=httpx.Response(200, text=_html(f"<article><p>{PARAGRAPH}</p></article>"))
    )
    read = _reader().read("https://go.test/x")
    assert read.status == MaterialStatus.READ.value
    assert read.final_url == "https://news.test/final"


@respx.mock
def test_reader_rejects_redirect_loop_and_private_targets() -> None:
    """重定向超过上限与"跳到内网"都要拒绝：检索结果来自外部，不能当内网代理。"""

    for index in range(8):
        respx.get(f"https://loop.test/{index}").mock(
            return_value=httpx.Response(302, headers={"location": f"https://loop.test/{index + 1}"})
        )
    looped = _reader().read("https://loop.test/0")
    assert looped.status == MaterialStatus.FAILED.value
    assert looped.error_code == "article_http_error"

    respx.get("https://public.test/a").mock(
        return_value=httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
    )
    blocked = _reader().read("https://public.test/a")
    assert blocked.status == MaterialStatus.FAILED.value
    assert blocked.error_code == "ssrf_blocked"


def test_reader_rejects_private_address_and_bad_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("rss_v2.adapters.web.reader._resolve", lambda host: ["10.1.2.3"])
    private = _reader().read("https://internal.test/a")
    assert private.error_code == "ssrf_blocked"
    bad_scheme = _reader().read("ftp://files.test/a")
    assert bad_scheme.error_code == "article_invalid_url"


@respx.mock
def test_reader_marks_status_codes_and_timeouts() -> None:
    respx.get("https://gone.test/a").mock(return_value=httpx.Response(404))
    gone = _reader().read("https://gone.test/a")
    assert (gone.status, gone.error_code) == (MaterialStatus.UNAVAILABLE.value, "")

    respx.get("https://broken.test/a").mock(return_value=httpx.Response(503))
    broken = _reader().read("https://broken.test/a")
    assert broken.error_code == "article_http_error"

    respx.get("https://slow.test/a").mock(side_effect=httpx.ConnectTimeout("timeout"))
    slow = _reader().read("https://slow.test/a")
    assert slow.error_code == "article_timeout"


@respx.mock
def test_reader_truncates_large_body() -> None:
    """超过体积上限时保留已读前缀并标记 truncated，不当作失败。"""

    body = "<article><p>" + ("超长正文" * 5000) + "</p></article>"
    respx.get("https://big.test/a").mock(return_value=httpx.Response(200, text=_html(body)))
    read = _reader(max_bytes=5000).read("https://big.test/a")
    assert read.status == MaterialStatus.READ.value
    assert read.truncated is True
    assert read.byte_size == 5000  # 严格不超过配置上限


@respx.mock
def test_reader_decodes_gbk_page() -> None:
    """中文站点常见 GBK 编码：按 meta charset 解码，不出现乱码。"""

    html = f"""<html><head><meta charset="gb18030"></head>
<body><article><p>{PARAGRAPH}</p></article></body></html>"""
    respx.get("https://gbk.test/a").mock(
        return_value=httpx.Response(
            200, content=html.encode("gb18030"), headers={"content-type": "text/html"}
        )
    )
    read = _reader().read("https://gbk.test/a")
    assert read.status == MaterialStatus.READ.value
    assert "验证段落抽取" in read.text


def _search_hits(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=payload)


@respx.mock
def test_searxng_parses_hits_and_slices_locally() -> None:
    """官方接口没有 limit 参数：命中在本地截断，publishedDate 为 "None" 时按缺失处理。"""

    payload = {
        "results": [
            {
                "title": f"结果 {index}",
                "url": f"https://news.test/{index}",
                "content": "摘要",
                "engine": "bing",
                "publishedDate": "None" if index == 1 else "2026-10-08T02:41:00+00:00",
            }
            for index in range(5)
        ],
        "unresponsive_engines": [["brave", "too many requests"]],
    }
    route = respx.get("http://search.test/search").mock(return_value=_search_hits(payload))
    client = HTTPXSearchClient("http://search.test", 5.0)

    outcome = client.search("OpenAI", limit=3)
    assert [hit.url for hit in outcome.hits] == [
        "https://news.test/0",
        "https://news.test/1",
        "https://news.test/2",
    ]
    assert outcome.hits[1].published_at is None
    assert outcome.hits[0].published_at == 1791427260  # 2026-10-08 02:41 UTC
    assert outcome.unresponsive_engines == ("brave",)

    params = route.calls.last.request.url.params
    assert (
        params["format"] == "json" and params["language"] == "all" and params["safesearch"] == "0"
    )
    assert "limit" not in params  # 真实实例不接受 limit，传了也会被忽略


@respx.mock
def test_searxng_reports_failures_by_class() -> None:
    client = HTTPXSearchClient("http://search.test", 5.0)
    respx.get("http://search.test/search").mock(return_value=httpx.Response(403, text="forbidden"))
    with pytest.raises(ExternalServiceError) as forbidden:
        client.search("q", limit=5)
    assert forbidden.value.code == "search_format_disabled"

    respx.get("http://search.test/search").mock(return_value=httpx.Response(429))
    with pytest.raises(ExternalServiceError) as limited:
        client.search("q", limit=5)
    assert limited.value.code == "search_rate_limited"

    respx.get("http://search.test/search").mock(return_value=httpx.Response(502))
    with pytest.raises(ExternalServiceError) as broken:
        client.search("q", limit=5)
    assert broken.value.code == "search_server_error"

    respx.get("http://search.test/search").mock(return_value=httpx.Response(200, text="<html>"))
    with pytest.raises(ExternalServiceError) as invalid:
        client.search("q", limit=5)
    assert invalid.value.code == "search_invalid_output"

    respx.get("http://search.test/search").mock(side_effect=httpx.ConnectTimeout("slow"))
    with pytest.raises(ExternalServiceError) as timeout:
        client.search("q", limit=5)
    assert timeout.value.code == "search_timeout"


def test_searxng_requires_configuration() -> None:
    """没有配置地址时明确失败：不用摘要或模型记忆冒充已读资料。"""

    client = HTTPXSearchClient("", 5.0)
    with pytest.raises(ExternalServiceError) as unconfigured:
        client.search("q", limit=5)
    assert unconfigured.value.code == "search_unconfigured"


@respx.mock
def test_searxng_empty_result_with_unresponsive_engines() -> None:
    """引擎全被限流时结果为空——这不是"没有资料"，未响应引擎要带回去。"""

    respx.get("http://search.test/search").mock(
        return_value=_search_hits(
            {"results": [], "unresponsive_engines": [["google cse", "too many requests"]]}
        )
    )
    outcome = HTTPXSearchClient("http://search.test", 5.0).search("q", limit=5)
    assert outcome.hits == ()
    assert outcome.unresponsive_engines == ("google cse",)
