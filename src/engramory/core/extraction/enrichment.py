"""Semantic chunking and text normalization utilities (PLAN-010 Phase 2)."""

from __future__ import annotations

from engramory.core.parsers.base import Section


class SemanticChunker:
    """Refines section chunks by merging undersized sections and splitting oversized chunks."""

    def __init__(
        self,
        min_words: int = 30,
        max_words: int = 400,
    ) -> None:
        self.min_words = min_words
        self.max_words = max_words

    def refine_sections(self, sections: list[Section]) -> list[Section]:
        """Refine list of sections by merging tiny fragments and splitting oversized sections."""
        if not sections:
            return []

        # Pass 1: Split oversized sections
        split_sections: list[Section] = []
        for s in sections:
            words = s.content.split()
            if len(words) <= self.max_words:
                split_sections.append(s)
            else:
                base_citation = s.citation or "§section"
                for i in range(0, len(words), self.max_words):
                    part_num = (i // self.max_words) + 1
                    part_words = words[i : i + self.max_words]
                    part_text = " ".join(part_words)
                    sub_citation = f"{base_citation}-p{part_num}" if part_num > 1 else base_citation

                    split_sections.append(
                        Section(
                            section_id=f"{s.section_id}_p{part_num}",
                            name=f"{s.name} (Part {part_num})" if part_num > 1 else s.name,
                            order=len(split_sections) + 1,
                            level=s.level,
                            content=part_text,
                            parent_id=s.parent_id,
                            citation=sub_citation,
                            char_start=s.char_start,
                            char_end=s.char_end,
                            page_start=s.page_start,
                            page_end=s.page_end,
                            source_tag=s.source_tag,
                            metadata=dict(s.metadata),
                        )
                    )

        # Pass 2: Merge undersized sections with previous section if same level or sub-section
        merged_sections: list[Section] = []
        for s in split_sections:
            words = s.content.split()
            if (
                len(words) < self.min_words
                and merged_sections
                and not s.content.startswith("[Doc: ")
                and not merged_sections[-1].content.startswith("[Doc: ")
                and (len(merged_sections[-1].content.split()) + len(words)) <= self.max_words
            ):
                # Merge into previous section
                prev = merged_sections[-1]
                prev.content = f"{prev.content}\n\n{s.content}"
                prev.char_end = max(prev.char_end, s.char_end)
                if s.page_end:
                    prev.page_end = s.page_end
            else:
                merged_sections.append(s)

        # Renumber order sequentially
        for idx, s in enumerate(merged_sections, 1):
            s.order = idx
            s.section_id = f"sec_{idx}"

        return merged_sections
