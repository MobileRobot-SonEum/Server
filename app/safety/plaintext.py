"""마크다운·이모지를 일반 텍스트로 바꾼다.

- 교사 모드: 마크다운 기호를 지우고, 글머리 목록은 "1) 2)"로 바꾼다 (줄바꿈 유지).
- 아이 모드(temi가 읽음): 기호·이모지까지 지우고 한 줄로 합친다.
"""

from __future__ import annotations

import re

_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_HEADER = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_BULLET = re.compile(r"^\s*(?:[-*+•·]|\d+[.)])\s+(.*)$")
_EMPHASIS = re.compile(r"(\*\*|__|\*|`+|~~)")
_QUOTE = re.compile(r"^\s*>\s?", re.MULTILINE)
_HR = re.compile(r"^\s*([-*_])\1{2,}\s*$", re.MULTILINE)
# 이모지와 그림 기호 (말로 읽을 수 없는 것)
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF\U00002B00-\U00002BFF"
    "️‍←-⇿⌀-⏿■-◿✀-➿☀-⛿❤♥♡★☆]"
)
_SPEECH_SYMBOLS = re.compile(r"[#*_`~^|<>{}\[\]\\=+@$%&♡♥★☆※→←↑↓]")

MARKDOWN_HINT = re.compile(r"(\*\*|__|^\s*#{1,6}\s|^\s*[-*+]\s|`|\[[^\]]+\]\([^)]+\))", re.MULTILINE)


def has_markdown(text: str | None) -> bool:
    return bool(text) and bool(MARKDOWN_HINT.search(text))


def has_emoji(text: str | None) -> bool:
    return bool(text) and bool(_EMOJI.search(text))


def to_plain_text(text: str | None) -> str:
    """교사 모드용."""
    if not text:
        return ""
    t = _LINK.sub(r"\1", text)
    t = _HR.sub("", t)
    t = _HEADER.sub("", t)
    t = _QUOTE.sub("", t)
    lines, n = [], 0
    for line in t.splitlines():
        m = _BULLET.match(line)
        if m:
            n += 1
            line = f"{n}) {m.group(1)}"
        elif line.strip():
            n = 0
        lines.append(_EMPHASIS.sub("", line).rstrip())
    t = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def to_speech_text(text: str | None) -> str:
    """아이 모드용: 기호·이모지 없이 한 줄."""
    if not text:
        return ""
    t = _LINK.sub(r"\1", text)
    t = _HEADER.sub("", t)
    t = "\n".join((_BULLET.match(l).group(1) if _BULLET.match(l) else l) for l in t.splitlines())  # noqa: E741
    t = _EMOJI.sub("", t)
    t = _SPEECH_SYMBOLS.sub("", t)
    return re.sub(r"\s+", " ", t).strip()


# 한자·가나 (qwen 계열이 가끔 중국어로 답함)
_FOREIGN_SCRIPT = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")


def has_foreign_script(text: str | None) -> bool:
    return bool(text) and bool(_FOREIGN_SCRIPT.search(text))


# 로봇 이름 표기: 모델이 '_temii', '_tempi', 'temi'처럼 쓰는 것을 '테미'로
_NAME_GARBLE = re.compile(r"_+\s*t[a-z]{2,6}", re.IGNORECASE)
_NAME_LATIN = re.compile(r"(?<![A-Za-z])temi(?![A-Za-z])", re.IGNORECASE)


def normalize_robot_name(text: str | None) -> str:
    if not text:
        return ""
    t = _NAME_GARBLE.sub("테미", text)
    return _NAME_LATIN.sub("테미", t)
