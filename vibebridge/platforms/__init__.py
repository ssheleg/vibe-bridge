"""Per-OS capability packs (spec §5, research-notes §E).

`vibebridge.capabilities.build_capabilities()` dispatches here off
sys.platform. Каждый пак обязан отдавать ПОЛНУЮ карту имён (единый MCP-
контракт): способность, невозможная на платформе, присутствует как stub с
probe-статусом `unavailable` и причиной, произносимой роботом, — а не
исчезает из списка (робот получает «недоступно на этой системе», не
«unknown tool»). Fail-fast: probe на регистрации, не на вызове.
"""
