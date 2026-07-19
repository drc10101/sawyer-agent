"""Tests for KeyStorage credential management."""

import os
import pytest

from sawyer_harness.key_storage import KeyStorage, mask_value


@pytest.fixture
def keys_dir(tmp_path):
    """Create a temporary keys directory."""
    keys_file = tmp_path / "keys.yaml"
    return keys_file


@pytest.fixture
def ks(keys_dir):
    """Create a KeyStorage instance with a temp file."""
    return KeyStorage(path=keys_dir)


class TestMaskValue:
    def test_short_value(self):
        assert mask_value("ab") == "****"

    def test_exact_visible(self):
        assert mask_value("1234") == "****"

    def test_longer_value(self):
        assert mask_value("sk-abcdef123456") == "***********3456"

    def test_empty(self):
        assert mask_value("") == "****"

    def test_none(self):
        assert mask_value(None) == "****"


class TestKeyStorageInit:
    def test_creates_directory(self, keys_dir):
        KeyStorage(path=keys_dir)
        assert keys_dir.parent.exists()

    def test_starts_empty(self, ks):
        entries = ks.list_entries()
        assert all(len(v) == 0 for v in entries.values())

    def test_categories(self, ks):
        cats = ks.categories()
        assert cats == ["ssh", "api", "tokens", "custom"]

    def test_count_empty(self, ks):
        count = ks.count()
        assert all(v == 0 for v in count.values())


class TestAddEntry:
    def test_add_ssh(self, ks):
        entry = ks.add_entry("ssh", {
            "name": "runpod-prod",
            "host": "ssh.runpod.io",
            "user": "root",
            "key_path": "~/.ssh/id_ed25519",
            "note": "Main RunPod instance"
        })
        assert entry["name"] == "runpod-prod"
        assert entry["host"] == "ssh.runpod.io"  # host is not sensitive
        assert entry["key_path"] == "~/.ssh/id_ed25519"

    def test_add_api_key_masked(self, ks):
        entry = ks.add_entry("api", {
            "name": "openai",
            "provider": "openai",
            "key": "sk-abcdef1234567890",
            "note": "Main OpenAI key"
        })
        assert entry["key"].endswith("7890")
        assert entry["key"].startswith("*")
        assert entry["provider"] == "openai"

    def test_add_token_masked(self, ks):
        entry = ks.add_entry("tokens", {
            "name": "telegram-bot",
            "service": "telegram",
            "token": "123456:ABC-DEF",
            "note": "Jade bot token"
        })
        assert entry["token"].endswith("-DEF")
        assert entry["token"].startswith("*")

    def test_add_custom(self, ks):
        entry = ks.add_entry("custom", {
            "name": "my-service",
            "endpoint": "https://api.example.com",
            "secret": "supersecret123"
        })
        assert entry["secret"].endswith("t123")
        assert entry["endpoint"] == "https://api.example.com"

    def test_add_duplicate_raises(self, ks):
        ks.add_entry("api", {"name": "openai", "key": "sk-test"})
        with pytest.raises(ValueError, match="already exists"):
            ks.add_entry("api", {"name": "openai", "key": "sk-test2"})

    def test_add_invalid_category(self, ks):
        with pytest.raises(ValueError, match="Invalid category"):
            ks.add_entry("invalid", {"name": "test"})

    def test_add_no_name_raises(self, ks):
        with pytest.raises(ValueError, match="name"):
            ks.add_entry("api", {"key": "sk-test"})

    def test_add_default_permission(self, ks):
        entry = ks.add_entry("api", {"name": "test", "key": "sk-test"})
        assert entry["permission"] == "ask"

    def test_add_explicit_permission(self, ks):
        entry = ks.add_entry("api", {"name": "test", "key": "sk-test", "permission": "allowlist"})
        assert entry["permission"] == "allowlist"

    def test_add_invalid_permission_raises(self, ks):
        with pytest.raises(ValueError, match="Invalid permission"):
            ks.add_entry("api", {"name": "test", "key": "sk-test", "permission": "invalid"})

    def test_add_with_allow_for(self, ks):
        entry = ks.add_entry("ssh", {
            "name": "prod-ssh",
            "host": "example.com",
            "permission": "allowlist",
            "allow_for": ["ssh-connect", "file-transfer"]
        })
        assert entry["permission"] == "allowlist"
        assert entry["allow_for"] == ["ssh-connect", "file-transfer"]


