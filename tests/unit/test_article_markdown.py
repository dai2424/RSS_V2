"""全文渲染器的单元测试：转义、白名单标签与链接协议。

渲染器是整个 P5 里唯一产出 HTML 的地方，因此这里对"不可信输入"做逐条断言：
任何输入都只可能得到转义后的文字与固定标签，不产生脚本、事件属性或非 http(s) 链接。
"""

from __future__ import annotations

from rss_v2.domain import (
    ArticleBody,
    ArticleParagraph,
    ArticleSection,
    ArticleSource,
)
from rss_v2.domain.article_markdown import (
    render_article_html,
    render_article_markdown,
    render_inline,
    safe_url,
)


def _body(paragraph_text: str = "正文", *, kind: str = "fact") -> ArticleBody:
    return ArticleBody(
        sections=(
            ArticleSection(
                heading="发生了什么",
                paragraphs=(ArticleParagraph(text=paragraph_text, kind=kind),),
            ),
        )
    )


def test_inline_escapes_untrusted_html() -> None:
    """尖括号与引号一律转义，不产生可执行标签。"""

    rendered = render_inline('<script>alert("x")</script>')
    assert "<script" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "&quot;x&quot;" in rendered


def test_inline_formats_whitelisted_markers() -> None:
    """粗体、斜体、行内代码渲染成固定标签。"""

    assert render_inline("**重点**") == "<strong>重点</strong>"
    assert render_inline("*斜体*") == "<em>斜体</em>"
    assert render_inline("`code`") == "<code>code</code>"


def test_inline_keeps_code_content_literal() -> None:
    """代码片段里的标记不参与解析（一次匹配的优先级）。"""

    assert render_inline("`**not bold**`") == "<code>**not bold**</code>"


def test_inline_links_require_http_scheme() -> None:
    """只放行 http/https；其它协议只保留文字。"""

    assert render_inline("[官网](https://example.test/a)") == (
        '<a href="https://example.test/a" rel="noopener noreferrer" target="_blank">官网</a>'
    )
    for raw in ("javascript:alert(1)", "data:text/html,x", "/relative", "//evil.test/a"):
        rendered = render_inline(f"[文字]({raw})")
        assert "<a " not in rendered, raw
        assert rendered == "文字"


def test_safe_url_unescapes_before_parsing() -> None:
    """转义过的查询串（&amp;）仍被认作合法地址。"""

    assert safe_url("https://example.test/a?x=1&amp;y=2") == "https://example.test/a?x=1&y=2"
    assert safe_url("mailto:a@example.test") is None
    assert safe_url("https://") is None


def test_citation_marker_renders_as_button() -> None:
    """引用标记渲染成按钮，前端用事件委托就地展开证据。"""

    rendered = render_inline("实测出现异常[^3]。")
    assert '<button type="button" class="citation-marker" data-marker="3">3</button>' in rendered


def test_article_html_structure_and_sources() -> None:
    """HTML 由结构生成：标题、小标题、段落类型属性与文末来源列表。"""

    body = ArticleBody(
        sections=(
            ArticleSection(
                heading="行业影响",
                paragraphs=(ArticleParagraph(text="可能推动标准更新[^1]。", kind="speculation"),),
            ),
        ),
        source_list=(
            ArticleSource(
                material_id="m1",
                title="第三方报道",
                publisher="量子位",
                url="https://example.test/report",
            ),
        ),
    )
    html = render_article_html("标题", "导语", body)
    assert html.startswith("<h1>标题</h1>")
    assert '<p class="lead">导语</p>' in html
    assert "<h2>行业影响</h2>" in html
    assert '<p data-kind="speculation">' in html
    assert "第三方报道 — 量子位 — " in html
    assert 'href="https://example.test/report"' in html


def test_markdown_keeps_markers_and_sources() -> None:
    """Markdown 给编辑器与导出用：保留引用标记，文末来源按固定格式。"""

    body = ArticleBody(
        sections=(
            ArticleSection(
                heading="发生了什么",
                paragraphs=(ArticleParagraph(text="实测出现异常[^1]。", kind="fact"),),
            ),
        ),
        source_list=(
            ArticleSource(
                material_id="m1", title="第三方报道", publisher="量子位", url="https://e.test/a"
            ),
        ),
    )
    markdown = render_article_markdown("标题", "导语", body)
    assert markdown.startswith("# 标题\n")
    assert "导语" in markdown
    assert "## 发生了什么" in markdown
    assert "实测出现异常[^1]。" in markdown
    assert "## 来源" in markdown
    assert "- 第三方报道 — 量子位 — https://e.test/a" in markdown
    # Markdown 里不出现 HTML 标签（它给平台与编辑器，不经过浏览器）。
    assert "<p" not in markdown
