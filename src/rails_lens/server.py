"""rails-lens MCP Server"""
from mcp.server.fastmcp import FastMCP

from rails_lens.analyzers.grep_search import GrepSearch
from rails_lens.bridge.runner import RailsBridge
from rails_lens.cache.manager import CacheManager
from rails_lens.config import RailsLensConfig, load_config
from rails_lens.tools.dependency_graph import register as register_dep_graph
from rails_lens.tools.find_references import register as register_find_refs
from rails_lens.tools.introspect_model import register as register_introspect
from rails_lens.tools.list_models import register as register_list_models
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


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
