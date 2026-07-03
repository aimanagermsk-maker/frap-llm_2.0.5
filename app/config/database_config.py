from typing import Any
from urllib.parse import quote, unquote, urlparse, urlunparse

from pydantic import BaseModel, model_validator


def _parse_dsn(dsn: str) -> dict[str, str]:
    """Разбирает dsn-строку на поля конфига."""
    parsed = urlparse(dsn.removeprefix("jdbc:"))
    scheme = parsed.scheme or "postgresql"
    host = parsed.hostname or "localhost"
    port = f":{parsed.port}" if parsed.port else ""
    return {
        "url": f"jdbc:{scheme}://{host}{port}",
        "username": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
    }


class DatabaseConfig(BaseModel):
    """Секция конфига с параметрами подключения."""

    url: str
    username: str
    password: str

    @model_validator(mode="before")
    @classmethod
    def resolve_connection(cls, data: Any) -> Any:
        """Сводит dsn и отдельные поля к единому набору параметров."""
        if not isinstance(data, dict):
            return data

        from_dsn = _parse_dsn(data["dsn"]) if data.get("dsn") else {}
        url = data.get("url") or from_dsn.get("url")
        username = data.get("username") if "username" in data else from_dsn.get("username", "")
        password = data.get("password") if "password" in data else from_dsn.get("password", "")

        if not url:
            raise ValueError("Укажите dsn или url")

        return {"url": url, "username": username, "password": password}

    def asyncpg_dsn(self) -> str:
        """Преобразует поля конфига в runtime-формат."""
        jdbc_url = self.url.removeprefix("jdbc:")
        parsed = urlparse(jdbc_url)

        host = parsed.hostname or "localhost"
        port = f":{parsed.port}" if parsed.port else ""
        credentials = f"{quote(self.username, safe='')}:{quote(self.password, safe='')}"
        netloc = f"{credentials}@{host}{port}"

        return urlunparse((parsed.scheme or "postgresql", netloc, "", "", "", ""))


def get_database() -> DatabaseConfig:
    """Возвращает секцию конфига активного профиля."""
    from app.config.app_config import get_app_config

    return get_app_config().database


def get_database_dsn() -> str:
    """Возвращает runtime-формат секции конфига."""
    return get_database().asyncpg_dsn()
