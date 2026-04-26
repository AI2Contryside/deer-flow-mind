"""Configuration for the tenant_profile feature.

Mirrors the ``tenant_profile:`` block in ``config.yaml`` (see
TENANT_PROFILE_DESIGN.md §7). Defaults align with the v1 decisions; every
knob is overridable so operators can tune per deployment.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TopKConfig(BaseModel):
    """Per-doctype top-K caps used by the summarizer to prune key_entities."""

    customer: int = 20
    supplier: int = 20
    item: int = 30
    warehouse: int = 10
    price_list: int = 5
    uom: int = 10
    item_group: int = 10
    customer_group: int = 10
    supplier_group: int = 10
    territory: int = 10
    sales_person: int = 10
    brand: int = 5
    account: int = 15
    cost_center: int = 10
    sales_tax_template: int = 5
    purchase_tax_template: int = 5
    payment_terms_template: int = 5
    pricing_rule: int = 5


class TriggerConfig(BaseModel):
    event_count_threshold: int = Field(default=50, ge=1)
    primary_event_threshold: int = Field(default=10, ge=1)
    time_threshold_seconds: int = Field(default=86400, ge=60)
    cooldown_seconds: int = Field(default=1800, ge=0)


class FactsConfig(BaseModel):
    bootstrap_ttl_seconds: int = Field(default=86400, ge=60)
    primary_company_resolver: str = Field(default="employee")  # employee | global_default | first
    sync_bootstrap_timeout_seconds: float = Field(default=2.0, ge=0.1, le=10.0)


class ObservationConfig(BaseModel):
    log_max_size_bytes: int = Field(default=5_242_880, ge=1024)
    skip_browse_events: bool = True
    thread_dedupe: bool = True
    failure_log_level: str = Field(default="warning")


class ArchiveConfig(BaseModel):
    upload_to_oss: bool = True
    bucket: str = "trademind-chat-session"
    keep_local_after_upload: bool = True
    retry_on_failure: bool = True
    max_retry_attempts: int = Field(default=3, ge=1, le=10)


class SummarizerModelConfig(BaseModel):
    model: str | None = None  # null → fallback to lead-agent default
    max_tokens: int = Field(default=4096, ge=256)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    thinking_enabled: bool = False


class SummarizerConfig(BaseModel):
    model: SummarizerModelConfig = Field(default_factory=SummarizerModelConfig)
    tool_call_budget: int = Field(default=10, ge=0)
    output_token_budget: int = Field(default=2000, ge=256)
    input_token_budget: int = Field(default=8000, ge=1024)


class DecayConfig(BaseModel):
    recently_quiet_runs_to_drop: int = Field(default=2, ge=1)


class InjectionConfig(BaseModel):
    max_tokens: int = Field(default=2000, ge=256)
    show_recently_quiet: bool = True
    show_open_questions: bool = True


class TenantProfileConfig(BaseModel):
    enabled: bool = True
    storage_path: str = ""  # empty → {base_dir}/tenant_profile

    facts: FactsConfig = Field(default_factory=FactsConfig)
    observation: ObservationConfig = Field(default_factory=ObservationConfig)
    archive: ArchiveConfig = Field(default_factory=ArchiveConfig)
    trigger: TriggerConfig = Field(default_factory=TriggerConfig)
    summarizer: SummarizerConfig = Field(default_factory=SummarizerConfig)
    decay: DecayConfig = Field(default_factory=DecayConfig)
    top_k: TopKConfig = Field(default_factory=TopKConfig)
    injection: InjectionConfig = Field(default_factory=InjectionConfig)


_config: TenantProfileConfig = TenantProfileConfig()


def get_tenant_profile_config() -> TenantProfileConfig:
    return _config


def set_tenant_profile_config(config: TenantProfileConfig) -> None:
    global _config
    _config = config


def load_tenant_profile_config_from_dict(config_dict: dict[str, Any]) -> None:
    global _config
    _config = TenantProfileConfig(**config_dict)


def reset_tenant_profile_config_for_tests() -> None:
    global _config
    _config = TenantProfileConfig()
