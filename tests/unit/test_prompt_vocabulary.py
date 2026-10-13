"""注入词表的渲染与复用键：纯函数，不碰数据库。"""

from rss_v2.domain import PromptVocabulary, render_vocabulary
from rss_v2.domain.keywords import EMPTY_VOCABULARY_TEXT


def test_render_is_deterministic_regardless_of_input_order() -> None:
    """同一份词表两次渲染必须完全一致，否则 llm_calls.input_hash 无法解释。"""

    one = PromptVocabulary(
        entities=("OpenAI", "ChatGPT"), topics=("AI 编程助手",), events=(), recent=()
    )
    two = PromptVocabulary(
        entities=("ChatGPT", "OpenAI"), topics=("AI 编程助手",), events=(), recent=()
    )
    assert render_vocabulary(one) == render_vocabulary(two)
    assert one.fingerprint == two.fingerprint


def test_render_groups_are_ordered_and_labeled() -> None:
    vocabulary = PromptVocabulary(
        entities=("ChatGPT",),
        topics=("智能驾驶",),
        events=("Codex 发布新版",),
        recent=("固态电池",),
    )
    assert render_vocabulary(vocabulary).splitlines() == [
        "实体：ChatGPT",
        "主题：智能驾驶",
        "事件：Codex 发布新版",
        "近期新增：固态电池",
    ]


def test_render_skips_empty_groups() -> None:
    vocabulary = PromptVocabulary(entities=(), topics=(), events=(), recent=("固态电池",))
    assert render_vocabulary(vocabulary) == "近期新增：固态电池"


def test_empty_vocabulary_renders_a_fixed_note() -> None:
    vocabulary = PromptVocabulary(entities=(), topics=(), events=(), recent=())
    assert vocabulary.empty
    assert render_vocabulary(vocabulary) == EMPTY_VOCABULARY_TEXT


def test_terms_are_normalized_matching_keys() -> None:
    """注入的是写法，判定复用要按匹配键比：大小写与空白差异不该算成新建。"""

    vocabulary = PromptVocabulary(entities=("ChatGPT",), topics=("AI 助手",), events=(), recent=())
    assert vocabulary.terms == {"chatgpt", "ai助手"}


def test_fingerprint_changes_only_with_content() -> None:
    one = PromptVocabulary(entities=("ChatGPT",), topics=(), events=(), recent=())
    two = PromptVocabulary(entities=("ChatGPT", "Codex"), topics=(), events=(), recent=())
    assert one.fingerprint != two.fingerprint
