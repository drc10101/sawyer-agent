"""Tests for Agent Creator / Templates."""

import pytest
from pathlib import Path

from sawyer_harness.agent_creator import AgentCreator, AgentTemplate, AgentSoul, DEFAULT_TEMPLATES


@pytest.fixture
def creator(tmp_path):
    """Create a fresh AgentCreator with a temp file."""
    path = tmp_path / "test_templates.yaml"
    return AgentCreator(path=path)


class TestAgentSoul:
    """Test AgentSoul dataclass."""

    def test_default_soul(self):
        soul = AgentSoul()
        assert soul.identity == ""
        assert soul.strengths == []
        assert soul.personality == []
        assert soul.values == []
        assert soul.quirks == []

    def test_soul_to_dict(self):
        soul = AgentSoul(
            identity="A focused coder",
            strengths=["debugging", "clean code"],
            personality=["concise", "pragmatic"],
            values=["working code over explanations"],
            quirks=["Never describes code -- writes it"],
        )
        d = soul.to_dict()
        assert d["identity"] == "A focused coder"
        assert len(d["strengths"]) == 2
        assert len(d["quirks"]) == 1

    def test_soul_from_dict(self):
        data = {
            "identity": "A researcher",
            "strengths": ["analysis"],
            "personality": ["thorough"],
            "values": ["citations always"],
            "quirks": ["digs deep"],
        }
        soul = AgentSoul.from_dict(data)
        assert soul.identity == "A researcher"
        assert soul.strengths == ["analysis"]
        assert soul.personality == ["thorough"]

    def test_soul_from_dict_legacy_string(self):
        """Backward compat: soul as plain string becomes identity-only."""
        soul = AgentSoul.from_dict("I am a legacy agent")
        assert soul.identity == "I am a legacy agent"
        assert soul.strengths == []

    def test_soul_from_dict_empty(self):
        soul = AgentSoul.from_dict({})
        assert soul.identity == ""
        assert soul.strengths == []

    def test_soul_prompt_section_empty(self):
        """Empty soul produces no prompt section."""
        soul = AgentSoul()
        assert soul.to_prompt_section() == ""

    def test_soul_prompt_section_identity_only(self):
        soul = AgentSoul(identity="I am a coder")
        prompt = soul.to_prompt_section()
        assert "## Who I Am" in prompt
        assert "I am a coder" in prompt

    def test_soul_prompt_section_full(self):
        soul = AgentSoul(
            identity="Sawyer -- your agent",
            strengths=["coding", "debugging"],
            personality=["direct", "thorough"],
            values=["done means done", "no patches"],
            quirks=["Never describes what should be done -- does it"],
        )
        prompt = soul.to_prompt_section()
        assert "## Who I Am" in prompt
        assert "Sawyer" in prompt
        assert "**Strengths:**" in prompt
        assert "coding, debugging" in prompt
        assert "**Personality:**" in prompt
        assert "**Values:**" in prompt
        assert "**Quirks:**" in prompt
        assert "- Never describes" in prompt

    def test_soul_roundtrip(self):
        soul = AgentSoul(
            identity="Test agent",
            strengths=["a", "b"],
            personality=["c"],
            values=["d"],
            quirks=["e", "f"],
        )
        d = soul.to_dict()
        restored = AgentSoul.from_dict(d)
        assert restored.identity == soul.identity
        assert restored.strengths == soul.strengths
        assert restored.personality == soul.personality
        assert restored.values == soul.values
        assert restored.quirks == soul.quirks


