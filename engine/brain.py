    def procedure_feedback(self, name: str, success: bool) -> bool:
        return self.vault_manager.procedure_feedback(name, success)

    def update_frontmatter(self, entity_name: str, updates: dict) -> dict:
        """Merge `updates` into an entity file's YAML frontmatter.

        Thin wrapper over VaultManager.update_frontmatter. Used by automation
        (e.g. promotion scanners) to tag entities without editing body content
        or bypassing the engine.
        """
        return self.vault_manager.update_frontmatter(entity_name, updates)
