from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str

    allowed_target_hosts: str = "localhost,127.0.0.1,::1"

    credential_encryption_key: SecretStr | None = None
    credential_encryption_key_version: str = "v1"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    @property
    def allowed_target_host_set(self) -> set[str]:
        return {
            host.strip()
            for host in self.allowed_target_hosts.split(",")
            if host.strip()
        }


settings = Settings()