class TestAgentTemplate:
    """Test AgentTemplate dataclass."""

    def test_to_dict_roundtrip(self):
        tpl = AgentTemplate(
            id="test-agent",
            name="Test Agent",
            description="A test agent",
            system_prompt="You are a test agent.",
            model="gpt-4o",
            temperature=0.5,
            rules=[{"name": "Be good", "rule": "Always be good", "priority": "P1"}],
        )
        d = tpl.to_dict()
        assert d["id"] == "test-agent"
        assert d["model"] == "gpt-4o"
        assert len(d["rules"]) == 1

        restored = AgentTemplate.from_dict(d)
        assert restored.id == tpl.id
        assert restored.model == tpl.model
        assert len(restored.rules) == 1

    def test_default_values(self):
        tpl = AgentTemplate(id="x", name="X")
        assert tpl.temperature == 0.7
        assert tpl.max_tokens == 4096
        assert tpl.tools_enabled == "all"
        assert tpl.icon == "bot"
        assert tpl.category == "general"
        assert tpl.is_builtin is False
        assert isinstance(tpl.soul, AgentSoul)
        assert tpl.soul.identity == ""

    def test_template_with_soul(self):
        soul = AgentSoul(
            identity="A focused executor",
            strengths=["speed", "accuracy"],
            personality=["direct"],
        )
        tpl = AgentTemplate(
            id="soul-agent",
            name="Soul Agent",
            soul=soul,
        )
        d = tpl.to_dict()
        assert "soul" in d
        assert d["soul"]["identity"] == "A focused executor"

        restored = AgentTemplate.from_dict(d)
        assert restored.soul.identity == "A focused executor"
        assert restored.soul.strengths == ["speed", "accuracy"]

    def test_template_soul_roundtrip_yaml(self):
        """Soul survives YAML persistence."""
        soul = AgentSoul(
            identity="Persistent soul",
            strengths=["persistence"],
            quirks=["never gives up"],
        )
        tpl = AgentTemplate(
            id="persist-test",
            name="Persist Test",
            soul=soul,
        )
        d = tpl.to_dict()
        # Simulate YAML round-trip
        import yaml
        yaml_str = yaml.dump(d, default_flow_style=False)
        loaded = yaml.safe_load(yaml_str)
        restored = AgentTemplate.from_dict(loaded)
        assert restored.soul.identity == "Persistent soul"
        assert restored.soul.strengths == ["persistence"]
        assert restored.soul.quirks == ["never gives up"]


