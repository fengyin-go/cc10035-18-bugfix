"""按真实采集片段切分 HTML 纯文本。

这里的唯一职责是把一次采集到的 HTML 还原成文档顺序上的片段列表，
不做任何补全：没有正文就返回空列表，绝不生成示例块。
"""
from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from typing import Any


# 需要独立成块的标签：块类型既用于分块清单展示，也用于情感分析前的语义判断。
BLOCK_TAGS: dict[str, str] = {
    "h1": "heading",
    "h2": "heading",
    "h3": "heading",
    "h4": "heading",
    "h5": "heading",
    "h6": "heading",
    "p": "paragraph",
    "li": "list_item",
    "blockquote": "quote",
    "pre": "code_block",
    "td": "table_cell",
    "th": "table_cell",
}

# 这些标签里的文字不属于正文，不能作为可分析文本。
IGNORED_TAGS = {
    "head", "title", "meta", "link", "base", "script", "style",
    "noscript", "template", "svg", "canvas", "iframe",
}

# 容器标签只是视觉分段：不单独成块，但要把裸文本切成自然段。
SEPARATOR_TAGS = {"div", "section", "article", "main", "header", "footer", "aside", "nav", "dd"}

CJK_RE = re.compile(r"[一-鿿]")


def _clean_text(text: str, *, preserve_newlines: bool = False) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if preserve_newlines:
        lines = [" ".join(line.split()) for line in text.split("\n")]
        return "\n".join(line for line in lines if line)
    return " ".join(text.split())


def _count_ordinals(fragments: list[dict[str, Any]]) -> None:
    """给片段补一个稳定的同标签序号，便于核查片段出处。"""
    counters: dict[str, int] = {}
    for fragment in fragments:
        tag = fragment["tag"]
        counters[tag] = counters.get(tag, 0) + 1
        fragment["ordinal"] = counters[tag]
        fragment["selector"] = f"{tag}:nth-of-type({counters[tag]})"


class _SegmentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fragments: list[dict[str, Any]] = []
        self._buffer: list[str] = []
        self._current_tag: str | None = None
        self._current_type: str | None = None
        self._ignored_depth = 0

    # ---- 内部工具 ----
    def _buffer_text(self) -> str:
        return "".join(self._buffer)

    def _flush(self) -> None:
        if self._current_tag is None:
            # 裸文本或 div/section 里的文字：按空行拆成自然段。
            raw = self._buffer_text()
            self._buffer = []
            for chunk in re.split(r"\n\s*\n", raw):
                text = _clean_text(chunk)
                if text:
                    self.fragments.append({
                        "tag": "text",
                        "block_type": "paragraph",
                        "text": text,
                    })
            return

        preserve = self._current_type == "code_block"
        text = _clean_text(self._buffer_text(), preserve_newlines=preserve)
        self._buffer = []
        if text:
            self.fragments.append({
                "tag": self._current_tag,
                "block_type": self._current_type or "paragraph",
                "text": text,
            })

    # ---- HTMLParser 回调 ----
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag == "br":
            self._buffer.append("\n")
            return
        block_type = BLOCK_TAGS.get(tag)
        if block_type is None:
            if tag in SEPARATOR_TAGS:
                # 容器边界也是段落边界：先收掉前面的裸文本，别把两个 div 粘成一段。
                self._flush()
                self._current_tag = None
                self._current_type = None
            return
        # 遇到新的块级标签时，先结束上一段，保证片段和文档顺序一致。
        if self._current_tag is not None or _clean_text(self._buffer_text()):
            self._flush()
        self._current_tag = tag
        self._current_type = block_type

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in IGNORED_TAGS:
            return
        if tag == "br":
            self._buffer.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in IGNORED_TAGS:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return
        if self._ignored_depth:
            return
        if tag in BLOCK_TAGS and self._current_tag == tag:
            self._flush()
            self._current_tag = None
            self._current_type = None

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        self._buffer.append(data)

    def close(self) -> None:
        super().close()
        self._flush()
        self._current_tag = None
        self._current_type = None


def extract_fragments(raw_html: str | None) -> list[dict[str, Any]]:
    """把 HTML 切分为真实采集片段；空 HTML 返回空列表。"""
    if not raw_html or not raw_html.strip():
        return []
    parser = _SegmentParser()
    try:
        parser.feed(raw_html)
        parser.close()
    except Exception:
        # 采集内容可能被截断，能解析出多少正文就用多少，不补示例。
        parser.fragments = parser.fragments
    fragments = [
        fragment for fragment in parser.fragments
        if str(fragment.get("text") or "").strip()
    ]
    _count_ordinals(fragments)
    return fragments


def build_capture_text(fragments: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """给片段标上在整篇正文中的偏移，返回规范化正文与带偏移的片段。"""
    cursor = 0
    positioned: list[dict[str, Any]] = []
    for index, fragment in enumerate(fragments):
        text = str(fragment["text"])
        if positioned:
            cursor += 2  # 片段之间用两个换行分隔
        start = cursor
        end = start + len(text)
        cursor = end
        positioned.append({
            **fragment,
            "index": index,
            "start": start,
            "end": end,
            "char_length": len(text),
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        })
    full_text = "\n\n".join(item["text"] for item in positioned)
    return full_text, positioned


def detect_language(text: str) -> str:
    return "zh-CN" if CJK_RE.search(text) else "en"
