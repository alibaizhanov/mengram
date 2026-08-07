    def list_notes(self) -> list[str]:
        return sorted([p.stem for p in self.vault_path.glob("*.md")])

    def update_frontmatter(self, entity_name: str, updates: dict) -> dict:
        """Merge `updates` into an entity file's YAML frontmatter without touching body content.

        Returns the resulting frontmatter dict. Raises FileNotFoundError if the
        entity file does not exist.
        """
        file_path = self._entity_file_path(entity_name)
        if not file_path.exists():
            raise FileNotFoundError(f"Entity '{entity_name}' not found at {file_path}")

        content = file_path.read_text(encoding="utf-8")
        frontmatter, body = self._parse_frontmatter(content)
        frontmatter.update(updates)

        new_content = self._format_with_frontmatter(frontmatter, body)
        file_path.write_text(new_content, encoding="utf-8")
        return frontmatter
