"""Template document parser with explicit @section marker and variable extraction (PLAN-010)."""

from __future__ import annotations

import re
from typing import Any

from engramory.core.parsers.base import (
    BaseParser,
    DocType,
    ParseResult,
    Section,
)


class TemplateParser(BaseParser):
    """Parser for documents with explicit section markers (@section: <name>)."""

    supported_extensions = ["template", "tmpl", "md", "txt"]
    doc_type = DocType.TEMPLATE

    PATTERNS = {
        "html_section": re.compile(r"<!--\s*@section:\s*([a-zA-Z0-9_\-\s]+?)\s*-->", re.IGNORECASE),
        "hash_section": re.compile(
            r"^#\s*@section:\s*([a-zA-Z0-9_\-\s]+?)\s*$", re.MULTILINE | re.IGNORECASE
        ),
        "plain_section": re.compile(
            r"^@section:\s*([a-zA-Z0-9_\-\s]+?)\s*$", re.MULTILINE | re.IGNORECASE
        ),
        "yaml_section": re.compile(
            r"^---\s*section:\s*([a-zA-Z0-9_\-\s]+?)\s*---\s*$", re.MULTILINE | re.IGNORECASE
        ),
        "html_end": re.compile(r"<!--\s*@end\s*-->", re.IGNORECASE),
        "hash_end": re.compile(r"^#\s*@end\s*$", re.MULTILINE | re.IGNORECASE),
        "plain_end": re.compile(r"^@end\s*$", re.MULTILINE | re.IGNORECASE),
    }

    VARIABLE_PATTERN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

    def parse(self, content: str | bytes, filename: str | None = None) -> ParseResult:
        """Parse template content into sections delineated by markers."""
        if isinstance(content, bytes):
            text = content.decode("utf-8", errors="replace")
        else:
            text = content

        text = text.replace("\r\n", "\n").replace("\r", "\n")

        markers = self._find_markers(text)

        if not markers:
            # Fallback if no markers
            sec = Section(
                section_id="sec_1",
                name="Template Content",
                order=1,
                level=1,
                content=text.strip(),
                citation="§template-content",
                char_start=0,
                char_end=len(text),
                source_tag="none",
            )
            return ParseResult(
                sections=[sec],
                doc_type=self.doc_type,
                title=filename or "Template",
                warnings=["No @section markers found in template document"],
            )

        sections: list[Section] = []
        variables_found: set[str] = set()

        for i, m in enumerate(markers):
            name = m["name"]
            order = i + 1
            sec_start = m["end"]  # Content begins after marker
            sec_end = markers[i + 1]["start"] if i + 1 < len(markers) else len(text)

            # Check if there is an explicit @end marker in this region
            end_match = self._find_first_end_marker(text, sec_start, sec_end)
            if end_match:
                sec_end = end_match

            sec_content = text[sec_start:sec_end].strip()

            # Extract {var} placeholders
            for v in self.VARIABLE_PATTERN.findall(sec_content):
                variables_found.add(v)

            sections.append(
                Section(
                    section_id=self._generate_section_id(order),
                    name=name,
                    order=order,
                    level=1,
                    content=sec_content,
                    char_start=sec_start,
                    char_end=sec_end,
                    source_tag=m["source_tag"],
                    metadata={"variables": list(self.VARIABLE_PATTERN.findall(sec_content))},
                )
            )

        self._link_subsections(sections)

        metadata: dict[str, Any] = {
            "template_variables": sorted(variables_found),
            "marker_count": len(markers),
        }

        return ParseResult(
            sections=sections,
            doc_type=self.doc_type,
            title=filename or (sections[0].name if sections else "Template"),
            metadata=metadata,
        )

    def _find_markers(self, text: str) -> list[dict[str, Any]]:
        markers: list[dict[str, Any]] = []

        for p_name in ["html_section", "hash_section", "plain_section", "yaml_section"]:
            pat = self.PATTERNS[p_name]
            for match in pat.finditer(text):
                name = match.group(1).strip()
                markers.append(
                    {
                        "name": name,
                        "start": match.start(),
                        "end": match.end(),
                        "source_tag": p_name,
                    }
                )

        markers.sort(key=lambda m: m["start"])
        return markers

    def _find_first_end_marker(self, text: str, start: int, end: int) -> int | None:
        sub = text[start:end]
        earliest: int | None = None
        for e_name in ["html_end", "hash_end", "plain_end"]:
            pat = self.PATTERNS[e_name]
            m = pat.search(sub)
            if m:
                pos = start + m.start()
                if earliest is None or pos < earliest:
                    earliest = pos
        return earliest
