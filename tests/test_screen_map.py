"""tests for screen_map tool and ScreenNameResolver"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from rails_lens.analyzers.screen_name_resolver import (
    ScreenNameResolver,
    parse_controller_action,
)
from rails_lens.config import RailsLensConfig
from rails_lens.errors import RailsRunnerExecutionError
from rails_lens.tools.screen_map import (
    ScreenMapInput,
    ScreenMapMode,
    ScreenToSourceOutput,
    _fallback_screen_to_source,
    _resolve_from_url_fallback,
    _screen_to_source_impl,
)

# ============================================================
# フィクスチャ
# ============================================================


@pytest.fixture
def rails_project(tmp_path: Path) -> Path:
    """簡易Railsプロジェクト構造"""
    project = tmp_path / "myapp"
    project.mkdir()
    (project / "Gemfile").write_text("gem 'rails'\n")
    (project / "config").mkdir()
    (project / "app" / "views" / "users").mkdir(parents=True)
    (project / "app" / "views" / "layouts").mkdir(parents=True)
    (project / "app" / "helpers").mkdir(parents=True)
    (project / "app" / "models").mkdir(parents=True)
    return project


@pytest.fixture
def config(rails_project: Path) -> RailsLensConfig:
    return RailsLensConfig(rails_project_path=rails_project)


@pytest.fixture
def resolver(config: RailsLensConfig) -> ScreenNameResolver:
    return ScreenNameResolver(config)


# ============================================================
# parse_controller_action のテスト
# ============================================================


class TestParseControllerAction:
    def test_simple_controller(self) -> None:
        resource, action, namespaces = parse_controller_action("UsersController#show")
        assert resource == "users"
        assert action == "show"
        assert namespaces == []

    def test_namespaced_admin(self) -> None:
        resource, action, namespaces = parse_controller_action(
            "Admin::UsersController#index"
        )
        assert resource == "users"
        assert action == "index"
        assert namespaces == ["Admin"]

    def test_api_versioned(self) -> None:
        resource, action, namespaces = parse_controller_action(
            "Api::V1::UsersController#index"
        )
        assert resource == "users"
        assert action == "index"
        assert namespaces == ["Api", "V1"]

    def test_snake_case_controller(self) -> None:
        resource, action, namespaces = parse_controller_action("users#index")
        assert resource == "users"
        assert action == "index"
        assert namespaces == []


# ============================================================
# ScreenNameResolver のユニットテスト
# ============================================================


class TestRestfulConvention:
    """RESTful規約からの自動生成テスト"""

    def test_index_ja(self, resolver: ScreenNameResolver) -> None:
        name, source = resolver.resolve("UsersController#index", locale="ja")
        assert "一覧" in name
        assert source == "restful_convention"

    def test_show_ja(self, resolver: ScreenNameResolver) -> None:
        name, source = resolver.resolve("UsersController#show", locale="ja")
        assert "詳細" in name
        assert source == "restful_convention"

    def test_new_ja(self, resolver: ScreenNameResolver) -> None:
        name, source = resolver.resolve("UsersController#new", locale="ja")
        assert "新規作成" in name
        assert source == "restful_convention"

    def test_edit_ja(self, resolver: ScreenNameResolver) -> None:
        name, source = resolver.resolve("UsersController#edit", locale="ja")
        assert "編集" in name
        assert source == "restful_convention"

    def test_index_en(self, resolver: ScreenNameResolver) -> None:
        name, source = resolver.resolve("UsersController#index", locale="en")
        assert "List" in name
        assert source == "restful_convention"

    def test_show_en(self, resolver: ScreenNameResolver) -> None:
        name, source = resolver.resolve("UsersController#show", locale="en")
        assert "Detail" in name
        assert source == "restful_convention"


class TestI18nResolution:
    """i18nキー優先度テスト"""

    def test_uses_i18n_key(self, resolver: ScreenNameResolver) -> None:
        i18n = {"users.show.title": "ユーザー詳細"}
        name, source = resolver.resolve(
            "UsersController#show", i18n_keys=i18n, locale="ja"
        )
        assert name == "ユーザー詳細"
        assert source == "i18n:users.show.title"

    def test_page_title_key(self, resolver: ScreenNameResolver) -> None:
        i18n = {"users.index.page_title": "ユーザー管理"}
        name, source = resolver.resolve(
            "UsersController#index", i18n_keys=i18n, locale="ja"
        )
        assert name == "ユーザー管理"
        assert source == "i18n:users.index.page_title"

    def test_empty_i18n_falls_back(self, resolver: ScreenNameResolver) -> None:
        name, source = resolver.resolve("UsersController#show", i18n_keys={}, locale="ja")
        assert source == "restful_convention"

    def test_i18n_beats_template(
        self, resolver: ScreenNameResolver, config: RailsLensConfig
    ) -> None:
        # Create a template with h1
        tpl_path = config.rails_project_path / "app" / "views" / "users" / "show.html.erb"
        tpl_path.write_text("<h1>テンプレートタイトル</h1>\n")
        i18n = {"users.show.title": "i18nタイトル"}
        name, source = resolver.resolve(
            "UsersController#show",
            i18n_keys=i18n,
            template_path=str(tpl_path.relative_to(config.rails_project_path)),
            locale="ja",
        )
        assert name == "i18nタイトル"
        assert source == "i18n:users.show.title"


class TestTemplateExtraction:
    """テンプレートからのタイトル抽出テスト"""

    def test_h1_tag(self, resolver: ScreenNameResolver, config: RailsLensConfig) -> None:
        tpl_path = config.rails_project_path / "app" / "views" / "users" / "show.html.erb"
        tpl_path.write_text("<h1>ユーザー詳細</h1>\n<p>content</p>\n")
        name, source = resolver.resolve(
            "UsersController#show",
            template_path="app/views/users/show.html.erb",
            locale="ja",
        )
        assert name == "ユーザー詳細"
        assert source == "h1_tag"

    def test_content_for_title(self, resolver: ScreenNameResolver, config: RailsLensConfig) -> None:
        tpl_path = config.rails_project_path / "app" / "views" / "users" / "show.html.erb"
        tpl_path.write_text('<% content_for :title, "プロフィール" %>\n<div>body</div>\n')
        name, source = resolver.resolve(
            "UsersController#show",
            template_path="app/views/users/show.html.erb",
            locale="ja",
        )
        assert name == "プロフィール"
        assert source == "content_for_title"

    def test_provide_title(self, resolver: ScreenNameResolver, config: RailsLensConfig) -> None:
        tpl_path = config.rails_project_path / "app" / "views" / "users" / "show.html.erb"
        tpl_path.write_text('<% provide :title, "ユーザー編集" %>\n<div>form</div>\n')
        name, source = resolver.resolve(
            "UsersController#show",
            template_path="app/views/users/show.html.erb",
            locale="ja",
        )
        assert name == "ユーザー編集"
        assert source == "content_for_title"

    def test_missing_template_fallback(self, resolver: ScreenNameResolver) -> None:
        name, source = resolver.resolve(
            "UsersController#show",
            template_path="app/views/nonexistent/show.html.erb",
            locale="ja",
        )
        assert source == "restful_convention"


class TestNamespaceHandling:
    """名前空間プレフィックス・サフィックステスト"""

    def test_admin_prefix_ja(self, resolver: ScreenNameResolver) -> None:
        name, source = resolver.resolve("Admin::UsersController#index", locale="ja")
        assert name.startswith("管理画面 - ")
        assert source == "restful_convention"

    def test_api_suffix_ja(self, resolver: ScreenNameResolver) -> None:
        name, source = resolver.resolve("Api::V1::UsersController#index", locale="ja")
        assert "(API" in name
        assert "v1" in name.lower()
        assert source == "restful_convention"

    def test_admin_prefix_en(self, resolver: ScreenNameResolver) -> None:
        name, source = resolver.resolve("Admin::UsersController#index", locale="en")
        assert name.startswith("Admin - ")

    def test_api_suffix_en(self, resolver: ScreenNameResolver) -> None:
        name, source = resolver.resolve("Api::V1::UsersController#index", locale="en")
        assert "(API" in name


# ============================================================
# screen_to_source 統合テスト（モック使用）
# ============================================================


class TestScreenToSourceIntegration:
    """screen_to_source モードの統合テスト（bridge をモック）"""

    @pytest.fixture
    def mock_bridge(self) -> MagicMock:
        bridge = MagicMock()
        bridge.execute = AsyncMock(return_value={
            "mode": "single",
            "mappings": [{
                "verb": "GET",
                "path": "/users/:id",
                "controller": "users",
                "action": "show",
                "route_name": "user",
                "layout": "application",
                "conventional_template": "users/show",
                "explicit_render": None,
                "i18n_title_keys": {"users.show.title": "ユーザー詳細"},
            }],
        })
        return bridge

    @pytest.mark.asyncio
    async def test_basic_screen_to_source(
        self, config: RailsLensConfig, rails_project: Path, mock_bridge: MagicMock
    ) -> None:
        # テンプレートファイルを作成
        tpl = rails_project / "app" / "views" / "users" / "show.html.erb"
        tpl.write_text("<h1>ユーザー詳細</h1>\n<p><%= @user.name %></p>\n")

        params = ScreenMapInput(
            mode=ScreenMapMode.SCREEN_TO_SOURCE,
            controller_action="UsersController#show",
        )
        output = await _screen_to_source_impl(params, mock_bridge, config)

        assert isinstance(output, ScreenToSourceOutput)
        assert output.screen.controller_action == "UsersController#show"
        assert output.screen.screen_name == "ユーザー詳細"
        assert output.screen.screen_name_source == "i18n:users.show.title"
        assert output.screen.http_method == "GET"
        assert output.screen.url_pattern == "/users/:id"

    @pytest.mark.asyncio
    async def test_model_refs_extracted(
        self, config: RailsLensConfig, rails_project: Path, mock_bridge: MagicMock
    ) -> None:
        tpl = rails_project / "app" / "views" / "users" / "show.html.erb"
        tpl.write_text(
            "<p><%= @user.name %></p>\n<p><%= @user.email %></p>\n"
        )
        params = ScreenMapInput(
            mode=ScreenMapMode.SCREEN_TO_SOURCE,
            controller_action="UsersController#show",
        )
        output = await _screen_to_source_impl(params, mock_bridge, config)
        model_names = [m.model for m in output.models_referenced]
        assert "User" in model_names
        user_ref = next(m for m in output.models_referenced if m.model == "User")
        assert "name" in user_ref.attributes_accessed
        assert "email" in user_ref.attributes_accessed

    @pytest.mark.asyncio
    async def test_layout_detected(
        self, config: RailsLensConfig, rails_project: Path, mock_bridge: MagicMock
    ) -> None:
        tpl = rails_project / "app" / "views" / "users" / "show.html.erb"
        tpl.write_text("<p>body</p>\n")
        layout = rails_project / "app" / "views" / "layouts" / "application.html.erb"
        layout.write_text(
            "<html><body>\n<% yield :header %>\n<%= yield %>\n</body></html>\n"
        )
        params = ScreenMapInput(
            mode=ScreenMapMode.SCREEN_TO_SOURCE,
            controller_action="UsersController#show",
        )
        output = await _screen_to_source_impl(params, mock_bridge, config)
        assert output.layout is not None
        assert "application" in output.layout.file

    @pytest.mark.asyncio
    async def test_bridge_failure_fallback(
        self, config: RailsLensConfig, rails_project: Path
    ) -> None:
        """bridgeが失敗した場合、ファイルベースフォールバックが動作する"""
        # テンプレート作成
        tpl = rails_project / "app" / "views" / "users" / "show.html.erb"
        tpl.write_text("<h1>ユーザー詳細</h1>\n")

        failing_bridge = MagicMock()
        failing_bridge.execute = AsyncMock(
            side_effect=RailsRunnerExecutionError("Rails not found")
        )

        params = ScreenMapInput(
            mode=ScreenMapMode.SCREEN_TO_SOURCE,
            controller_action="UsersController#show",
        )
        output = await _screen_to_source_impl(params, failing_bridge, config)

        assert isinstance(output, ScreenToSourceOutput)
        assert output._metadata is not None
        assert output._metadata.get("source") == "file_analysis"

    @pytest.mark.asyncio
    async def test_url_to_controller_action(
        self, config: RailsLensConfig, rails_project: Path
    ) -> None:
        """URLからコントローラ#アクションを解決する"""
        tpl = rails_project / "app" / "views" / "users" / "show.html.erb"
        tpl.write_text("<p>body</p>\n")

        bridge = MagicMock()
        # First call: "all" mode for URL resolution
        # Second call: "single" mode for route details
        bridge.execute = AsyncMock(side_effect=[
            {
                "mode": "all",
                "total": 1,
                "mappings": [{
                    "verb": "GET",
                    "path": "/users/:id",
                    "controller": "users",
                    "action": "show",
                    "route_name": "user",
                    "layout": None,
                    "conventional_template": "users/show",
                    "explicit_render": None,
                    "i18n_title_keys": {},
                }],
            },
            {
                "mode": "single",
                "mappings": [{
                    "verb": "GET",
                    "path": "/users/:id",
                    "controller": "users",
                    "action": "show",
                    "route_name": "user",
                    "layout": None,
                    "conventional_template": "users/show",
                    "explicit_render": None,
                    "i18n_title_keys": {},
                }],
            },
        ])

        params = ScreenMapInput(
            mode=ScreenMapMode.SCREEN_TO_SOURCE,
            url="/users/123",
        )
        output = await _screen_to_source_impl(params, bridge, config)
        assert output.screen.controller_action == "UsersController#show"


