"""生成时的形态过滤：只判定"这句写法能不能被人用来检索"，不判定语义。"""

import pytest

from rss_v2.domain import keyword_rejection_reason


@pytest.mark.parametrize(
    "text",
    [
        # 真实数据里的价格、数量与指标：只剩数字与单位。
        "19.98万元",
        "17.18万-21.78万",
        "2250千瓦",
        "1612N",
        "2027款",
        "2027",
        # 句子式写法：带了句读标点。
        "Day 9/ We've rolled out optimizations, and more",
        "华为发布新品，售价公布",
        "尊界S800 交付；销量破万",
        # 超长：多半是把一句话塞了进来。
        "day9优化chatgpt与codex桌面应用磁盘存储",
    ],
)
def test_rejects_non_searchable_shapes(text: str) -> None:
    assert keyword_rejection_reason(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        # 具名实体与型号：带数字也要保留。
        "华为乾崑ADS 5",
        "GPT-6.1 Sol",
        "800V高压平台",
        "2400MPa热成型钢",
        "理想MEGA",
        "新一代理想MEGA",
        "ChatGPT",
        "OpenAI",
        "AI 编程助手",
        "尊界V800",
    ],
)
def test_keeps_searchable_shapes(text: str) -> None:
    assert keyword_rejection_reason(text) is None


def test_boundary_keeps_short_suffix_forms() -> None:
    """规则故意不覆盖「19.99万元起」这类带后缀的价格。

    要剔除它就得把"单位词"列成表，那张表迟早误伤「GPT-4」这类真型号；这类写法交给
    提示词约束，并由热词门槛挡在热词表之外——形态规则只负责明显的那几类。
    """

    assert keyword_rejection_reason("19.99万元起") is None


def test_reason_is_readable() -> None:
    """原因要写给人看：日志里靠它判断是规则太松还是太紧。"""

    assert keyword_rejection_reason("19.98万元") == "只有数值与单位"
    assert keyword_rejection_reason("华为发布新品，售价公布") == "句子式写法"
