# --- locations.py ---
"""
The tools' own folder in each person's home folder: the user config file and the default backup folders
of restore_file. Shared by common.py and init.py, as init runs before a project has a tools config,
so it cannot import common.py. This module reads no config.
"""
import hashlib
from pathlib import Path

# Each person's own folder of the tools, for all their projects
USER_FOLDER = Path.home() / ".ollmcp-tools"
# The user config file holds each person's own settings, such as API keys, so that they are never committed with a project's tools config
USER_CONFIG_PATH = USER_FOLDER / "user-config.json"
# Backups of the files changed by the filesystem tools, for restore_file. Outside the project, so that they are not in git.
DEFAULT_BACKUP_ROOT = USER_FOLDER / "backups"

def default_backup_folder(project_dir: Path) -> Path:
    """
    Returns the default backup folder of a project, named after it and a hash of its path,
    so that projects with the same name do not share one.
    """
    key = hashlib.sha256(str(project_dir).lower().encode("utf-8")).hexdigest()[:12]
    return DEFAULT_BACKUP_ROOT / f"{project_dir.name}-{key}"
