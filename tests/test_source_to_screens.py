"""tests for source_to_screens mode and ReverseIndexBuilder"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from rails_lens.analyzers.reverse_index_builder import (
    ReverseIndex,
    ReverseIndexBuilder,
    _build_controller_action,
    _template_to_controller_action,
)
from rails_lens.config import RailsLensConfig
from rails_lens.errors import RailsRunnerExecutionError
from rails_lens.tools.screen_map import (
    ScreenMapInput,
    ScreenMapMode,
    SourceToScreensOutput,
    _determine_impact_level,
    _determine_source_type,
    _fallback_source_to_screens,
    _file_path_to_class_name,
    _source_to_screens_impl,
)

# ============================================================
# フィクスチャ
# ============================================================


@pytest.fixture
def rails_project(tmp_path: Path) -> Path:
    """簡易 Rails プロジェクト構造"""
    project = tmp_path / "myapp"
    project.mkdir()
    (project / "Gemfile").write_text("gem 'rails'\n")
    (project / "config").mkdir()
    (project / "config" / "routes.rb").write_text("Rails.application.routes.draw {}\n")
    for d in [
        "app/views/users",
        "app/views/shared",
        "app/views/layouts",
        "app/helpers",
        "app/models",
        "app/decorators",
        "app/presenters",
    ]:
        (project / d).mkdir(parents=True, exist_ok=True)
    return project


@pytest.fixture
def config(rails_project: Path) -> RailsLensConfig:
    return RailsLensConfig(rails_project_path=rails_project)


@pytest.fixture
def builder(config: RailsLensConfig) -> ReverseIndexBuilder:
    return ReverseIndexBuilder(config)


# ============================================================
# ユーティリティ関数のテスト
# ============================================================


class TestDetermineSourceType:
    def test_partial(self) -> None:
        assert _determine_source_type("app/views/shared/_nav.html.erb") == "partial"

    def test_helper(self) -> None:
        assert _determine_source_type("app/helpers/users_helper.rb") == "helper"

    def test_model(self) -> None:
        assert _determine_source_type("app/models/user.rb") == "model"

    def test_decorator(self) -> None:
        assert _determine_source_type("app/decorators/user_decorator.rb") == "decorator"

    def test_presenter(self) -> None:
        assert _determine_source_type("app/presenters/user_presenter.rb") == "presenter"

    def test_template_non_partial(self) -> None:
        # 非パーシャルテンプレートもソース種別は partial 系
        assert _determine_source_type("app/views/users/show.html.erb") == "partial"


class TestDetermineImpactLevel:
    def test_via_layout_is_critical(self) -> None:
        assert _determine_impact_level(1, via_layout=True) == "critical"

    def test_count_10_is_critical(self) -> None:
        assert _determine_impact_level(10) == "critical"

    def test_count_11_is_critical(self) -> None:
        assert _determine_impact_level(11) == "critical"

    def test_count_9_is_high(self) -> None:
        assert _determine_impact_level(9) == "high"

    def test_count_5_is_high(self) -> None:
        assert _determine_impact_level(5) == "high"

    def test_count_4_is_moderate(self) -> None:
        assert _determine_impact_level(4) == "moderate"

    def test_count_2_is_moderate(self) -> None:
        assert _determine_impact_level(2) == "moderate"

    def test_count_1_is_low(self) -> None:
        assert _determine_impact_level(1) == "low"

    def test_count_0_is_low(self) -> None:
        assert _determine_impact_level(0) == "low"

    def test_str_count_is_critical(self) -> None:
        assert _determine_impact_level("all (layout)") == "critical"  # type: ignore[arg-type]


class TestBuildControllerAction:
    def test_simple(self) -> None:
        assert _build_controller_action("users", "show") == "UsersController#show"

    def test_namespaced(self) -> None:
        assert _build_controller_action("admin/users", "index") == "Admin::UsersController#index"

    def test_api_versioned(self) -> None:
        result = _build_controller_action("api/v1/users", "index")
        assert result == "Api::V1::UsersController#index"


class TestTemplateToControllerAction:
    def test_simple(self) -> None:
        result = _template_to_controller_action("app/views/users/show.html.erb")
        assert result == "UsersController#show"

    def test_partial_returns_none(self) -> None:
        result = _template_to_controller_action("app/views/shared/_nav.html.erb")
        assert result is None

    def test_layout_returns_none(self) -> None:
        result = _template_to_controller_action("app/views/layouts/application.html.erb")
        assert result is None

    def test_namespaced(self) -> None:
        result = _template_to_controller_action("app/views/admin/users/index.html.erb")
        assert result == "Admin::UsersController#index"


class TestFilePathToClassName:
    def test_simple(self) -> None:
        assert _file_path_to_class_name("app/models/user.rb") == "User"

    def test_snake_case(self) -> None:
        assert _file_path_to_class_name("app/models/blog_post.rb") == "BlogPost"

    def test_decorator(self) -> None:
        assert _file_path_to_class_name("app/decorators/user_decorator.rb") == "UserDecorator"


# ============================================================
# ReverseIndexBuilder のテスト
# ============================================================


class TestReverseIndexBuilderCache:
    def test_cache_miss_returns_none(self, builder: ReverseIndexBuilder) -> None:
        assert builder.load_cache() is None

    def test_save_and_load(
        self, builder: ReverseIndexBuilder, rails_project: Path
    ) -> None:
        index = ReverseIndex()
        index.partials["app/views/shared/_nav.html.erb"] = [
            {
                "screen_name": "ユーザー一覧",
                "controller_action": "UsersController#index",
                "url_pattern": "/users",
                "included_via": "app/views/users/index.html.erb:3",
                "via_partial": True,
                "is_api": False,
                "attributes_used": [],
                "methods_used": [],
            }
        ]
        builder.save_cache(index)
        loaded = builder.load_cache()
        assert loaded is not None
        assert "app/views/shared/_nav.html.erb" in loaded.partials

    def test_cache_invalidated_on_routes_change(
        self, builder: ReverseIndexBuilder, rails_project: Path
    ) -> None:
        index = ReverseIndex()
        builder.save_cache(index)

        # routes.rb を変更
        import time
        time.sleep(0.01)
        routes_file = rails_project / "config" / "routes.rb"
        routes_file.write_text("# changed\n")

        loaded = builder.load_cache()
        assert loaded is None


class TestReverseIndexBuilderBuildFromMappings:
    def test_partial_indexed(
        self, builder: ReverseIndexBuilder, rails_project: Path
    ) -> None:
        # テンプレートとパーシャルを作成
        (rails_project / "app" / "views" / "users" / "index.html.erb").write_text(
            "<%= render partial: 'shared/nav' %>\n<h1>Users</h1>\n"
        )
        (rails_project / "app" / "views" / "shared" / "_nav.html.erb").write_text(
            "<nav>nav</nav>\n"
        )

        mappings = [
            {
                "verb": "GET",
                "path": "/users",
                "controller": "users",
                "action": "index",
                "conventional_template": "users/index",
                "explicit_render": None,
                "layout": None,
            }
        ]
        index = builder.build_from_mappings(mappings)
        # パーシャルがインデックスに含まれている
        assert any(
            "shared/_nav" in k or "_nav" in k for k in index.partials
        )

    def test_layout_indexed(
        self, builder: ReverseIndexBuilder, rails_project: Path
    ) -> None:
        (rails_project / "app" / "views" / "users" / "show.html.erb").write_text(
            "<p>body</p>\n"
        )
        (rails_project / "app" / "views" / "layouts" / "application.html.erb").write_text(
            "<html><%= yield %></html>\n"
        )

        mappings = [
            {
                "verb": "GET",
                "path": "/users/:id",
                "controller": "users",
                "action": "show",
                "conventional_template": "users/show",
                "explicit_render": None,
                "layout": "application",
            }
        ]
        index = builder.build_from_mappings(mappings)
        assert "app/views/layouts/application.html.erb" in index.layouts

    def test_helper_indexed(
        self, builder: ReverseIndexBuilder, rails_project: Path
    ) -> None:
        (rails_project / "app" / "views" / "users" / "show.html.erb").write_text(
            "<p><%= user_badge(@user) %></p>\n"
        )
        mappings = [
            {
                "verb": "GET",
                "path": "/users/:id",
                "controller": "users",
                "action": "show",
                "conventional_template": "users/show",
                "explicit_render": None,
                "layout": None,
            }
        ]
        index = builder.build_from_mappings(mappings)
        assert "user_badge" in index.helpers

    def test_model_indexed(
        self, builder: ReverseIndexBuilder, rails_project: Path
    ) -> None:
        (rails_project / "app" / "views" / "users" / "show.html.erb").write_text(
            "<p><%= @user.name %></p>\n<p><%= @user.email %></p>\n"
        )
        mappings = [
            {
                "verb": "GET",
                "path": "/users/:id",
                "controller": "users",
                "action": "show",
                "conventional_template": "users/show",
                "explicit_render": None,
                "layout": None,
            }
        ]
        index = builder.build_from_mappings(mappings)
        assert "User" in index.models


class TestReverseIndexBuilderGrepFallback:
    def test_partial_grep_finds_usage(
        self, builder: ReverseIndexBuilder, rails_project: Path
    ) -> None:
        (rails_project / "app" / "views" / "users" / "index.html.erb").write_text(
            "<%= render partial: 'shared/nav' %>\n"
        )
        (rails_project / "app" / "views" / "shared" / "_nav.html.erb").write_text(
            "<nav></nav>\n"
        )
        refs = builder.build_partial_index_by_grep("app/views/shared/_nav.html.erb")
        assert len(refs) >= 1

    def test_helper_grep_finds_usage(
        self, builder: ReverseIndexBuilder, rails_project: Path
    ) -> None:
        (rails_project / "app" / "views" / "users" / "show.html.erb").write_text(
            "<%= user_badge(@user) %>\n"
        )
        refs = builder.build_helper_index_by_grep("user_badge")
        assert len(refs) >= 1

    def test_model_grep_finds_usage(
        self, builder: ReverseIndexBuilder, rails_project: Path
    ) -> None:
        (rails_project / "app" / "views" / "users" / "show.html.erb").write_text(
            "<%= @user.name %>\n"
        )
        refs = builder.build_model_index_by_grep("User")
        assert len(refs) >= 1


# ============================================================
# source_to_screens 統合テスト（bridge モック）
# ============================================================


class TestSourceToScreensImpl:
    @pytest.fixture
    def mock_bridge(self, rails_project: Path) -> MagicMock:
        # テンプレートとパーシャルを事前に作成
        (rails_project / "app" / "views" / "users" / "index.html.erb").write_text(
            "<%= render partial: 'shared/nav' %>\n<h1>Users</h1>\n"
        )
        (rails_project / "app" / "views" / "users" / "show.html.erb").write_text(
            "<%= render partial: 'shared/nav' %>\n<p><%= @user.name %></p>\n"
        )
        (rails_project / "app" / "views" / "shared" / "_nav.html.erb").write_text(
            "<nav>nav</nav>\n"
        )

        bridge = MagicMock()
        bridge.execute = AsyncMock(return_value={
            "mode": "all",
            "total": 2,
            "mappings": [
                {
                    "verb": "GET",
                    "path": "/users",
                    "controller": "users",
                    "action": "index",
                    "conventional_template": "users/index",
                    "explicit_render": None,
                    "layout": None,
                },
                {
                    "verb": "GET",
                    "path": "/users/:id",
                    "controller": "users",
                    "action": "show",
                    "conventional_template": "users/show",
                    "explicit_render": None,
                    "layout": None,
                },
            ],
        })
        return bridge

    @pytest.mark.asyncio
    async def test_partial_source_type(
        self,
        config: RailsLensConfig,
        mock_bridge: MagicMock,
    ) -> None:
        params = ScreenMapInput(
            mode=ScreenMapMode.SOURCE_TO_SCREENS,
            file_path="app/views/shared/_nav.html.erb",
        )
        output = await _source_to_screens_impl(params, mock_bridge, config)
        assert isinstance(output, SourceToScreensOutput)
        assert output.source_type == "partial"
        assert output.source_file == "app/views/shared/_nav.html.erb"

    @pytest.mark.asyncio
    async def test_partial_screens_found(
        self,
        config: RailsLensConfig,
        mock_bridge: MagicMock,
    ) -> None:
        params = ScreenMapInput(
            mode=ScreenMapMode.SOURCE_TO_SCREENS,
            file_path="app/views/shared/_nav.html.erb",
        )
        output = await _source_to_screens_impl(params, mock_bridge, config)
        # 2画面で使用されている
        assert output.total_screen_count >= 1

    @pytest.mark.asyncio
    async def test_impact_level_critical_via_layout(
        self,
        config: RailsLensConfig,
        rails_project: Path,
    ) -> None:
        """レイアウト経由のパーシャルは critical"""
        (rails_project / "app" / "views" / "layouts" / "application.html.erb").write_text(
            "<%= render partial: 'shared/nav' %>\n<%= yield %>\n"
        )
        bridge = MagicMock()
        bridge.execute = AsyncMock(return_value={
            "mode": "all",
            "total": 1,
            "mappings": [
                {
                    "verb": "GET",
                    "path": "/users",
                    "controller": "users",
                    "action": "index",
                    "conventional_template": "users/index",
                    "explicit_render": None,
                    "layout": "application",
                },
            ],
        })
        params = ScreenMapInput(
            mode=ScreenMapMode.SOURCE_TO_SCREENS,
            file_path="app/views/layouts/application.html.erb",
        )
        output = await _source_to_screens_impl(params, bridge, config)
        assert output.impact_level == "critical"

    @pytest.mark.asyncio
    async def test_bridge_failure_fallback(
        self,
        config: RailsLensConfig,
        rails_project: Path,
    ) -> None:
        """bridge 失敗時はファイルベースフォールバックを使う"""
        (rails_project / "app" / "views" / "users" / "index.html.erb").write_text(
            "<%= render partial: 'shared/nav' %>\n"
        )
        (rails_project / "app" / "views" / "shared" / "_nav.html.erb").write_text(
            "<nav></nav>\n"
        )
        failing_bridge = MagicMock()
        failing_bridge.execute = AsyncMock(
            side_effect=RailsRunnerExecutionError("Rails not found")
        )
        params = ScreenMapInput(
            mode=ScreenMapMode.SOURCE_TO_SCREENS,
            file_path="app/views/shared/_nav.html.erb",
        )
        output = await _source_to_screens_impl(params, failing_bridge, config)
        assert isinstance(output, SourceToScreensOutput)
        assert output._metadata is not None
        assert output._metadata.get("source") == "file_analysis"


# ============================================================
# _fallback_source_to_screens のテスト
# ============================================================


class TestFallbackSourceToScreens:
    def test_partial_fallback(
        self, builder: ReverseIndexBuilder, rails_project: Path
    ) -> None:
        (rails_project / "app" / "views" / "users" / "index.html.erb").write_text(
            "<%= render partial: 'shared/nav' %>\n"
        )
        output = _fallback_source_to_screens(
            "app/views/shared/_nav.html.erb", "partial", builder
        )
        assert isinstance(output, SourceToScreensOutput)
        assert output.source_type == "partial"
        assert output._metadata is not None
        assert output._metadata["source"] == "file_analysis"

    def test_helper_fallback(
        self, builder: ReverseIndexBuilder, rails_project: Path
    ) -> None:
        helper_file = rails_project / "app" / "helpers" / "users_helper.rb"
        helper_file.write_text(
            "module UsersHelper\n  def user_badge(user)\n    user.name\n  end\nend\n"
        )
        (rails_project / "app" / "views" / "users" / "show.html.erb").write_text(
            "<%= user_badge(@user) %>\n"
        )
        output = _fallback_source_to_screens(
            "app/helpers/users_helper.rb", "helper", builder
        )
        assert isinstance(output, SourceToScreensOutput)
        assert output.source_type == "helper"
        assert output._metadata is not None
        assert output._metadata["source"] == "file_analysis"

    def test_model_fallback(
        self, builder: ReverseIndexBuilder, rails_project: Path
    ) -> None:
        (rails_project / "app" / "views" / "users" / "show.html.erb").write_text(
            "<p><%= @user.name %></p>\n"
        )
        output = _fallback_source_to_screens(
            "app/models/user.rb", "model", builder
        )
        assert isinstance(output, SourceToScreensOutput)
        assert output.source_type == "model"
        assert output._metadata is not None
        assert output._metadata["source"] == "file_analysis"


# ============================================================
# impact_level 判定の境界値テスト
# ============================================================


class TestImpactLevelBoundary:
    def test_9_screens_high(self) -> None:
        assert _determine_impact_level(9) == "high"

    def test_10_screens_critical(self) -> None:
        assert _determine_impact_level(10) == "critical"

    def test_4_screens_moderate(self) -> None:
        assert _determine_impact_level(4) == "moderate"

    def test_5_screens_high(self) -> None:
        assert _determine_impact_level(5) == "high"
