from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str
    allowed_user_ids: Annotated[list[int], NoDecode]
    transmission_host: str = "127.0.0.1"
    transmission_port: int = 9091
    transmission_user: str = ""
    transmission_password: str = ""
    transmission_system_user: str = "debian-transmission"
    # HTTP(S) announce/scrape через прокси (socks5h://…). Пусто — без прокси.
    transmission_proxy_url: str = ""
    default_download_dir: str = "/var/lib/transmission-daemon/downloads"
    allowed_roots: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["/var/lib/transmission-daemon"]
    )
    db_path: str = "remotor.db"
    metadata_timeout_sec: int = 300
    status_interval_sec: int = 15

    @field_validator("allowed_user_ids", mode="before")
    @classmethod
    def split_user_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(item.strip()) for item in value.split(",") if item.strip()]
        return value

    @field_validator("allowed_user_ids")
    @classmethod
    def user_ids_required(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("ALLOWED_USER_IDS is empty")
        return value

    @field_validator("allowed_roots", mode="before")
    @classmethod
    def split_roots(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("allowed_roots")
    @classmethod
    def roots_required(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("ALLOWED_ROOTS is empty")
        return value

    @field_validator("metadata_timeout_sec", "status_interval_sec")
    @classmethod
    def positive_seconds(cls, value: int) -> int:
        if value < 1:
            raise ValueError("interval must be positive")
        return value
