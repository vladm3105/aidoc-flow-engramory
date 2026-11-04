"""HTML document parser with stdlib and optional BeautifulSoup support (PLAN-010)."""

from __future__ import annotations

import re
from html.parser import HTMLParser as StdlibHTMLParser

from engramory.core.parsers.base import (
    BaseParser,
    DocType,
    ParseResult,
    Section,
)

try:
    from bs4 import BeautifulSoup

    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False


class _SimpleHTMLTextExtractor(StdlibHTMLParser):
    """Stdlib-based HTML text and header extractor."""

    HEADER_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
    IGNORE_TAGS = {"script", "style", "noscript", "svg", "head"}

    def __init__(self) -> None:
        super().__init__()
        self.text_parts: list[str] = []
        self.headers: list[tuple[int, str, int]] = []  # (level, text, start_pos)
        self.current_header_level: int | None = None
        self.current_header_text: list[str] = []
        self.current_header_start = 0
        self.ignore_depth = 0
        self.current_pos = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in self.IGNORE_TAGS:
            self.ignore_depth += 1
            return
        if self.ignore_depth > 0:
            return
        if t in self.HEADER_TAGS:
            self.current_header_level = self.HEADER_TAGS[t]
            self.current_header_text = []
            self.current_header_start = self.current_pos

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in self.IGNORE_TAGS:
            self.ignore_depth = max(0, self.ignore_depth - 1)
            return
        if self.ignore_depth > 0:
            return
        if t in self.HEADER_TAGS and self.current_header_level is not None:
            header_name = "".join(self.current_header_text).strip()
            if header_name:
                self.headers.append(
                    (self.current_header_level, header_name, self.current_header_start)
                )
            self.current_header_level = None
            self.current_header_text = []

    def handle_data(self, data: str) -> None:
        if self.ignore_depth > 0:
            return
        if self.current_header_level is not None:
            self.current_header_text.append(data)
        self.text_parts.append(data)
        self.current_pos += len(data)


class HTMLParser(BaseParser):
    """Parser for HTML documents with semantic element and header hierarchy support."""

    supported_extensions = ["html", "htm", "xhtml"]
    doc_type = DocType.HTML

    HEADER_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

    def parse(self, content: str | bytes, filename: str | None = None) -> ParseResult:
        """Parse HTML into structured sections."""
        if isinstance(content, bytes):
            text = content.decode("utf-8", errors="replace")
        else:
            text = content

        if HAS_BS4:
            return self._parse_with_bs4(text, filename)
        return self._parse_with_stdlib(text, filename)

    def _parse_with_bs4(self, text: str, filename: str | None) -> ParseResult:
        soup = BeautifulSoup(text, "html.parser")

        # Decompose non-content elements
        for el in soup(["script", "style", "noscript", "svg"]):
            el.decompose()

        # Extract title
        title_el = soup.find("title")
        title = title_el.get_text(strip=True) if title_el else filename

        # Find header elements
        header_nodes = soup.find_all(list(self.HEADER_TAGS.keys()))
        sections: list[Section] = []

        if not header_nodes:
            body_text = soup.get_text(separator="\n", strip=True)
            sec = Section(
                section_id="sec_1",
                name="Content",
                order=1,
                level=1,
                content=body_text,
                char_start=0,
                char_end=len(text),
                source_tag="body",
            )
            self._link_subsections([sec])
            return ParseResult(
                sections=[sec],
                doc_type=self.doc_type,
                title=title,
                warnings=["No header elements found in HTML"] if body_text else ["Empty document"],
            )

        for h_node in header_nodes:
            name = h_node.get_text(strip=True)
            if not name:
                continue
            level = self.HEADER_TAGS[h_node.name.lower()]
            order = len(sections) + 1

            # Accumulate text until next header
            content_parts: list[str] = [name]
            for sibling in h_node.next_siblings:
                if getattr(sibling, "name", None) in self.HEADER_TAGS:
                    break
                sibling_text = (
                    sibling.get_text(separator=" ", strip=True)
                    if hasattr(sibling, "get_text")
                    else str(sibling).strip()
                )
                if sibling_text:
                    content_parts.append(sibling_text)

            sec_content = "\n\n".join(content_parts)
            sections.append(
                Section(
                    section_id=self._generate_section_id(order),
                    name=name,
                    order=order,
                    level=level,
                    content=sec_content,
                    source_tag=h_node.name.lower(),
                )
            )

        self._link_subsections(sections)
        return ParseResult(
            sections=sections,
            doc_type=self.doc_type,
            title=title or self._extract_title(sections),
        )

    def _parse_with_stdlib(self, text: str, filename: str | None) -> ParseResult:
        extractor = _SimpleHTMLTextExtractor()
        try:
            extractor.feed(text)
        except Exception:
            pass

        full_text = "".join(extractor.text_parts)
        headers = extractor.headers

        # Extract title from title tags via regex
        title_match = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
        title = title_match.group(1).strip() if title_match else filename

        if not headers:
            body_clean = full_text.strip()
            sec = Section(
                section_id="sec_1",
                name="Content",
                order=1,
                level=1,
                content=body_clean,
                char_start=0,
                char_end=len(text),
                source_tag="body",
            )
            self._link_subsections([sec])
            return ParseResult(
                sections=[sec],
                doc_type=self.doc_type,
                title=title,
                warnings=["No header elements found in HTML"] if body_clean else ["Empty document"],
            )

        sections: list[Section] = []
        for i, (level, name, start_pos) in enumerate(headers):
            order = len(sections) + 1
            end_pos = headers[i + 1][2] if i + 1 < len(headers) else len(full_text)
            sec_content = full_text[start_pos:end_pos].strip()
            if not sec_content:
                sec_content = name

            sections.append(
                Section(
                    section_id=self._generate_section_id(order),
                    name=name,
                    order=order,
                    level=level,
                    content=sec_content,
                    source_tag=f"h{level}",
                )
            )

        self._link_subsections(sections)
        return ParseResult(
            sections=sections,
            doc_type=self.doc_type,
            title=title or self._extract_title(sections),
        )
