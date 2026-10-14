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
        "2400MPa",
        "2027",
        "19.98 万元",
        "约 5000 元",
        # 带后缀的价格：单位表覆盖了「起」「约」这类常见后缀。
        "19.99万元起",
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
        "H100",
        "A100",
        "B200",
        "V100",
        "5G",
        "RTX5090",
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


def test_boundary_keeps_single_letter_suffix_forms() -> None:
    """单字母单位（N/V/A/W）刻意不进单位表：它们与型号撞名。

    代价是「1612N」这类写法留在词表里（由门槛与人工合并收尾），换来的是 A100、V100
    不会被误删——型号的检索价值高得多。「2027款」同理：款不是单位，形态上是"数字+词"。
    """

    assert keyword_rejection_reason("1612N") is None
    assert keyword_rejection_reason("2027款") is None
    # 纯数字仍在拒绝范围内。
    assert keyword_rejection_reason("2027") == "只有数值与单位"


def test_reason_is_readable() -> None:
    """原因要写给人看：日志里靠它判断是规则太松还是太紧。"""

    assert keyword_rejection_reason("19.98万元") == "只有数值与单位"
    assert keyword_rejection_reason("华为发布新品，售价公布") == "句子式写法"
