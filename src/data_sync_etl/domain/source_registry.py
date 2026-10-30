from pydantic import BaseModel, ConfigDict, Field, field_validator


class SourceCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    read_metadata: bool = True
    read_content: bool = False
    process_content: bool = False
    index_derived_content: bool = False
    exact_resource_lookup: bool = True
    exact_version_lookup: bool = False
    file_reference_lookup: bool = False
    external_viewer: bool = False
    remote_search: bool = False
    write_metadata: bool = False


class SourceRegistration(BaseModel):
    """Non-secret server-side registration; credentials are referenced by env key."""
    model_config = ConfigDict(frozen=True, extra='forbid', hide_input_in_errors=True)

    source_id: str = Field(min_length=1, max_length=30, pattern=r'^[A-Za-z0-9_-]+$')
    backend: str = Field(min_length=1, max_length=64)
    namespace: str = Field(min_length=1, max_length=255)
    adapter_type: str = Field(min_length=1, max_length=64)
    enabled: bool = True
    capabilities: SourceCapabilities = Field(default_factory=SourceCapabilities)
    connection_ref: str | None = Field(default=None, max_length=128)
    credential_ref: str | None = Field(default=None, max_length=128)
    timeout_seconds: int = Field(default=15, ge=1, le=600)
    viewer_hosts: tuple[str, ...] = ()

    @field_validator('backend', 'namespace', 'adapter_type')
    @classmethod
    def non_uri_identifier(cls, value):
        value = value.strip()
        if not value or '://' in value or any(ord(char) < 32 for char in value):
            raise ValueError('must be a non-empty opaque identifier')
        return value

    @field_validator('credential_ref', 'connection_ref')
    @classmethod
    def env_reference_only(cls, value):
        if value is not None and not value.startswith('env:'):
            raise ValueError('credentials must use an env: reference')
        if value is not None and (not value[4:] or not value[4:].isascii()
                                  or not value[4:].replace('_', 'A').isalnum()
                                  or value[4][0].isdigit()):
            raise ValueError('invalid environment credential reference')
        return value

    @field_validator('viewer_hosts')
    @classmethod
    def validate_viewer_hosts(cls, values):
        import re
        from urllib.parse import urlsplit

        normalized = []
        for value in values:
            host = value.strip().lower().rstrip('.')
            parsed = urlsplit(f'https://{host}')
            if (not host or '*' in host or parsed.hostname != host or parsed.port is not None
                    or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*', host)
                    or parsed.path or parsed.query or parsed.fragment or '@' in host):
                raise ValueError('viewer_hosts must contain host names only')
            normalized.append(host)
        if len(set(normalized)) != len(normalized):
            raise ValueError('viewer_hosts must be unique')
        return tuple(normalized)