class TestFallbackScreenToSource:
    """ファイルベースフォールバックのテスト"""

    def test_fallback_returns_output(
        self, config: RailsLensConfig, rails_project: Path
    ) -> None:
        tpl = rails_project / "app" / "views" / "users" / "show.html.erb"
        tpl.write_text("<p>body</p>\n")

        output = _fallback_screen_to_source("UsersController#show", config)
        assert isinstance(output, ScreenToSourceOutput)
        assert output._metadata is not None
        assert output._metadata["source"] == "file_analysis"
        assert output.screen.controller_action == "UsersController#show"

    def test_fallback_restful_convention(
        self, config: RailsLensConfig, rails_project: Path
    ) -> None:
        output = _fallback_screen_to_source("UsersController#index", config)
        assert "一覧" in output.screen.screen_name
        assert output.screen.screen_name_source == "restful_convention"


class TestResolveFromUrlFallback:
    """_resolve_from_url_fallback のテスト"""

    @pytest.fixture
    def project_with_routes(self, rails_project: Path) -> Path:
        routes_rb = rails_project / "config" / "routes.rb"
        routes_rb.write_text(
            "Rails.application.routes.draw do\n"
            "  resources :issues\n"
            "  resources :projects\n"
            "end\n"
        )
        return rails_project

    def test_static_url_resolved(
        self, config: RailsLensConfig, project_with_routes: Path
    ) -> None:
        """静的URL（/issues）が正しくCA解決される"""
        ca = _resolve_from_url_fallback("/issues", config)
        assert ca == "IssuesController#index"

    def test_dynamic_url_resolved(
        self, config: RailsLensConfig, project_with_routes: Path
    ) -> None:
        """動的パラメータURL（/projects/1）が正しくCA解決される"""
        ca = _resolve_from_url_fallback("/projects/1", config)
        assert ca == "ProjectsController#show"

    def test_unknown_url_returns_none(
        self, config: RailsLensConfig, project_with_routes: Path
    ) -> None:
        """存在しないURLはNoneを返す"""
        ca = _resolve_from_url_fallback("/nonexistent/path", config)
        assert ca is None

    def test_missing_routes_file_returns_none(
        self, config: RailsLensConfig, rails_project: Path
    ) -> None:
        """routes.rbがない場合はNoneを返す（routes.rbなしのfixtureを使用）"""
        ca = _resolve_from_url_fallback("/issues", config)
        assert ca is None

    @pytest.mark.asyncio
    async def test_bridge_failure_with_url_uses_fallback(
        self, config: RailsLensConfig, rails_project: Path
    ) -> None:
        """bridge失敗時にURLフォールバックが動作し_fallback_screen_to_sourceが呼ばれる"""
        routes_rb = rails_project / "config" / "routes.rb"
        routes_rb.write_text(
            "Rails.application.routes.draw do\n"
            "  resources :issues\n"
            "end\n"
        )
        tpl = rails_project / "app" / "views" / "issues" / "index.html.erb"
        tpl.parent.mkdir(parents=True, exist_ok=True)
        tpl.write_text("<h1>Issues</h1>\n")

        failing_bridge = MagicMock()
        failing_bridge.execute = AsyncMock(
            side_effect=RailsRunnerExecutionError("Rails not found")
        )

        params = ScreenMapInput(
            mode=ScreenMapMode.SCREEN_TO_SOURCE,
            url="/issues",
        )
        output = await _screen_to_source_impl(params, failing_bridge, config)

        assert isinstance(output, ScreenToSourceOutput)
        assert output.screen.controller_action == "IssuesController#index"
        assert output._metadata is not None
        assert output._metadata["source"] == "file_analysis"