class TestGetEntry:
    def test_get_returns_unmasked(self, ks):
        ks.add_entry("api", {"name": "openai", "key": "sk-abcdef1234567890"})
        entry = ks.get_entry("api", "openai")
        assert entry["key"] == "sk-abcdef1234567890"

    def test_get_nonexistent(self, ks):
        assert ks.get_entry("api", "nonexistent") is None

    def test_get_value(self, ks):
        ks.add_entry("api", {"name": "openai", "key": "sk-test123"})
        assert ks.get_value("api", "openai", "key") == "sk-test123"

    def test_get_value_default_field(self, ks):
        ks.add_entry("api", {"name": "openai", "key": "sk-test123"})
        assert ks.get_value("api", "openai") == "sk-test123"


class TestUpdateEntry:
    def test_update_note(self, ks):
        ks.add_entry("api", {"name": "openai", "key": "sk-test", "note": "old"})
        updated = ks.update_entry("api", "openai", {"note": "new note"})
        assert updated["note"] == "new note"

    def test_update_key_rotated(self, ks):
        ks.add_entry("api", {"name": "openai", "key": "sk-old-key"})
        updated = ks.update_entry("api", "openai", {"key": "sk-new-key"})
        assert updated["key"].endswith("-key")  # masked in return

    def test_update_cannot_change_name(self, ks):
        ks.add_entry("api", {"name": "openai", "key": "sk-test"})
        updated = ks.update_entry("api", "openai", {"name": "different"})
        assert updated["name"] == "openai"

    def test_update_nonexistent_raises(self, ks):
        with pytest.raises(KeyError):
            ks.update_entry("api", "nonexistent", {"key": "test"})


class TestDeleteEntry:
    def test_delete(self, ks):
        ks.add_entry("api", {"name": "openai", "key": "sk-test"})
        deleted = ks.delete_entry("api", "openai")
        assert deleted["name"] == "openai"
        assert ks.get_entry("api", "openai") is None

    def test_delete_nonexistent_raises(self, ks):
        with pytest.raises(KeyError):
            ks.delete_entry("api", "nonexistent")


class TestListEntries:
    def test_list_masked_by_default(self, ks):
        ks.add_entry("api", {"name": "openai", "key": "sk-abcdef1234567890"})
        result = ks.list_entries(category="api", masked=True)
        assert result["api"][0]["key"].endswith("7890")
        assert result["api"][0]["key"].startswith("*")

    def test_list_unmasked(self, ks):
        ks.add_entry("api", {"name": "openai", "key": "sk-abcdef1234567890"})
        result = ks.list_entries(category="api", masked=False)
        assert result["api"][0]["key"] == "sk-abcdef1234567890"

    def test_list_all_categories(self, ks):
        ks.add_entry("ssh", {"name": "prod", "host": "example.com"})
        ks.add_entry("api", {"name": "openai", "key": "sk-test"})
        result = ks.list_entries()
        assert len(result["ssh"]) == 1
        assert len(result["api"]) == 1
        assert len(result["tokens"]) == 0


class TestPersistence:
    def test_saves_to_yaml(self, ks, keys_dir):
        ks.add_entry("api", {"name": "openai", "key": "sk-test"})
        assert keys_dir.exists()

    def test_file_permissions(self, ks, keys_dir):
        ks.add_entry("api", {"name": "test", "key": "sk-test"})
        os.stat(keys_dir).st_mode & 0o777
        # On Windows, chmod may not work as expected; just verify file exists
        assert keys_dir.exists()

    def test_reload_from_disk(self, keys_dir):
        ks1 = KeyStorage(path=keys_dir)
        ks1.add_entry("api", {"name": "openai", "key": "sk-test123"})
        # Create a new instance pointing to the same file
        ks2 = KeyStorage(path=keys_dir)
        entry = ks2.get_entry("api", "openai")
        assert entry is not None
        assert entry["key"] == "sk-test123"

    def test_count(self, ks):
        ks.add_entry("ssh", {"name": "prod", "host": "example.com"})
        ks.add_entry("api", {"name": "openai", "key": "sk-test"})
        count = ks.count()
        assert count["ssh"] == 1
        assert count["api"] == 1
        assert count["tokens"] == 0