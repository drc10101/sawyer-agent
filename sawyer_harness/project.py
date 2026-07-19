"""
Project layout manager -- standardized directory structure for every project.

When you start a Sawyer project, it gets the same directory structure every time.
No more hunting for where the agent put things. No more inconsistency between
projects. The layout is predictable, searchable, and self-documenting.

Standard layout:
    project-name/
    ├── README.md              # Always first. Project overview.
    ├── config.yaml             # Project configuration
    ├── src/                    # Source code
    │   ├── __init__.py
    │   └── ...
    ├── tests/                  # Test files
    │   ├── __init__.py
    │   └── ...
    ├── docs/                   # Documentation
    │   └── ...
    ├── data/                   # Data files (input/output)
    │   ├── raw/                # Original/unprocessed data
    │   └── processed/          # Processed/cleaned data
    ├── outputs/                # Agent-generated outputs
    │   ├── reports/
    │   └── exports/
    ├── .sawyer/                # Sawyer metadata (hidden, like .git)
    │   ├── session-notes/      # Auto-generated session notes
    │   ├── goals/               # Goal tracking
    │   └── memory.json          # Project-specific memory
    └── pyproject.toml           # Python project config (if applicable)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .paths import UserData

logger = logging.getLogger("sawyer-harness.project")


# Standard directory structure
STANDARD_LAYOUT = {
    "src": "Source code",
    "tests": "Test files",
    "docs": "Documentation",
    "data/raw": "Original/unprocessed data",
    "data/processed": "Processed/cleaned data",
    "outputs/reports": "Generated reports",
    "outputs/exports": "Exported files",
    ".sawyer/session-notes": "Auto-generated session notes",
    ".sawyer/goals": "Goal tracking files",
}


def _readme_template(name, description, date):
    desc = description or "A Sawyer Harness project"
    return f"""# {name}

> Created with Sawyer Harness

## Overview

{desc}

## Project Structure

- `src/` - Source code
- `tests/` - Test files
- `docs/` - Documentation
- `data/` - Data files (raw and processed)
- `outputs/` - Generated outputs and reports
- `.sawyer/` - Sawyer metadata (session notes, goals, memory)

## Getting Started

```bash
# Install dependencies
pip install -e .

# Run tests
python -m pytest tests/ -v
```
"""


def _config_template(name, description, date):
    desc = description or ""
    return f"""# Sawyer Harness project configuration
project:
  name: "{name}"
  description: "{desc}"
  created: "{date}"

agent:
  model: "sawyer"
  max_tool_rounds: 20

memory:
  path: ".sawyer/memory.json"

skills:
  path: ".sawyer/skills/"

context:
  window_size: 128000
  reserve_ratio: 0.20
  compression_enabled: true
  compression_threshold: 0.70
"""


def _src_init_template(name, description, date):
    return f'"""  {name} source package """\n'


def _tests_init_template(name, description, date):
    return f"# {name} tests\n"


def _memory_template(name, description, date):
    return json.dumps({
        "entries": [],
        "project": name,
        "created": date,
    }, indent=2)


def _gitignore_template(name, description, date):
    return """# Sawyer Harness
.sawyer/memory.json
.sawyer/session-notes/

# Python
__pycache__/
*.py[cod]
*$py.class
*.egg-info/
dist/
build/
.eggs/

# Virtual environments
.venv/
venv/

