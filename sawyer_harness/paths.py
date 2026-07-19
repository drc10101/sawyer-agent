"""
UserData paths — single source of truth for all Sawyer file locations.

Two zones under ~/.sawyer-harness/:
  user/   — permanent, never deleted by uninstall/upgrade/revert
  cache/  — ephemeral, safe to wipe (session scores, uploads, temp files)

All other modules import from here. No other file references
~/.sawyer-harness directly.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Root of all Sawyer user data
SAWYER_HOME = Path.home() / ".sawyer-harness"

# Permanent user data — survives uninstall
USER_DIR = SAWYER_HOME / "user"

# Ephemeral cache — safe to delete
CACHE_DIR = SAWYER_HOME / "cache"

# Logs
LOG_DIR = SAWYER_HOME / "logs"


class UserData:
    """Namespace for all Sawyer file paths. Single source of truth."""

    # ── Root directories ──
    home: Path = SAWYER_HOME
    user_dir: Path = USER_DIR
    cache_dir: Path = CACHE_DIR
    log_dir: Path = LOG_DIR

    # ── Config ──
    config_file: Path = USER_DIR / "config.yaml"

    # ── Memory ──
    memory_db: Path = USER_DIR / "memory.db"

    # ── Credentials ──
    keys_file: Path = USER_DIR / "keys.yaml"

    # ── Last Known Good ──
    lkg_file: Path = USER_DIR / "lkg.json"

    # ── Process management ──
    pid_file: Path = CACHE_DIR / "sawyer.pid"

    # ── Goal loops ──
    goal_loops_file: Path = USER_DIR / "goal_loops.yaml"

    # ── Cron ──
    cron_db: Path = USER_DIR / "cron.db"

    # ── Skills ──
    skills_dir: Path = USER_DIR / "skills"

    # ── Agents (global definitions) ──
    agents_dir: Path = USER_DIR / "agents"

    # ── Tools ──
    tools_dir: Path = USER_DIR / "tools"

    # ── Projects ──
    projects_dir: Path = USER_DIR / "projects"

    # ── Suggestions ──
    suggestions_dir: Path = USER_DIR / "suggestions"

    # ── Agent templates ──
    agent_templates_file: Path = USER_DIR / "agent_templates.yaml"

    # ── Orchestrations ──
    orchestrations_file: Path = USER_DIR / "orchestrations.yaml"

    # ── Rules ──
    rules_file: Path = USER_DIR / "rules.yaml"

    # ── Uploads (ephemeral) ──
    uploads_dir: Path = CACHE_DIR / "uploads"

    # ── Session scores (ephemeral) ──
    session_scores_dir: Path = CACHE_DIR / "session-scores"

    # ── Restart scripts (ephemeral) ──
    restart_script: Path = CACHE_DIR / "_restart"

    # ── Logs ──
    log_file: Path = LOG_DIR / "sawyer.log"

    # ── Desktop launcher (not in user/, lives at root) ──
    launch_script: Path = SAWYER_HOME / "launch.bat"

    # ── Migration marker ──
    _migration_marker: Path = USER_DIR / ".migrated"

    # ── Directories that must exist at startup ──
    _DIR_PATHS: list[Path] | None = None  # computed at class init time

    @classmethod
    def _merge_memory_dbs(cls, root_db: Path, user_db: Path) -> None:
        """Merge entries from root memory.db into user memory.db.

        When config.path pointed to the legacy root location, new entries
        were written there instead of the canonical user/ location. This
        merges any missing entries from root into user so no data is lost.
        """
        import sqlite3
        try:
            root_conn = sqlite3.connect(str(root_db))
            user_conn = sqlite3.connect(str(user_db))

            # Find keys in root that aren't in user
            root_keys = {row[0] for row in root_conn.execute("SELECT key FROM memories")}
            user_keys = {row[0] for row in user_conn.execute("SELECT key FROM memories")}
            missing = root_keys - user_keys

            if missing:
                for key in missing:
                    row = root_conn.execute(
                        "SELECT key, content, category, created_at, updated_at, access_count FROM memories WHERE key = ?",
                        (key,),
                    ).fetchone()
                    if row:
                        user_conn.execute(
                            "INSERT OR IGNORE INTO memories (key, content, category, created_at, updated_at, access_count) VALUES (?, ?, ?, ?, ?, ?)",
                            row,
                        )
                user_conn.commit()
                logger.info("Merged %d memory entries from root DB into user DB", len(missing))
            else:
                logger.debug("No missing memory entries to merge from root DB")

            root_conn.close()
            user_conn.close()
        except Exception as e:
            logger.warning("Failed to merge memory DBs (non-fatal): %s", e)

    @classmethod
    def _all_dirs(cls) -> list[Path]:
        """Return all Path attributes that are directories (no suffix)."""
        dirs = []
        for attr_name in dir(cls):
            if attr_name.startswith("_"):
                continue
            attr = getattr(cls, attr_name)
            if isinstance(attr, Path) and attr.suffix == "" and attr != SAWYER_HOME:
                dirs.append(attr)
        return dirs

    @classmethod
    def ensure_dirs(cls) -> None:
        """Create all directories that should exist at startup."""
        for d in cls._all_dirs():
            d.mkdir(parents=True, exist_ok=True)

    @classmethod
    def needs_migration(cls) -> bool:
        """Check if legacy files exist and haven't been migrated yet.

        Returns True if config.yaml is at the legacy root location and
        the migration marker doesn't exist yet.
        """
        if cls._migration_marker.exists():
            return False
        return (SAWYER_HOME / "config.yaml").exists()

    @classmethod
    def migrate_legacy(cls) -> None:
        """Migrate files from root ~/.sawyer-harness/ into user/ subdirectory.

        Called once on first launch after upgrade. Copies config, keys,
        memory, skills, tools, cron, lkg, goal_loops into user/.
        Skips any file that doesn't exist at the legacy location.
        Does NOT delete the legacy files — leaves them as backup.

        The server should be started before any other module accesses paths,
        and the launcher/server startup calls this automatically.
        """
        if not cls.needs_migration():
            logger.info("Migration not needed (already migrated or no legacy data)")
            return

        logger.info("Migrating legacy files from %s to %s ...", SAWYER_HOME, USER_DIR)
        cls.user_dir.mkdir(parents=True, exist_ok=True)

        # Single files: copy if source exists and destination doesn't
        legacy_files: dict[Path, Path] = {
            SAWYER_HOME / "config.yaml": cls.config_file,
            SAWYER_HOME / "keys.yaml": cls.keys_file,
            SAWYER_HOME / "memory.db": cls.memory_db,
            SAWYER_HOME / "memory.db-shm": cls.memory_db.parent / "memory.db-shm",
            SAWYER_HOME / "memory.db-wal": cls.memory_db.parent / "memory.db-wal",
            SAWYER_HOME / "lkg.json": cls.lkg_file,
            SAWYER_HOME / "goal_loops.yaml": cls.goal_loops_file,
            SAWYER_HOME / "cron.db": cls.cron_db,
            SAWYER_HOME / "orchestrations.yaml": cls.orchestrations_file,
            SAWYER_HOME / "rules.yaml": cls.rules_file,
            SAWYER_HOME / "agent_templates.yaml": cls.agent_templates_file,
        }

        # Directories: copy tree if source exists and destination doesn't
        legacy_dirs: dict[Path, Path] = {
            SAWYER_HOME / "skills": cls.skills_dir,
            SAWYER_HOME / "tools": cls.tools_dir,
            SAWYER_HOME / "sawyer-test-project": cls.projects_dir / "sawyer-test-project",
        }

        copied_files = 0
        for src, dst in legacy_files.items():
            if src.exists() and not dst.exists():
                shutil.copy2(str(src), str(dst))
                logger.debug("  Copied %s -> %s", src.name, dst)
                copied_files += 1

        copied_dirs = 0
        for src, dst in legacy_dirs.items():
            if src.is_dir() and not dst.exists():
                shutil.copytree(str(src), str(dst))
                logger.debug("  Copied dir %s -> %s", src.name, dst)
                copied_dirs += 1

        # Ephemeral data: move session-scores and uploads to cache/
        ephemeral_moves: dict[Path, Path] = {
            SAWYER_HOME / "session-scores": cls.session_scores_dir,
            SAWYER_HOME / "uploads": cls.uploads_dir,
        }
        for src, dst in ephemeral_moves.items():
            if src.is_dir() and not dst.exists():
                shutil.copytree(str(src), str(dst))
                logger.debug("  Copied ephemeral %s -> %s", src.name, dst)

        # Merge memory.db data if both root and user DBs exist
        # (root DB may have entries that user DB doesn't due to config.path pointing to root)
        root_db = SAWYER_HOME / "memory.db"
        user_db = cls.memory_db
        if root_db.exists() and user_db.exists():
            cls._merge_memory_dbs(root_db, user_db)

        # Mark migration as done
        cls._migration_marker.write_text("v1", encoding="utf-8")
        logger.info(
            "Migration complete: %d files, %d directories copied to %s",
            copied_files, copied_dirs, USER_DIR,
        )