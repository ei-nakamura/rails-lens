"""rails-lens MCP Server"""
from mcp.server.fastmcp import FastMCP

from rails_lens.analyzers.grep_search import GrepSearch
from rails_lens.bridge.runner import RailsBridge
from rails_lens.cache.manager import CacheManager
from rails_lens.config import RailsLensConfig, load_config
from rails_lens.tools.analyze_concern import register as register_analyze_concern
from rails_lens.tools.circular_dependencies import (
    register as register_circular_dependencies,
)
from rails_lens.tools.data_flow import register as register_data_flow
from rails_lens.tools.dead_code import register as register_dead_code
from rails_lens.tools.dependency_graph import register as register_dep_graph
from rails_lens.tools.explain_method_resolution import (
    register as register_explain_method_resolution,
)
from rails_lens.tools.extract_concern_candidate import (
    register as register_extract_concern_candidate,
)
from rails_lens.tools.find_references import register as register_find_refs
from rails_lens.tools.gem_introspect import register as register_gem_introspect
from rails_lens.tools.get_routes import register as register_get_routes
from rails_lens.tools.get_schema import register as register_get_schema
from rails_lens.tools.impact_analysis import register as register_impact_analysis
from rails_lens.tools.introspect_model import register as register_introspect
from rails_lens.tools.list_models import register as register_list_models
from rails_lens.tools.migration_context import register as register_migration_context
from rails_lens.tools.refresh_cache import register as register_refresh_cache
from rails_lens.tools.test_mapping import register as register_test_mapping
from rails_lens.tools.trace_callback_chain import register as register_trace

mcp = FastMCP(
    "rails-lens",
    instructions=(
        "Rails application introspection server. "
        "Use rails_lens_introspect_model to understand model dependencies "
        "before making changes."
    ),
)

# --- グローバル状態（サーバーライフサイクルで共有） ---
_config: RailsLensConfig | None = None
_bridge: RailsBridge | None = None
_cache: CacheManager | None = None
_grep: GrepSearch | None = None


def _ensure_initialized() -> tuple[RailsLensConfig, RailsBridge, CacheManager, GrepSearch]:
    """遅延初期化。初回ツール呼び出し時に設定を読み込む。"""
    global _config, _bridge, _cache, _grep
    if _config is None:
        _config = load_config()
        _bridge = RailsBridge(_config)
        _cache = CacheManager(_config)
        _grep = GrepSearch(_config)
    assert _bridge is not None
    assert _cache is not None
    assert _grep is not None
    return _config, _bridge, _cache, _grep


# --- ツール登録 ---
register_introspect(mcp, _ensure_initialized)
register_find_refs(mcp, _ensure_initialized)
register_list_models(mcp, _ensure_initialized)
register_trace(mcp, _ensure_initialized)
register_dep_graph(mcp, _ensure_initialized)
register_get_schema(mcp, _ensure_initialized)
register_get_routes(mcp, _ensure_initialized)
register_analyze_concern(mcp, _ensure_initialized)
register_refresh_cache(mcp, _ensure_initialized)
register_explain_method_resolution(mcp, _ensure_initialized)
register_gem_introspect(mcp, _ensure_initialized)
register_impact_analysis(mcp, _ensure_initialized)
register_test_mapping(mcp, _ensure_initialized)
register_dead_code(mcp, _ensure_initialized)
register_circular_dependencies(mcp, _ensure_initialized)
register_extract_concern_candidate(mcp, _ensure_initialized)
register_data_flow(mcp, _ensure_initialized)
register_migration_context(mcp, _ensure_initialized)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
