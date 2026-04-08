from pydantic import BaseModel, ConfigDict


class HttpConfig(BaseModel):
    """HTTP connection pool and timeout settings for the global httpx patch.

    The values here are applied to ALL httpx.AsyncClient instances in the
    process (via a module-level monkey-patch). The most recently created
    CompletionClient's http_config applies to subsequent AsyncClient instances.
    """

    model_config = ConfigDict(frozen=True)

    max_connections: int = 100
    max_keepalive_connections: int = 0  # 0 = disabled, prevents CLOSE_WAIT
    keepalive_expiry: float = 0.0
    connect_timeout: float = 10.0
    read_timeout: float = 60.0  # catches CLOSE_WAIT hangs
    write_timeout: float = 10.0
    pool_timeout: float = 10.0

    def merge_with(self, other: "HttpConfig") -> "HttpConfig":
        if not other.model_fields_set:
            raise ValueError(
                "merge_with() received a config with no model_fields_set. "
                "Was it constructed from model_dump() or model_validate()? "
                "Config objects must be freshly constructed: HttpConfig(field=value)."
            )
        return self.model_copy(update={k: getattr(other, k) for k in other.model_fields_set})
