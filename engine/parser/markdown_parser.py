"""
Obsidian Markdown Parser

Parses .md files from Obsidian vault and extracts:
- Frontmatter (YAML metadata)
- Wikilinks ([[links]])
- Tags (#tags and from frontmatter)
- Headings (document structure)
- Text chunks (for embeddings)
"""

import re
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class WikiLink:
    """Wikilink [[target]] or [[target|alias]]"""
    target: str
    alias: Optional[str] = None
    context: str = ""  # sentence where the link was found

    def __repr__(self):
        if self.alias:
            return f"WikiLink({self.target} | {self.alias})"
        return f"WikiLink({self.target})"


@dataclass
class Section:
    """Document section (heading + content)"""
    title: str
    level: int  # 1 = #, 2 = ##, 3 = ###
    content: str

    def __repr__(self):
        return f"Section(L{self.level}: {self.title})"


@dataclass
class TextChunk:
    """Text chunk for vector embedding"""
    content: str
    section: str  # which section it belongs to
    position: int  # position in document

    def __repr__(self):
        preview = self.content[:50] + "..." if len(self.content) > 50 else self.content
        return f"Chunk({self.section}: {preview})"


@dataclass
class ParsedNote:
    """Result of parsing one .md note"""
    file_path: str
    title: str
    frontmatter: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    wikilinks: list[WikiLink] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    chunks: list[TextChunk] = field(default_factory=list)
    raw_content: str = ""

    @property
    def name(self) -> str:
        """Filename without extension = entity name"""
        return Path(self.file_path).stem

    def __repr__(self):
        return (
            f"ParsedNote(\n"
            f"  title={self.title}\n"
            f"  tags={self.tags}\n"
            f"  links={[l.target for l in self.wikilinks]}\n"
            f"  sections={len(self.sections)}\n"
            f"  chunks={len(self.chunks)}\n"
            f")"
        )


# Regex patterns
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
TAG_RE = re.compile(r"(?:^|\s)#([a-zA-Z\w\-/]+)", re.MULTILINE)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """
    Extracts YAML frontmatter from file beginning.
    Returns (metadata_dict, content_without_frontmatter)
    """
    match = FRONTMATTER_RE.match(content)
    if not match:
        return {}, content

    try:
        metadata = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        metadata = {}

    body = content[match.end():]
    return metadata, body


def extract_wikilinks(content: str) -> list[WikiLink]:
    """
    Extracts all [[wikilinks]] from text.
    Supports [[target]] and [[target|alias]] formats.
    """
    links = []
    for match in WIKILINK_RE.finditer(content):
        target = match.group(1).strip()
        alias = match.group(2).strip() if match.group(2) else None

        # Extract context — sentence around the link
        start = max(0, match.start() - 80)
        end = min(len(content), match.end() + 80)
        context = content[start:end].replace("\n", " ").strip()

        links.append(WikiLink(target=target, alias=alias, context=context))

    return links


def extract_tags(content: str, frontmatter: dict) -> list[str]:
    """
    Extracts tags from:
    1. Frontmatter (tags: [tag1, tag2])
    2. Inline #tags in text
    """
    tags = set()

    # From frontmatter
    fm_tags = frontmatter.get("tags", [])
    if isinstance(fm_tags, list):
        tags.update(fm_tags)
    elif isinstance(fm_tags, str):
        tags.add(fm_tags)

    # Inline tags
    for match in TAG_RE.finditer(content):
        tag = match.group(1)
        # Exclude headings (## is not a tag)
        if not tag.startswith("#"):
            tags.add(tag)

    return sorted(tags)


def extract_sections(content: str) -> list[Section]:
    """
    Splits document into sections by headings.
    """
    sections = []
    headings = list(HEADING_RE.finditer(content))

    if not headings:
        # No headings — entire content as one section
        stripped = content.strip()
        if stripped:
            sections.append(Section(title="(root)", level=0, content=stripped))
        return sections

    # Text before first heading
    pre_heading = content[:headings[0].start()].strip()
    if pre_heading:
        sections.append(Section(title="(intro)", level=0, content=pre_heading))

    # Each heading + content until next
    for i, heading in enumerate(headings):
        level = len(heading.group(1))
        title = heading.group(2).strip()

        # Content: from end of heading to start of next (or end of file)
        start = heading.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(content)
        body = content[start:end].strip()

        sections.append(Section(title=title, level=level, content=body))

    return sections


def create_chunks(sections: list[Section], chunk_size: int = 500) -> list[TextChunk]:
    """
    Creates text chunks for vector embeddings.
    Splits by sections, long sections — by paragraphs.

    chunk_size — approximate chunk size in characters.
    """
    chunks = []
    position = 0

    for section in sections:
        if not section.content:
            continue

        # If section is small — one chunk
        if len(section.content) <= chunk_size:
            chunk_text = section.content
            if section.title not in ("(root)", "(intro)"):
                chunk_text = f"{section.title}: {chunk_text}"

            chunks.append(TextChunk(
                content=chunk_text,
                section=section.title,
                position=position,
            ))
            position += 1
            continue

        # Long section — split by paragraphs
        paragraphs = re.split(r"\n\s*\n", section.content)
        current_chunk = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            if len(current_chunk) + len(para) > chunk_size and current_chunk:
                chunks.append(TextChunk(
                    content=current_chunk.strip(),
                    section=section.title,
                    position=position,
                ))
                position += 1
                current_chunk = para
            else:
                current_chunk += "\n\n" + para if current_chunk else para

        if current_chunk.strip():
            chunks.append(TextChunk(
                content=current_chunk.strip(),
                section=section.title,
                position=position,
            ))
            position += 1

    return chunks


def parse_note(file_path: str) -> ParsedNote:
    """
    Main function: parses one .md file into ParsedNote.
    """
    path = Path(file_path)
    content = path.read_text(encoding="utf-8")

    # 1. Frontmatter
    frontmatter, body = parse_frontmatter(content)

    # 2. Title — from H1 or filename
    h1_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
    title = h1_match.group(1).strip() if h1_match else path.stem

    # 3. Wikilinks
    wikilinks = extract_wikilinks(body)

    # 4. Tags
    tags = extract_tags(body, frontmatter)

    # 5. Sections
    sections = extract_sections(body)

    # 6. Chunks
    chunks = create_chunks(sections)

    return ParsedNote(
        file_path=str(path),
        title=title,
        frontmatter=frontmatter,
        tags=tags,
        wikilinks=wikilinks,
        sections=sections,
        chunks=chunks,
        raw_content=content,
    )


def parse_vault(vault_path: str) -> list[ParsedNote]:
    """
    Parses all .md files in vault.
    """
    vault = Path(vault_path)
    notes = []

    for md_file in sorted(vault.rglob("*.md")):
        # Skip hidden files and .obsidian folder
        if any(part.startswith(".") for part in md_file.parts):
            continue

        try:
            note = parse_note(str(md_file))
            notes.append(note)
        except Exception as e:
            print(f"⚠️  Parse error {md_file}: {e}", file=sys.stderr)

    return notes


# --- Entry point for testing ---
if __name__ == "__main__":
    import sys

    vault_path = sys.argv[1] if len(sys.argv) > 1 else "./test_vault"
    notes = parse_vault(vault_path)

    print(f"\n📚 Parsed {len(notes)} notes from vault\n")

    for note in notes:
        print(f"{'='*60}")
        print(note)
        print(f"  frontmatter: {note.frontmatter}")
        print(f"  chunks preview:")
        for chunk in note.chunks[:2]:
            print(f"    - {chunk}")
        print()
