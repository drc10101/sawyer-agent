"""Tests for Agent Rules engine."""

import pytest

from sawyer_harness.rules import RulesStore, AgentRule, RulePriority, RuleScope


@pytest.fixture
def rules_store(tmp_path):
    """Create a fresh RulesStore with a temp file."""
    path = tmp_path / "test_rules.yaml"
    return RulesStore(path=path)


class TestAgentRule:
    """Test AgentRule dataclass."""

    def test_to_dict_roundtrip(self):
        rule = AgentRule(
            id="test1",
            name="No Push",
            rule="Never push to production without approval",
            detail="Ask Dave first",
            priority=RulePriority.P0,
            scope=RuleScope.GLOBAL,
        )
        d = rule.to_dict()
        assert d["id"] == "test1"
        assert d["priority"] == "P0"
        assert d["scope"] == "global"

        restored = AgentRule.from_dict(d)
        assert restored.id == rule.id
        assert restored.priority == RulePriority.P0
        assert restored.scope == RuleScope.GLOBAL

    def test_from_dict_with_string_enums(self):
        d = {
            "id": "r1",
            "name": "Test",
            "rule": "Do X",
            "priority": "P1",
            "scope": "cron",
            "enabled": True,
            "created": "",
            "updated": "",
            "last_run": "",
            "next_run": "",
            "run_count": 0,
            "metadata": {},
            "agent": "",
            "detail": "",
        }
        rule = AgentRule.from_dict(d)
        assert rule.priority == RulePriority.P1
        assert rule.scope == RuleScope.CRON


class TestRulesStore:
    """Test RulesStore CRUD operations."""

    def test_add_rule(self, rules_store):
        rule = rules_store.add_rule(
            name="No Push",
            rule="Never push to production without approval",
            priority=RulePriority.P0,
        )
        assert rule.id
        assert rule.name == "No Push"
        assert rule.priority == RulePriority.P0
        assert rule.scope == RuleScope.GLOBAL
        assert rule.enabled is True

    def test_get_rule(self, rules_store):
        added = rules_store.add_rule(name="Test", rule="Do stuff")
        fetched = rules_store.get_rule(added.id)
        assert fetched is not None
        assert fetched.name == "Test"

    def test_get_nonexistent_rule(self, rules_store):
        assert rules_store.get_rule("nonexistent") is None

    def test_update_rule(self, rules_store):
        added = rules_store.add_rule(name="Old Name", rule="Old Rule")
        updated = rules_store.update_rule(added.id, name="New Name", rule="New Rule")
        assert updated.name == "New Name"
        assert updated.rule == "New Rule"
        assert updated.updated  # timestamp was set

    def test_update_nonexistent_rule(self, rules_store):
        result = rules_store.update_rule("nonexistent", name="x")
        assert result is None

    def test_delete_rule(self, rules_store):
        added = rules_store.add_rule(name="Delete Me", rule="Bye")
        assert rules_store.delete_rule(added.id) is True
        assert rules_store.get_rule(added.id) is None

    def test_delete_nonexistent_rule(self, rules_store):
        assert rules_store.delete_rule("nonexistent") is False

    def test_list_rules(self, rules_store):
        rules_store.add_rule(name="Rule A", rule="A", priority=RulePriority.P1)
        rules_store.add_rule(name="Rule B", rule="B", priority=RulePriority.P0)
        rules_store.add_rule(name="Rule C", rule="C", priority=RulePriority.P2)
        
        all_rules = rules_store.list_rules()
        assert len(all_rules) == 3
        # Should be sorted by priority
        assert all_rules[0].priority == RulePriority.P0
        assert all_rules[1].priority == RulePriority.P1
        assert all_rules[2].priority == RulePriority.P2

    def test_list_rules_by_scope(self, rules_store):
        rules_store.add_rule(name="Global", rule="G", scope=RuleScope.GLOBAL)
        rules_store.add_rule(name="Cron", rule="C", scope=RuleScope.CRON)
        
        cron_rules = rules_store.list_rules(scope=RuleScope.CRON)
        assert len(cron_rules) == 1
        assert cron_rules[0].name == "Cron"

    def test_list_rules_enabled_only(self, rules_store):
        rules_store.add_rule(name="On", rule="Active")
        r2 = rules_store.add_rule(name="Off", rule="Inactive")
        rules_store.update_rule(r2.id, enabled=False)
        
        enabled = rules_store.list_rules(enabled_only=True)
        assert len(enabled) == 1
        assert enabled[0].name == "On"

    def test_list_rules_by_agent(self, rules_store):
        rules_store.add_rule(name="For Coder", rule="Code well", scope=RuleScope.AGENT, agent="coder")
        rules_store.add_rule(name="For All", rule="Be good", scope=RuleScope.GLOBAL)
        
        agent_rules = rules_store.list_rules(agent="coder")
        # GLOBAL rules are included, AGENT rules for "coder" are included
        # AGENT rules for other agents are excluded
        assert any(r.name == "For Coder" for r in agent_rules)
        assert any(r.name == "For All" for r in agent_rules)

    def test_disable_rule(self, rules_store):
        rule = rules_store.add_rule(name="Test", rule="Test rule")
        rules_store.update_rule(rule.id, enabled=False)
        fetched = rules_store.get_rule(rule.id)
        assert fetched.enabled is False

    def test_persistence(self, tmp_path):
        """Rules persist across store instances."""
        path = tmp_path / "persist_rules.yaml"
        store1 = RulesStore(path=path)
        store1.add_rule(name="Persistent", rule="I survive restarts")
        
        # Create a new store pointing to the same file
        store2 = RulesStore(path=path)
        rules = store2.list_rules()
        assert len(rules) == 1
        assert rules[0].name == "Persistent"

    def test_get_rules_prompt(self, rules_store):
        rules_store.add_rule(
            name="No Push",
            rule="Never push to production without approval",
            detail="Ask Dave first",
            priority=RulePriority.P0,
        )
        prompt = rules_store.get_rules_prompt()
        assert "Custom Agent Rules" in prompt
        assert "[P0]" in prompt
        assert "Never push to production" in prompt
        assert "Ask Dave first" in prompt

    def test_get_rules_prompt_empty(self, rules_store):
        prompt = rules_store.get_rules_prompt()
        assert prompt == ""

    def test_reload(self, rules_store):
        rules_store.add_rule(name="Before Reload", rule="Test")
        count_before = rules_store.count()
        rules_store.reload()
        assert rules_store.count() == count_before

    def test_count(self, rules_store):
        assert rules_store.count() == 0
        rules_store.add_rule(name="A", rule="A")
        assert rules_store.count() == 1
        rules_store.add_rule(name="B", rule="B")
        assert rules_store.count() == 2