class TestAgentCreator:
    """Test AgentCreator CRUD operations."""

    def test_default_templates_loaded(self, creator):
        """Built-in templates are always present."""
        templates = creator.list_templates()
        assert len(templates) >= 5  # At least the 5 defaults
        names = [t.name for t in templates]
        assert "General Assistant" in names
        assert "Coder" in names

    def test_create_template(self, creator):
        tpl = creator.create_template(
            name="Security Scanner",
            description="Scans for vulnerabilities",
            system_prompt="You are a security expert.",
            category="security",
        )
        assert tpl.id
        assert tpl.name == "Security Scanner"
        assert tpl.category == "security"
        assert tpl.is_builtin is False

    def test_create_template_unique_id(self, creator):
        """Creating a template with the same name gets a unique ID."""
        t1 = creator.create_template(name="My Agent")
        t2 = creator.create_template(name="My Agent")
        assert t1.id != t2.id

    def test_get_template(self, creator):
        tpl = creator.create_template(name="Fetcher", description="Fetches data")
        fetched = creator.get_template(tpl.id)
        assert fetched is not None
        assert fetched.name == "Fetcher"

    def test_get_nonexistent_template(self, creator):
        assert creator.get_template("nonexistent") is None

    def test_update_template(self, creator):
        tpl = creator.create_template(name="Old", description="Old desc")
        updated = creator.update_template(tpl.id, name="New", description="New desc")
        assert updated.name == "New"
        assert updated.description == "New desc"

    def test_cannot_delete_builtin(self, creator):
        """Built-in templates cannot be deleted."""
        general = creator.get_template("general")
        assert general is not None
        assert general.is_builtin is True
        result = creator.delete_template("general")
        assert result is False
        assert creator.get_template("general") is not None

    def test_delete_user_template(self, creator):
        tpl = creator.create_template(name="Delete Me", description="Bye")
        result = creator.delete_template(tpl.id)
        assert result is True
        assert creator.get_template(tpl.id) is None

    def test_list_by_category(self, creator):
        creator.create_template(name="Dev Agent", category="development")
        creator.create_template(name="Ops Agent", category="operations")

        dev_templates = creator.list_templates(category="development")
        assert all(t.category == "development" for t in dev_templates)
        assert len(dev_templates) >= 1

    def test_get_categories(self, creator):
        categories = creator.get_categories()
        assert "general" in categories
        assert "development" in categories

    def test_persistence(self, tmp_path):
        """User templates persist across creator instances."""
        path = tmp_path / "persist_templates.yaml"
        creator1 = AgentCreator(path=path)
        creator1.create_template(name="Persistent Agent", description="Survives restarts")

        creator2 = AgentCreator(path=path)
        templates = creator2.list_templates()
        names = [t.name for t in templates]
        assert "Persistent Agent" in names

    def test_create_with_rules(self, creator):
        tpl = creator.create_template(
            name="Ruled Agent",
            rules=[
                {"name": "Test first", "rule": "Always run tests before reporting done", "priority": "P0"},
            ],
        )
        assert len(tpl.rules) == 1
        assert tpl.rules[0]["priority"] == "P0"

    def test_spawn_config_override(self, creator):
        """Templates can override model/provider/temp."""
        tpl = creator.create_template(
            name="Focused",
            model="gpt-4o-mini",
            temperature=0.2,
            max_tokens=2048,
        )
        assert tpl.model == "gpt-4o-mini"
        assert tpl.temperature == 0.2
        assert tpl.max_tokens == 2048

    def test_count(self, creator):
        # Starts with builtins
        initial = creator.count()
        creator.create_template(name="New One")
        assert creator.count() == initial + 1

    def test_reload(self, creator):
        creator.create_template(name="Before Reload")
        count = creator.count()
        creator.reload()
        assert creator.count() == count

    def test_create_with_soul(self, creator):
        """Create a template with soul fields."""
        tpl = creator.create_template(
            name="Soulful Agent",
            soul_identity="I am a soulful agent",
            soul_strengths=["empathy", "precision"],
            soul_personality=["warm", "direct"],
            soul_values=["honesty", "completeness"],
            soul_quirks=["Always says good morning"],
        )
        assert tpl.soul.identity == "I am a soulful agent"
        assert "empathy" in tpl.soul.strengths
        assert "warm" in tpl.soul.personality
        assert "honesty" in tpl.soul.values
        assert len(tpl.soul.quirks) == 1

    def test_create_with_soul_object(self, creator):
        """Create a template with an AgentSoul object."""
        soul = AgentSoul(
            identity="Direct executor",
            strengths=["speed"],
            quirks=["Never asks unnecessary questions"],
        )
        tpl = creator.create_template(name="Soul Object Agent", soul=soul)
        assert tpl.soul.identity == "Direct executor"
        assert tpl.soul.strengths == ["speed"]

    def test_update_soul(self, creator):
        """Update soul on an existing template."""
        tpl = creator.create_template(name="Update Soul")
        assert tpl.soul.identity == ""

        soul = AgentSoul(
            identity="Updated identity",
            strengths=["updated"],
        )
        updated = creator.update_template(tpl.id, soul=soul)
        assert updated.soul.identity == "Updated identity"
        assert "updated" in updated.soul.strengths

    def test_soul_persistence(self, tmp_path):
        """Soul data persists to YAML and loads back correctly."""
        path = tmp_path / "soul_persist.yaml"
        creator1 = AgentCreator(path=path)
        creator1.create_template(
            name="Soul Persist",
            soul_identity="I persist across restarts",
            soul_strengths=["persistence"],
            soul_quirks=["never forgets"],
        )

        creator2 = AgentCreator(path=path)
        tpl = creator2.get_template("soul-persist")
        # User template, not builtin
        user_tpls = [t for t in creator2.list_templates() if t.name == "Soul Persist"]
        assert len(user_tpls) == 1
        assert user_tpls[0].soul.identity == "I persist across restarts"
        assert user_tpls[0].soul.strengths == ["persistence"]

    def test_default_templates_have_souls(self, creator):
        """All built-in templates have non-empty souls."""
        for tpl in creator.list_templates():
            if tpl.is_builtin:
                assert tpl.soul.identity, f"Built-in {tpl.name} missing soul identity"
                assert len(tpl.soul.strengths) >= 1, f"Built-in {tpl.name} missing soul strengths"
                assert len(tpl.soul.personality) >= 1, f"Built-in {tpl.name} missing soul personality"

    def test_soul_prompt_section_in_template(self, creator):
        """Template with soul generates a prompt section."""
        tpl = creator.create_template(
            name="Prompt Tester",
            soul_identity="I am the prompt tester",
            soul_strengths=["testing"],
            soul_personality=["thorough"],
        )
        prompt = tpl.soul.to_prompt_section()
        assert "## Who I Am" in prompt
        assert "I am the prompt tester" in prompt