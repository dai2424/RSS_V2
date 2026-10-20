"""全文渲染：结构化正文 → 受控 HTML / Markdown。

渲染器**不做"整篇 Markdown → HTML"的转换**：输入是已经过校验的结构化正文
（分节、段落、引用、来源），我们只对每段纯文本做少量行内替换，替换结果只有
下面这些常量标签。所有文本先 `html.escape`，再插入标签，因此输入里的任何
`<script>`、属性注入或非法协议都只会变成**文字**——这是界面规范"不注入未清洗
HTML"的落法，也让前端不需要 Markdown 清洗依赖。

同一份实现服务：P5 的预览、P6a 的编辑预览、P6b 的 HTML 片段/导出。
"""

from __future__ import annotations

import html
import re
from urllib.parse import urlsplit

from rss_v2.domain.articles import ArticleBody, ArticleSource

#: 允许渲染为链接的协议；其余（javascript/data/vbscript/相对路径）只保留文字。
SAFE_LINK_SCHEMES = frozenset({"http", "https"})

#: 行内语法一次匹配；顺序即优先级：代码 → 粗体 → 斜体 → 链接 → 引用标记。
#: 不支持嵌套（例如链接文字里的粗体），这是刻意的 Markdown 子集口径。
#: 链接地址允许一层配对的圆括号（`javascript:alert(1)` 这类地址也能被完整识别，
#: 从而整段判为不安全；否则会漏下一个 ")" 留在正文里）。
_LINK_URL = r"(?:[^()\s<]|\([^()\s<]*\))+"
_INLINE = re.compile(
    r"`(?P<code>[^`\n]+)`"
    r"|\*\*(?P<bold>[^*\n]+)\*\*"
    r"|(?<!\*)\*(?P<italic>[^*\n]+)\*(?!\*)"
    rf"|\[(?P<link_text>[^\]\n<]+)\]\((?P<link_url>{_LINK_URL})\)"
    r"|\[\^(?P<marker>\d{1,3})\]"
)


def safe_url(url: str) -> str | None:
    """返回可直接放进 `href` 的地址；协议或主机不合法时返回 None。

    入参可能是已转义的文本（`&amp;` 等），先还原再解析，避免把合法链接误杀。
    """

    raw = html.unescape(url).strip()
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    if parts.scheme.lower() not in SAFE_LINK_SCHEMES or not parts.netloc:
        return None
    return raw


def safe_markdown_link(text: str, url: str) -> str:
    """把一段 `[文字](地址)` 归一化：地址不安全时只保留文字。"""

    return f"[{text}]({url})" if safe_url(url) is not None else text


def render_inline(text: str) -> str:
    """渲染一段纯文本的行内标记；除白名单标签外不产生任何 HTML。"""

    return _INLINE.sub(_replace, html.escape(text, quote=True))


def _replace(match: re.Match[str]) -> str:
    """把一个行内匹配换成常量标签；捕获组已经是转义后的文本，可原样嵌入。"""

    if (code := match.group("code")) is not None:
        return f"<code>{code}</code>"
    if (bold := match.group("bold")) is not None:
        return f"<strong>{bold}</strong>"
    if (italic := match.group("italic")) is not None:
        return f"<em>{italic}</em>"
    if (marker := match.group("marker")) is not None:
        # 引用标记渲染成按钮：前端用事件委托就地展开证据，不需要内联脚本。
        return (
            f'<sup><button type="button" class="citation-marker"'
            f' data-marker="{marker}">{marker}</button></sup>'
        )
    text, url = match.group("link_text"), match.group("link_url")
    if text is None or url is None:
        return match.group(0)
    safe = safe_url(url)
    if safe is None:
        return text
    return (
        f'<a href="{html.escape(safe, quote=True)}" rel="noopener noreferrer"'
        f' target="_blank">{text}</a>'
    )


def render_source_html(source: ArticleSource) -> str:
    """文末来源列表的一项：`标题 — 发布者 — 链接`。"""

    title = html.escape(source.title or "（无标题）", quote=True)
    publisher = html.escape(source.publisher, quote=True)
    safe = safe_url(source.url)
    link = (
        f'<a href="{html.escape(safe, quote=True)}" rel="noopener noreferrer" target="_blank">'
        f"{html.escape(source.url, quote=True)}</a>"
        if safe is not None
        else html.escape(source.url, quote=True)
    )
    return f"<li>{title} — {publisher} — {link}</li>"


def render_article_html(title: str, lead: str, body: ArticleBody) -> str:
    """把正文渲染成受控 HTML：标题、导语、分节段落、文末来源列表。"""

    parts = [f"<h1>{html.escape(title, quote=True)}</h1>"]
    if lead.strip():
        parts.append(f'<p class="lead">{render_inline(lead)}</p>')
    for section in body.sections:
        parts.append(f"<h2>{html.escape(section.heading, quote=True)}</h2>")
        for paragraph in section.paragraphs:
            parts.append(
                f'<p data-kind="{html.escape(paragraph.kind, quote=True)}">'
                f"{render_inline(paragraph.text)}</p>"
            )
    if body.source_list:
        items = "".join(render_source_html(source) for source in body.source_list)
        parts.append(f'<ul class="source-list">{items}</ul>')
    return "\n".join(parts)


def render_article_markdown(title: str, lead: str, body: ArticleBody) -> str:
    """把正文渲染成 Markdown（供落库、复制与导出）。

    Markdown 是给编辑器与平台的正文，**不做 HTML 转义**：它不含我们自己生成的标签，
    也不经过浏览器渲染；预览走 `render_article_html`，那条路径全程转义。
    引用标记保持 `[^n]` 原样，交给平台或 P6b 决定呈现方式。
    """

    lines = [f"# {title}".rstrip()]
    if lead.strip():
        lines += ["", lead.strip()]
    for section in body.sections:
        lines += ["", f"## {section.heading}".rstrip()]
        for paragraph in section.paragraphs:
            lines += ["", paragraph.text.strip()]
    if body.source_list:
        lines += ["", "## 来源", ""]
        lines += [
            f"- {source.title or '（无标题）'} — {source.publisher} — {source.url}"
            for source in body.source_list
        ]
    return "\n".join(lines).strip() + "\n"
