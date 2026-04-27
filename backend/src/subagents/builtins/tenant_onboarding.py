"""Tenant onboarding subagent configuration."""

from src.agents.tenant_onboarding.prompt import build_system_prompt
from src.subagents.config import SubagentConfig

TENANT_ONBOARDING_CONFIG = SubagentConfig(
    name="tenant-onboarding",
    description="""First-time tenant initialization specialist.

Use this subagent EXACTLY ONCE per tenant — on the first chat session, when
no ``profile.json`` exists yet for the tenant. It walks the user through:
  1. Channel selection (Excel uploads, interactive Q&A, or both).
  2. ERPNext seeding via the ``erpnext-cli`` skill (Company, default
     Warehouse, optional Price List, plus master data from uploads).
  3. Writing a v1 ``profile.json`` so the runtime tenant_profile summarizer
     can take over.

Do NOT use this subagent:
  - For any subsequent ad-hoc imports — those go through the lead agent.
  - When the tenant already has a ``profile.json``.
  - For anything outside ERPNext masters + profile composition.""",
    system_prompt=build_system_prompt(),
    # Inherit all parent tools — the subagent legitimately needs bash (read
    # uploads / run composer), file I/O, ask_clarification (Q&A path),
    # present_files, and skill-loading. Everything except spawning more
    # subagents (handled by disallowed_tools below).
    tools=None,
    disallowed_tools=["task"],
    model="inherit",
    # Onboarding can be chatty (Q&A + multiple CLI calls), so allow more
    # turns than general-purpose. Still bounded so a stuck flow eventually
    # surfaces instead of looping silently.
    max_turns=80,
    # Explicit superstep budget. Onboarding fans out into the most middleware
    # work of any subagent (sandbox + uploads + dangling-tool-call +
    # repeated-tool-failure + clarification on every turn, plus per-turn
    # bash + erpnext-cli + ask_clarification tool calls). The 5x default
    # multiplier (= 400) is the floor; we set it explicitly so a future
    # config tweak to ``max_turns`` doesn't silently shrink the cap below
    # what the v1 onboarding flow has historically needed.
    recursion_limit=400,
    # 30 minutes — longer than the 15-min default because the user is in
    # the loop answering questions and we don't want timeouts mid-Q&A.
    timeout_seconds=1800,
)
