from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SETTINGS_DIR = PROJECT_ROOT / "settings"
ENV_LOCAL = PROJECT_ROOT / ".env.local"

PROFILE_CONFIG_PREFIX = "application-"
