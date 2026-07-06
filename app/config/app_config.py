import copy
import logging
import os
from functools import lru_cache
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from app.config.database_config import DatabaseConfig
from app.config.logging_config import LoggingConfig
from app.config.paths import ENV_LOCAL, PROFILE_CONFIG_PREFIX, SETTINGS_DIR

if ENV_LOCAL and ENV_LOCAL.exists():
    load_dotenv(ENV_LOCAL)

logger = logging.getLogger(__name__)

BASE_CONFIG_PATH = SETTINGS_DIR / "application.yaml"
ACTIVE_PROFILE_PROPERTY = "python.profiles.active"
ACTIVE_PROFILE_ENV = "PYTHON_PROFILES_ACTIVE"
DEFAULT_PROFILE = "sandbox"


class KafkaConfig(BaseModel):
    enabled: bool = True
    bootstrap_servers: str = "gitlab-ci.ru:9092"
    input_topic: str = "frap-llm-helper-in"
    output_topic: str = "frap-llm-helper-out"
    group_id: str = "frap-llm-helper-reader"
    output_dir: str = "received_messages"
    documents_home_dir: str = "."
    send_json: str | None = None
    send_json_file: str | None = None


class AppConfig(BaseModel):
    """Итоговый конфиг приложения после слияния yaml-файлов и environment."""
    contour: str = "sandbox"
    description: str = ""
    database: DatabaseConfig
    logging: LoggingConfig
    kafka: KafkaConfig = Field(default_factory=KafkaConfig)


def _profile_config_path(profile: str) -> os.PathLike[str]:
    return SETTINGS_DIR / f"{PROFILE_CONFIG_PREFIX}{profile}.yaml"


def _list_available_profiles() -> list[str]:
    return sorted(
        path.name.removeprefix(PROFILE_CONFIG_PREFIX).removesuffix(".yaml")
        for path in SETTINGS_DIR.glob(f"{PROFILE_CONFIG_PREFIX}*.yaml")
    )


def _load_yaml(path: os.PathLike[str] | str) -> dict[str, Any]:
    """Читает yaml-файл в словарь."""
    with open(path, encoding="utf-8") as config_file:
        data = yaml.safe_load(config_file)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid config format: {path}")
    return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Дополняет общий конфиг значениями из профиля, не затирая соседние поля."""
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _yaml_path_to_env_key(yaml_path: str) -> str:
    """database.url → DATABASE_URL (как тестировщики пишут в K8S environment)."""
    return yaml_path.replace(".", "_").upper()


def _get_env_value(env_key: str) -> str | None:
    value = os.getenv(env_key)
    if value is not None:
        return value
    for name, env_value in os.environ.items():
        if _yaml_path_to_env_key(name) == env_key:
            return env_value
    return None


def _flatten_yaml_paths(data: dict[str, Any], prefix: str = "") -> dict[str, str]:
    """Пути yaml → ключи env (DATABASE_URL и т.д.)."""
    paths: dict[str, str] = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            paths.update(_flatten_yaml_paths(value, path))
        else:
            paths[_yaml_path_to_env_key(path)] = path
    return paths


def _paths_from_model(model: type[BaseModel], prefix: str = "") -> dict[str, str]:
    paths: dict[str, str] = {}
    for name, field in model.model_fields.items():
        if name == "contour":
            continue
        path = f"{prefix}.{name}" if prefix else name
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            paths.update(_paths_from_model(annotation, path))
        else:
            paths[_yaml_path_to_env_key(path)] = path
    return paths


def _env_paths(yaml_config: dict[str, Any]) -> dict[str, str]:
    """Ключи env для всех полей конфига + доп. полей из yaml (например database.dsn)."""
    paths = _paths_from_model(AppConfig)
    paths.update(_flatten_yaml_paths(yaml_config))
    return paths


def _set_nested_value(target: dict[str, Any], parts: list[str], value: str) -> None:
    current = target
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def get_active_profile() -> str:
    """Активный профиль: python.profiles.active → PYTHON_PROFILES_ACTIVE."""
    return _get_env_value(ACTIVE_PROFILE_ENV) or DEFAULT_PROFILE


def _load_env_overrides(yaml_config: dict[str, Any]) -> dict[str, Any]:
    """Environment в K8S перезаписывает поля yaml: database.url → DATABASE_URL."""
    overrides: dict[str, Any] = {}

    for env_key, yaml_path in _env_paths(yaml_config).items():
        value = _get_env_value(env_key)
        if value is not None:
            _set_nested_value(overrides, yaml_path.split("."), value)

    return overrides


def _load_merged_config(profile: str) -> dict[str, Any]:
    """Собирает итоговый словарь конфига для профиля."""
    if not BASE_CONFIG_PATH.is_file():
        raise FileNotFoundError(f"Base config not found: {BASE_CONFIG_PATH}")

    profile_path = _profile_config_path(profile)
    if not os.path.isfile(profile_path):
        available = _list_available_profiles()
        raise FileNotFoundError(f"Profile config not found: {profile_path}. Available: {available}")

    merged = _load_yaml(BASE_CONFIG_PATH)
    merged = _deep_merge(merged, _load_yaml(profile_path))
    merged = _deep_merge(merged, _load_env_overrides(merged))
    merged["contour"] = profile
    return merged


@lru_cache
def get_app_config() -> AppConfig:
    """Возвращает типизированный конфиг активного профиля."""
    return AppConfig.model_validate(_load_merged_config(get_active_profile()))


def log_app_config() -> None:
    """Пишет активный конфиг в лог при старте."""
    config = get_app_config()
    logger.info(
        "Active config (profile=%s, %s=%s):",
        config.contour,
        ACTIVE_PROFILE_PROPERTY,
        get_active_profile(),
    )
    logger.info("%s", yaml.safe_dump(config.model_dump(), allow_unicode=True, sort_keys=False).rstrip())
