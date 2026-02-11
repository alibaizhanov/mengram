import sys
"""
Vault Manager v2 — создаёт/обновляет .md файлы с Rich Knowledge.

Секции в .md файле:
  ## Facts — короткие факты
  ## Relations — связи с другими entities
  ## Knowledge — решения, формулы, рецепты, конфиги (с артефактами)
"""

import re
from pathlib import Path
from datetime import datetime
from typing import Optional

import yaml

from engine.extractor.conversation_extractor import (
    ExtractionResult,
    ExtractedEntity,
    ExtractedRelation,
    ExtractedKnowledge,
)


class VaultManager:
    """Управляет Obsidian vault"""

    def __init__(self, vault_path: str):
        self.vault_path = Path(vault_path)
        self.vault_path.mkdir(parents=True, exist_ok=True)
        print(f"📁 Vault: {self.vault_path.absolute()}", file=sys.stderr)

    def process_extraction(self, extraction: ExtractionResult) -> dict:
        """
        Главный метод — обрабатывает извлечённые знания.
        Создаёт/обновляет файлы.
        """
        stats = {"created": [], "updated": []}

        # 1. Обрабатываем entities
        for entity in extraction.entities:
            file_path = self._entity_file_path(entity.name)

            entity_relations = [
                r for r in extraction.relations
                if r.from_entity == entity.name or r.to_entity == entity.name
            ]

            entity_knowledge = [
                k for k in extraction.knowledge
                if k.entity == entity.name
            ]

            if file_path.exists():
                self._update_note(file_path, entity, entity_relations, entity_knowledge)
                stats["updated"].append(entity.name)
            else:
                self._create_note(file_path, entity, entity_relations, entity_knowledge)
                stats["created"].append(entity.name)

        # 2. Knowledge для entities которые ещё не обработаны
        all_entity_names = {e.name for e in extraction.entities}
        for k in extraction.knowledge:
            if k.entity and k.entity not in all_entity_names:
                file_path = self._entity_file_path(k.entity)
                if file_path.exists():
                    self._append_knowledge(file_path, [k])
                    if k.entity not in stats["updated"]:
                        stats["updated"].append(k.entity)
                else:
                    stub = ExtractedEntity(name=k.entity, entity_type="concept", facts=[])
                    self._create_note(file_path, stub, [], [k])
                    stats["created"].append(k.entity)
                    all_entity_names.add(k.entity)

        # 3. Stub files for entities only in relations
        for rel in extraction.relations:
            for name in (rel.from_entity, rel.to_entity):
                if name not in all_entity_names:
                    file_path = self._entity_file_path(name)
                    if not file_path.exists():
                        stub = ExtractedEntity(name=name, entity_type="concept", facts=[])
                        self._create_note(file_path, stub, [], [])
                        stats["created"].append(name)
                        all_entity_names.add(name)

        return stats

    def _create_note(self, file_path: Path, entity: ExtractedEntity,
                     relations: list[ExtractedRelation],
                     knowledge: list[ExtractedKnowledge] = None):
        """Создаёт новый .md файл"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        frontmatter = {
            "type": entity.entity_type,
            "created": now,
            "updated": now,
            "tags": [entity.entity_type],
        }

        lines = []
        lines.append(f"# {entity.name}\n")

        # Facts
        if entity.facts:
            lines.append("## Facts\n")
            for fact in entity.facts:
                linked = self._add_wikilinks(fact, entity.name)
                lines.append(f"- {linked}")
            lines.append("")

        # Relations
        if relations:
            lines.append("## Relations\n")
            for rel in relations:
                other = rel.to_entity if rel.from_entity == entity.name else rel.from_entity
                direction = "→" if rel.from_entity == entity.name else "←"
                desc = f": {rel.description}" if rel.description else ""
                lines.append(f"- {direction} **{rel.relation_type}** [[{other}]]{desc}")
            lines.append("")

        # Knowledge
        if knowledge:
            lines.append("## Knowledge\n")
            for k in knowledge:
                lines.append(self._format_knowledge_entry(k))
            lines.append("")

        content = self._format_with_frontmatter(frontmatter, "\n".join(lines))
        file_path.write_text(content, encoding="utf-8")

    def _update_note(self, file_path: Path, entity: ExtractedEntity,
                     relations: list[ExtractedRelation],
                     knowledge: list[ExtractedKnowledge] = None):
        """Обновляет существующий .md файл"""
        content = file_path.read_text(encoding="utf-8")
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        frontmatter, body = self._parse_frontmatter(content)
        frontmatter["updated"] = now

        existing_facts = self._extract_existing_facts(body)

        # New facts
        new_facts = []
        for fact in entity.facts:
            if not self._fact_exists(fact, existing_facts):
                new_facts.append(fact)

        if new_facts:
            if "## Facts" in body:
                # Append to existing Facts section
                insert_pos = body.find("## Facts")
                # Find end of Facts section (next ## or end)
                next_section = self._find_next_section(body, insert_pos + 1)
                insert_at = next_section if next_section else len(body)
                new_lines = ""
                for fact in new_facts:
                    linked = self._add_wikilinks(fact, entity.name)
                    new_lines += f"- {linked}\n"
                body = body[:insert_at].rstrip() + "\n" + new_lines + "\n" + body[insert_at:]
            else:
                body = body.rstrip() + "\n\n## Facts\n\n"
                for fact in new_facts:
                    linked = self._add_wikilinks(fact, entity.name)
                    body += f"- {linked}\n"

        # New relations
        existing_links = set(re.findall(r"\[\[([^\]]+)\]\]", body))
        new_rels = [r for r in relations
                    if (r.to_entity if r.from_entity == entity.name else r.from_entity) not in existing_links]
        if new_rels:
            if "## Relations" in body:
                insert_pos = body.find("## Relations")
                next_section = self._find_next_section(body, insert_pos + 1)
                insert_at = next_section if next_section else len(body)
                new_lines = ""
                for rel in new_rels:
                    other = rel.to_entity if rel.from_entity == entity.name else rel.from_entity
                    direction = "→" if rel.from_entity == entity.name else "←"
                    desc = f": {rel.description}" if rel.description else ""
                    new_lines += f"- {direction} **{rel.relation_type}** [[{other}]]{desc}\n"
                body = body[:insert_at].rstrip() + "\n" + new_lines + "\n" + body[insert_at:]
            else:
                body = body.rstrip() + "\n\n## Relations\n\n"
                for rel in new_rels:
                    other = rel.to_entity if rel.from_entity == entity.name else rel.from_entity
                    direction = "→" if rel.from_entity == entity.name else "←"
                    desc = f": {rel.description}" if rel.description else ""
                    body += f"- {direction} **{rel.relation_type}** [[{other}]]{desc}\n"

        # Knowledge
        if knowledge:
            self._write_with_knowledge(file_path, frontmatter, body, knowledge)
            return

        if new_facts or new_rels:
            content = self._format_with_frontmatter(frontmatter, body)
            file_path.write_text(content, encoding="utf-8")

    def _append_knowledge(self, file_path: Path, knowledge: list[ExtractedKnowledge]):
        """Добавляет knowledge к существующему файлу"""
        content = file_path.read_text(encoding="utf-8")
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        frontmatter, body = self._parse_frontmatter(content)
        frontmatter["updated"] = now
        self._write_with_knowledge(file_path, frontmatter, body, knowledge)

    def _write_with_knowledge(self, file_path: Path, frontmatter: dict,
                              body: str, knowledge: list[ExtractedKnowledge]):
        """Записывает файл с новыми knowledge entries"""
        existing_titles = set(re.findall(r"\*\*\[[\w]+\]\s+(.+?)\*\*", body))

        new_knowledge = [k for k in knowledge if k.title not in existing_titles]
        if not new_knowledge:
            content = self._format_with_frontmatter(frontmatter, body)
            file_path.write_text(content, encoding="utf-8")
            return

        if "## Knowledge" in body:
            # Append to existing Knowledge section
            for k in new_knowledge:
                body = body.rstrip() + "\n\n" + self._format_knowledge_entry(k)
        else:
            body = body.rstrip() + "\n\n## Knowledge\n\n"
            for k in new_knowledge:
                body += self._format_knowledge_entry(k) + "\n"

        content = self._format_with_frontmatter(frontmatter, body)
        file_path.write_text(content, encoding="utf-8")

    def _format_knowledge_entry(self, k: ExtractedKnowledge) -> str:
        """Форматирует одну knowledge entry"""
        now = datetime.now().strftime("%Y-%m-%d")
        entity_name = k.entity

        lines = []
        lines.append(f"**[{k.knowledge_type}] {k.title}** ({now})")
        
        # Content with wikilinks
        linked_content = self._add_wikilinks(k.content, entity_name)
        lines.append(linked_content)

        # Artifact (code block)
        if k.artifact:
            artifact = k.artifact.strip()
            # Auto-detect language for code block
            lang = self._detect_artifact_lang(artifact, k.knowledge_type)
            lines.append(f"\n```{lang}")
            lines.append(artifact)
            lines.append("```")

        lines.append("")  # blank line after entry
        return "\n".join(lines)

    def _detect_artifact_lang(self, artifact: str, knowledge_type: str) -> str:
        """Определяет язык для code block"""
        # By content
        if artifact.strip().startswith("SELECT") or artifact.strip().startswith("select"):
            return "sql"
        if artifact.strip().startswith("{") or artifact.strip().startswith("["):
            return "json"
        if artifact.strip().startswith("<"):
            return "xml"
        if artifact.strip().startswith("def ") or artifact.strip().startswith("import "):
            return "python"
        if artifact.strip().startswith("public ") or artifact.strip().startswith("private "):
            return "java"
        if ":" in artifact and not artifact.strip().startswith("http"):
            return "yaml"
        if artifact.strip().startswith("$") or artifact.strip().startswith("#!"):
            return "bash"

        # By knowledge type
        type_map = {
            "command": "bash",
            "config": "yaml",
            "formula": "math",
            "sql": "sql",
        }
        return type_map.get(knowledge_type, "")

    def _find_next_section(self, body: str, start: int) -> Optional[int]:
        """Находит начало следующей ## секции"""
        match = re.search(r"\n## ", body[start:])
        if match:
            return start + match.start()
        return None

    # --- Existing helpers (unchanged) ---

    def _add_wikilinks(self, text: str, current_entity: str) -> str:
        existing_notes = {p.stem for p in self.vault_path.glob("*.md")}
        for note_name in existing_notes:
            if note_name == current_entity:
                continue
            pattern = re.compile(re.escape(note_name), re.IGNORECASE)
            if f"[[{note_name}]]" not in text:
                text = pattern.sub(f"[[{note_name}]]", text, count=1)
        return text

    def _entity_file_path(self, entity_name: str) -> Path:
        safe_name = re.sub(r'[<>:"/\\|?*]', '_', entity_name)
        return self.vault_path / f"{safe_name}.md"

    def _format_with_frontmatter(self, frontmatter: dict, body: str) -> str:
        fm_str = yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False).strip()
        return f"---\n{fm_str}\n---\n\n{body.strip()}\n"

    def _parse_frontmatter(self, content: str) -> tuple[dict, str]:
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if not match:
            return {}, content
        try:
            fm = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            fm = {}
        body = content[match.end():]
        return fm, body

    def _extract_existing_facts(self, body: str) -> list[str]:
        facts = []
        for line in body.split("\n"):
            line = line.strip()
            if line.startswith("- ") and "**" not in line and "[" not in line[:3]:
                clean = re.sub(r"\[\[([^\]]+)\]\]", r"\1", line[2:])
                facts.append(clean.lower().strip())
        return facts

    def _fact_exists(self, new_fact: str, existing_facts: list[str]) -> bool:
        new_clean = new_fact.lower().strip()
        for existing in existing_facts:
            new_words = set(new_clean.split())
            existing_words = set(existing.split())
            if not new_words:
                continue
            overlap = len(new_words & existing_words) / len(new_words)
            if overlap > 0.7:
                return True
        return False

    def get_vault_stats(self) -> dict:
        files = list(self.vault_path.glob("*.md"))
        types = {}
        knowledge_count = 0
        for f in files:
            content = f.read_text(encoding="utf-8")
            fm, body = self._parse_frontmatter(content)
            t = fm.get("type", "unknown")
            types[t] = types.get(t, 0) + 1
            knowledge_count += len(re.findall(r"\*\*\[\w+\]", body))
        return {
            "total_notes": len(files),
            "by_type": types,
            "knowledge_entries": knowledge_count,
        }

    def list_notes(self) -> list[str]:
        return sorted([p.stem for p in self.vault_path.glob("*.md")])