# IDE
.vscode/
.idea/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Data (large files)
data/raw/*.csv
data/raw/*.json
data/processed/*.csv
data/processed/*.json
"""


TEMPLATE_FILES = {
    "README.md": _readme_template,
    "config.yaml": _config_template,
    "src/__init__.py": _src_init_template,
    "tests/__init__.py": _tests_init_template,
    ".sawyer/memory.json": _memory_template,
    ".gitignore": _gitignore_template,
}


@dataclass
class Project:
    """Represents a Sawyer project with its directory structure."""
    name: str
    path: Path
    description: str = ""
    created: str = ""

    @property
    def sawyer_dir(self) -> Path:
        return self.path / ".sawyer"

    @property
    def session_notes_dir(self) -> Path:
        return self.sawyer_dir / "session-notes"

    @property
    def goals_dir(self) -> Path:
        return self.sawyer_dir / "goals"

    @property
    def memory_path(self) -> Path:
        return self.sawyer_dir / "memory.json"

    @property
    def config_path(self) -> Path:
        return self.path / "config.yaml"

    @property
    def is_initialized(self) -> bool:
        """Check if this path is an initialized Sawyer project."""
        return (self.path / ".sawyer").exists() and (self.path / "config.yaml").exists()

    def get_file_index(self) -> dict:
        """Get an index of all files in the project, organized by directory."""
        index = {}
        for filepath in self.path.rglob("*"):
            if not filepath.is_file():
                continue
            # Skip hidden directories except .sawyer
            if any(part.startswith(".") and part != ".sawyer" for part in filepath.parts):
                continue

            # Skip compiled files
            if filepath.name.endswith((".pyc", ".pyo", ".egg", ".whl")):
                continue

            try:
                rel_path = filepath.relative_to(self.path)
                size = filepath.stat().st_size
                modified = datetime.fromtimestamp(
                    filepath.stat().st_mtime,
                    tz=timezone.utc,
                ).isoformat()
                # Normalize to forward slashes for cross-platform consistency
                key = str(rel_path).replace("\\", "/")
                index[key] = {
                    "size": size,
                    "modified": modified,
                    "path": str(filepath),
                }
            except OSError:
                continue

        return index


class ProjectManager:
    """
    Manages Sawyer projects: creating, loading, and navigating the
    standard directory structure.
    """

    def __init__(self, base_path: Path | None = None):
        """
        Args:
            base_path: Base directory for projects. Defaults to ~/projects/
        """
        self.base_path = base_path or UserData.projects_dir
        self.base_path.mkdir(parents=True, exist_ok=True)

    def create_project(
        self,
        name: str,
        description: str = "",
        path: Path | None = None,
        template: str = "default",
    ) -> Project:
        """
        Create a new Sawyer project with the standard directory structure.

        Args:
            name: Project name (used for directory name)
            description: Project description
            path: Override the project path (defaults to base_path/name)
            template: Project template (default, python, data-science)

        Returns:
            Project object for the newly created project
        """
        # Sanitize name for directory use
        safe_name = name.lower().replace(" ", "-").replace("_", "-")
        safe_name = "".join(c for c in safe_name if c.isalnum() or c in "-.")

        project_path = path or self.base_path / safe_name
        project_path.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc).isoformat()

        project = Project(
            name=name,
            path=project_path,
            description=description,
            created=now,
        )

        # Create standard directories
        for dir_path, _ in STANDARD_LAYOUT.items():
            full_path = project_path / dir_path
            full_path.mkdir(parents=True, exist_ok=True)

        # Create template files
        for filename, template_fn in TEMPLATE_FILES.items():
            file_path = project_path / filename
            if not file_path.exists():
                content = template_fn(name, description, now)
                file_path.write_text(content, encoding="utf-8")

        logger.info(f"Created project '{name}' at {project_path}")
        return project

    def load_project(self, path: Path) -> Project | None:
        """Load an existing Sawyer project from its directory."""
        path = Path(path)

        if not (path / ".sawyer").exists():
            logger.warning(f"Not a Sawyer project: {path}")
            return None

        # Read project config if it exists
        config_path = path / "config.yaml"
        name = path.name
        description = ""
        created = ""

        if config_path.exists():
            try:
                import yaml
                config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
                if config:
                    project_config = config.get("project", {})
                    name = project_config.get("name", name)
                    description = project_config.get("description", description)
                    created = project_config.get("created", created)
            except Exception:
                pass

        return Project(
            name=name,
            path=path,
            description=description,
            created=created,
        )

    def find_project(self, name: str) -> Project | None:
        """Find a project by name in the base directory."""
        # Try exact match first
        project_path = self.base_path / name
        if (project_path / ".sawyer").exists():
            return self.load_project(project_path)

        # Try with hyphens/underscores variations
        for variation in [name.replace(" ", "-"), name.replace(" ", "_"),
                         name.lower(), name.lower().replace(" ", "-")]:
            path = self.base_path / variation
            if (path / ".sawyer").exists():
                return self.load_project(path)

        return None

    def list_projects(self) -> list[Project]:
        """List all Sawyer projects in the base directory."""
        projects = []
        for path in self.base_path.iterdir():
            if path.is_dir() and (path / ".sawyer").exists():
                project = self.load_project(path)
                if project:
                    projects.append(project)
        return sorted(projects, key=lambda p: p.name)

    def get_output_path(self, project: Project, filename: str, category: str = "reports") -> Path:
        """
        Get the standard output path for a generated file.

        This is how the agent knows WHERE to put things. Instead of
        random locations, output goes to outputs/{category}/filename.
        """
        output_dir = project.path / "outputs" / category
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir / filename

    def get_data_path(self, project: Project, filename: str, processed: bool = True) -> Path:
        """
        Get the standard data path for a data file.

        Args:
            processed: True for processed data, False for raw data
        """
        subcategory = "processed" if processed else "raw"
        data_dir = project.path / "data" / subcategory
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir / filename