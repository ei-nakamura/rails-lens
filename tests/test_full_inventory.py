"""tests for full_inventory mode, InventoryFormatter, and ApiDetector"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from rails_lens.analyzers.api_detector import (
    _singularize,
    detect_serializer,
    is_api_controller,
    is_json_only_action,
)
from rails_lens.analyzers.inventory_formatter import InventoryFormatter
from rails_lens.config import RailsLensConfig
from rails_lens.errors import RailsRunnerExecutionError
from rails_lens.tools.screen_map import (
    FullInventoryOutput,
    ScreenEntry,
    ScreenGroup,
    ScreenMapGroupBy,
    ScreenMapInput,
    ScreenMapMode,
    SharedPartialEntry,
    _fallback_full_inventory,
    _full_inventory_impl,
    _group_screens,
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
        "app/views/orders",
        "app/views/layouts",
        "app/views/shared",
        "app/views/api/v1",
        "app/helpers",
        "app/models",
        "app/controllers",
        "app/controllers/api/v1",
        "app/serializers",
        "app/blueprints",
    ]:
        (project / d).mkdir(parents=True, exist_ok=True)
    return project


@pytest.fixture
def config(rails_project: Path) -> RailsLensConfig:
    return RailsLensConfig(rails_project_path=rails_project)


# ============================================================
# _singularize のテスト
# ============================================================


class TestSingularize:
    def test_regular_plural(self) -> None:
        assert _singularize("users") == "user"

    def test_ies_plural(self) -> None:
        assert _singularize("companies") == "company"

    def test_already_singular(self) -> None:
        assert _singularize("user") == "user"

    def test_sses(self) -> None:
        assert _singularize("classes") == "class"

    def test_ss_not_singularized(self) -> None:
        assert _singularize("address") == "address"


# ============================================================
# is_api_controller のテスト
# ============================================================


class TestIsApiController:
    def test_api_namespace(self, rails_project: Path) -> None:
        assert is_api_controller("api/v1/users", rails_project) is True

    def test_non_api_controller(self, rails_project: Path) -> None:
        assert is_api_controller("users", rails_project) is False

    def test_api_base_inherit(self, rails_project: Path) -> None:
        ctrl_file = rails_project / "app" / "controllers" / "users_controller.rb"
        ctrl_file.write_text(
            "class UsersController < ActionController::API\n  def index; end\nend\n"
        )
        assert is_api_controller("users", rails_project) is True

    def test_api_base_not_present(self, rails_project: Path) -> None:
        ctrl_file = rails_project / "app" / "controllers" / "posts_controller.rb"
        ctrl_file.write_text(
            "class PostsController < ApplicationController\n  def index; end\nend\n"
        )
        assert is_api_controller("posts", rails_project) is False


# ============================================================
# is_json_only_action のテスト
# ============================================================


class TestIsJsonOnlyAction:
    def test_respond_to_json_inline(self, rails_project: Path) -> None:
        ctrl_file = rails_project / "app" / "controllers" / "users_controller.rb"
        ctrl_file.write_text(
            "class UsersController < ApplicationController\n"
            "  def index\n"
            "    respond_to :json\n"
            "  end\n"
            "end\n"
        )
        assert is_json_only_action("users", "index", rails_project) is True

    def test_respond_to_block_json_only(self, rails_project: Path) -> None:
        ctrl_file = rails_project / "app" / "controllers" / "users_controller.rb"
        ctrl_file.write_text(
            "class UsersController < ApplicationController\n"
            "  def show\n"
            "    respond_to do |format|\n"
            "      format.json { render json: @user }\n"
            "    end\n"
            "  end\n"
            "end\n"
        )
        assert is_json_only_action("users", "show", rails_project) is True

    def test_respond_to_html_and_json(self, rails_project: Path) -> None:
        ctrl_file = rails_project / "app" / "controllers" / "users_controller.rb"
        ctrl_file.write_text(
            "class UsersController < ApplicationController\n"
            "  def index\n"
            "    respond_to do |format|\n"
            "      format.html\n"
            "      format.json { render json: @users }\n"
            "    end\n"
            "  end\n"
            "end\n"
        )
        assert is_json_only_action("users", "index", rails_project) is False

    def test_no_controller_file(self, rails_project: Path) -> None:
        assert is_json_only_action("nonexistent", "index", rails_project) is False


# ============================================================
# detect_serializer のテスト
# ============================================================


class TestDetectSerializer:
    def test_jbuilder_detected(self, rails_project: Path) -> None:
        jbuilder = (
            rails_project / "app" / "views" / "users" / "index.json.jbuilder"
        )
        jbuilder.write_text("json.array! @users, :id, :name\n")
        result = detect_serializer("users", "index", rails_project)
        assert result is not None
        assert "index.json.jbuilder" in result

    def test_active_model_serializer_detected(self, rails_project: Path) -> None:
        ser = rails_project / "app" / "serializers" / "user_serializer.rb"
        ser.write_text(
            "class UserSerializer < ActiveModel::Serializer\n"
            "  attributes :id, :name\n"
            "end\n"
        )
        result = detect_serializer("users", "index", rails_project)
        assert result == "UserSerializer"

    def test_blueprinter_detected(self, rails_project: Path) -> None:
        bp = rails_project / "app" / "blueprints" / "user_blueprint.rb"
        bp.write_text(
            "class UserBlueprint < Blueprinter::Base\n"
            "  identifier :id\n"
            "end\n"
        )
        result = detect_serializer("users", "index", rails_project)
        assert result == "UserBlueprint"

    def test_jsonapi_serializer_detected(self, rails_project: Path) -> None:
        ser = rails_project / "app" / "serializers" / "user_serializer.rb"
        ser.write_text(
            "class UserSerializer\n"
            "  include JSONAPI::Serializer\n"
            "  attributes :id, :name\n"
            "end\n"
        )
        result = detect_serializer("users", "index", rails_project)
        assert result == "UserSerializer"

    def test_no_serializer(self, rails_project: Path) -> None:
        result = detect_serializer("products", "index", rails_project)
        assert result is None


# ============================================================
# _group_screens のテスト
# ============================================================


class TestGroupScreens:
    def _make_screen(self, ctrl_action: str, is_api: bool = False) -> ScreenEntry:
        return ScreenEntry(
            screen_name="テスト",
            url_pattern="/test",
            http_method="GET",
            controller_action=ctrl_action,
            is_api=is_api,
        )

    def test_flat_grouping(self) -> None:
        screens = [
            self._make_screen("UsersController#index"),
            self._make_screen("OrdersController#index"),
        ]
        groups = _group_screens(screens, "flat", "ja")
        assert len(groups) == 1
        assert groups[0].group_name == "全画面"
        assert len(groups[0].screens) == 2

    def test_namespace_grouping_no_namespace(self) -> None:
        screens = [
            self._make_screen("UsersController#index"),
            self._make_screen("UsersController#show"),
            self._make_screen("OrdersController#index"),
        ]
        groups = _group_screens(screens, "namespace", "ja")
        # users and orders in separate groups
        group_names = {g.group_name for g in groups}
        assert len(groups) == 2
        assert "orders" in str(group_names).lower() or "注文" in str(group_names)

    def test_namespace_grouping_admin(self) -> None:
        screens = [
            self._make_screen("Admin::UsersController#index"),
        ]
        groups = _group_screens(screens, "namespace", "ja")
        assert groups[0].group_name == "管理画面"

    def test_resource_grouping(self) -> None:
        screens = [
            self._make_screen("UsersController#index"),
            self._make_screen("Admin::UsersController#show"),
        ]
        groups = _group_screens(screens, "resource", "ja")
        # Both "users" should be in same group
        assert len(groups) == 1

    def test_flat_grouping_en(self) -> None:
        screens = [self._make_screen("UsersController#index")]
        groups = _group_screens(screens, "flat", "en")
        assert groups[0].group_name == "All"


# ============================================================
# InventoryFormatter のテスト
# ============================================================


class TestInventoryFormatter:
    def _make_output(
        self, include_api: bool = False, markdown: str | None = None
    ) -> FullInventoryOutput:
        web_screen = ScreenEntry(
            screen_name="ユーザー一覧",
            url_pattern="/users",
            http_method="GET",
            controller_action="UsersController#index",
            template="app/views/users/index.html.erb",
            partial_count=2,
            models=["User", "Company"],
            is_api=False,
        )
        api_screen = ScreenEntry(
            screen_name="ユーザー一覧 API",
            url_pattern="/api/v1/users",
            http_method="GET",
            controller_action="Api::V1::UsersController#index",
            template=None,
            partial_count=0,
            models=["User"],
            is_api=True,
            serializer="UserSerializer",
        )
        screens = [web_screen] + ([api_screen] if include_api else [])
        groups = [ScreenGroup(group_name="ユーザー管理", screens=screens)]
        shared = [
            SharedPartialEntry(
                file="shared/_nav.html.erb",
                screen_count=5,
                impact_level="high",
            )
        ]
        return FullInventoryOutput(
            generated_at="2026-04-01T00:00:00+00:00",
            total_screen_count=len(screens),
            web_screen_count=1,
            api_endpoint_count=1 if include_api else 0,
            groups=groups,
            shared_partials=shared,
            markdown=markdown,
        )

    def test_format_basic(self) -> None:
        formatter = InventoryFormatter()
        output = self._make_output()
        md = formatter.format(output)
        assert "# 画面台帳（自動生成）" in md
        assert "rails-lens" in md
        assert "2026-04-01T00:00:00+00:00" in md
        assert "ユーザー一覧" in md
        assert "Web画面" in md
        assert "shared/_nav.html.erb" in md

    def test_format_includes_api(self) -> None:
        formatter = InventoryFormatter()
        output = self._make_output(include_api=True)
        md = formatter.format(output)
        assert "APIエンドポイント" in md
        assert "UserSerializer" in md

    def test_format_shared_partials(self) -> None:
        formatter = InventoryFormatter()
        output = self._make_output()
        md = formatter.format(output)
        assert "共有パーシャル使用状況" in md
        assert "shared/_nav.html.erb" in md
        assert "high" in md

    def test_no_api_section_when_no_api(self) -> None:
        formatter = InventoryFormatter()
        output = self._make_output(include_api=False)
        md = formatter.format(output)
        assert "APIエンドポイント" not in md


# ============================================================
# _fallback_full_inventory のテスト
# ============================================================


class TestFallbackFullInventory:
    def test_basic_scan(self, config: RailsLensConfig, rails_project: Path) -> None:
        # テンプレートを作成
        (rails_project / "app" / "views" / "users" / "index.html.erb").write_text(
            "<h1>Users</h1>\n"
        )
        (rails_project / "app" / "views" / "users" / "show.html.erb").write_text(
            "<h1>User</h1>\n"
        )
        output = _fallback_full_inventory(config, "ja", "namespace", True)
        assert isinstance(output, FullInventoryOutput)
        assert output._metadata is not None
        assert output._metadata["source"] == "file_analysis"
        assert output.total_screen_count >= 2

    def test_partials_excluded(
        self, config: RailsLensConfig, rails_project: Path
    ) -> None:
        # パーシャルはカウントされない
        (rails_project / "app" / "views" / "users" / "_user_card.html.erb").write_text(
            "<div><%= @user.name %></div>\n"
        )
        (rails_project / "app" / "views" / "users" / "index.html.erb").write_text(
            "<h1>Users</h1>\n"
        )
        output = _fallback_full_inventory(config, "ja", "namespace", True)
        for g in output.groups:
            for s in g.screens:
                assert not s.controller_action.endswith("#_user_card")

    def test_layouts_excluded(
        self, config: RailsLensConfig, rails_project: Path
    ) -> None:
        (
            rails_project / "app" / "views" / "layouts" / "application.html.erb"
        ).write_text("<html><%= yield %></html>\n")
        output = _fallback_full_inventory(config, "ja", "namespace", True)
        all_actions = [
            s.controller_action for g in output.groups for s in g.screens
        ]
        assert not any("layout" in a.lower() for a in all_actions)

    def test_empty_project(
        self, config: RailsLensConfig, rails_project: Path
    ) -> None:
        output = _fallback_full_inventory(config, "ja", "flat", True)
        assert output.total_screen_count == 0
        assert output.groups == [] or all(len(g.screens) == 0 for g in output.groups)


# ============================================================
# _full_inventory_impl の統合テスト（bridge をモック）
# ============================================================


class TestFullInventoryImpl:
    def _make_bridge(self, mappings: list[dict]) -> MagicMock:
        bridge = MagicMock()
        bridge.execute = AsyncMock(
            return_value={"mode": "all", "total": len(mappings), "mappings": mappings}
        )
        return bridge

    def _base_mappings(self) -> list[dict]:
        return [
            {
                "verb": "GET",
                "path": "/users",
                "controller": "users",
                "action": "index",
                "route_name": "users",
                "layout": "application",
                "conventional_template": "users/index",
                "explicit_render": None,
                "i18n_title_keys": {},
                "format_constraint": None,
            },
            {
                "verb": "GET",
                "path": "/users/:id",
                "controller": "users",
                "action": "show",
                "route_name": "user",
                "layout": "application",
                "conventional_template": "users/show",
                "explicit_render": None,
                "i18n_title_keys": {"users.show.title": "ユーザー詳細"},
                "format_constraint": None,
            },
        ]

    @pytest.mark.asyncio
    async def test_basic_inventory(
        self, config: RailsLensConfig, rails_project: Path
    ) -> None:
        (rails_project / "app" / "views" / "users" / "index.html.erb").write_text(
            "<h1>ユーザー一覧</h1>\n<p><%= @user.name %></p>\n"
        )
        (rails_project / "app" / "views" / "users" / "show.html.erb").write_text(
            "<h1>ユーザー詳細</h1>\n"
        )
        bridge = self._make_bridge(self._base_mappings())
        params = ScreenMapInput(mode=ScreenMapMode.FULL_INVENTORY)
        output = await _full_inventory_impl(params, bridge, config)

        assert isinstance(output, FullInventoryOutput)
        assert output.total_screen_count == 2
        assert output.web_screen_count == 2
        assert output.api_endpoint_count == 0
        assert output.generated_at != ""

    @pytest.mark.asyncio
    async def test_markdown_format(
        self, config: RailsLensConfig, rails_project: Path
    ) -> None:
        (rails_project / "app" / "views" / "users" / "index.html.erb").write_text(
            "<h1>ユーザー一覧</h1>\n"
        )
        bridge = self._make_bridge(self._base_mappings())
        params = ScreenMapInput(mode=ScreenMapMode.FULL_INVENTORY, format="markdown")
        output = await _full_inventory_impl(params, bridge, config)

        assert output.markdown is not None
        assert "# 画面台帳" in output.markdown
        assert "rails-lens" in output.markdown

    @pytest.mark.asyncio
    async def test_grouping_namespace(
        self, config: RailsLensConfig, rails_project: Path
    ) -> None:
        (rails_project / "app" / "views" / "users" / "index.html.erb").write_text(
            "<h1>Users</h1>\n"
        )
        bridge = self._make_bridge(self._base_mappings())
        params = ScreenMapInput(
            mode=ScreenMapMode.FULL_INVENTORY,
            group_by=ScreenMapGroupBy.NAMESPACE,
        )
        output = await _full_inventory_impl(params, bridge, config)
        # groupsが存在すること
        assert len(output.groups) > 0

    @pytest.mark.asyncio
    async def test_grouping_flat(
        self, config: RailsLensConfig, rails_project: Path
    ) -> None:
        (rails_project / "app" / "views" / "users" / "index.html.erb").write_text(
            "<h1>Users</h1>\n"
        )
        bridge = self._make_bridge(self._base_mappings())
        params = ScreenMapInput(
            mode=ScreenMapMode.FULL_INVENTORY,
            group_by=ScreenMapGroupBy.FLAT,
        )
        output = await _full_inventory_impl(params, bridge, config)
        assert len(output.groups) == 1
        assert output.groups[0].group_name in ("全画面", "All")

    @pytest.mark.asyncio
    async def test_bridge_failure_fallback(
        self, config: RailsLensConfig, rails_project: Path
    ) -> None:
        (rails_project / "app" / "views" / "users" / "index.html.erb").write_text(
            "<h1>Users</h1>\n"
        )
        failing_bridge = MagicMock()
        failing_bridge.execute = AsyncMock(
            side_effect=RailsRunnerExecutionError("Rails not available")
        )
        params = ScreenMapInput(mode=ScreenMapMode.FULL_INVENTORY)
        output = await _full_inventory_impl(params, failing_bridge, config)

        assert isinstance(output, FullInventoryOutput)
        assert output._metadata is not None
        assert output._metadata["source"] == "file_analysis"

    @pytest.mark.asyncio
    async def test_progress_reporting(
        self, config: RailsLensConfig, rails_project: Path
    ) -> None:
        """ctx.report_progress が呼ばれることを確認"""
        (rails_project / "app" / "views" / "users" / "index.html.erb").write_text(
            "<h1>Users</h1>\n"
        )
        bridge = self._make_bridge(self._base_mappings())
        ctx = MagicMock()
        ctx.report_progress = AsyncMock()

        params = ScreenMapInput(mode=ScreenMapMode.FULL_INVENTORY)
        await _full_inventory_impl(params, bridge, config, ctx)

        assert ctx.report_progress.call_count >= 1

    @pytest.mark.asyncio
    async def test_include_api_false(
        self, config: RailsLensConfig, rails_project: Path
    ) -> None:
        """include_api=False のときAPIエンドポイントが除外される"""
        mappings = [
            {
                "verb": "GET",
                "path": "/users",
                "controller": "users",
                "action": "index",
                "route_name": "users",
                "layout": None,
                "conventional_template": "users/index",
                "explicit_render": None,
                "i18n_title_keys": {},
                "format_constraint": None,
            },
            {
                "verb": "GET",
                "path": "/api/v1/users",
                "controller": "api/v1/users",
                "action": "index",
                "route_name": "api_v1_users",
                "layout": None,
                "conventional_template": "api/v1/users/index",
                "explicit_render": None,
                "i18n_title_keys": {},
                "format_constraint": "json",
            },
        ]
        (rails_project / "app" / "views" / "users" / "index.html.erb").write_text(
            "<h1>Users</h1>\n"
        )
        bridge = self._make_bridge(mappings)
        params = ScreenMapInput(
            mode=ScreenMapMode.FULL_INVENTORY, include_api=False
        )
        output = await _full_inventory_impl(params, bridge, config)
        all_screens = [s for g in output.groups for s in g.screens]
        assert all(not s.is_api for s in all_screens)

    @pytest.mark.asyncio
    async def test_shared_partials_collected(
        self, config: RailsLensConfig, rails_project: Path
    ) -> None:
        """共有パーシャルが複数画面で使われると SharedPartialEntry に含まれる"""
        (rails_project / "app" / "views" / "shared").mkdir(parents=True, exist_ok=True)
        nav = rails_project / "app" / "views" / "shared" / "_nav.html.erb"
        nav.write_text("<nav>nav</nav>\n")

        index_tpl = rails_project / "app" / "views" / "users" / "index.html.erb"
        index_tpl.write_text('<%= render "shared/nav" %>\n<h1>Users</h1>\n')

        show_tpl = rails_project / "app" / "views" / "users" / "show.html.erb"
        show_tpl.write_text('<%= render "shared/nav" %>\n<h1>User</h1>\n')

        bridge = self._make_bridge(self._base_mappings())
        params = ScreenMapInput(mode=ScreenMapMode.FULL_INVENTORY)
        output = await _full_inventory_impl(params, bridge, config)

        partial_files = [p.file for p in output.shared_partials]
        # 共有パーシャルが検出されている
        assert any("nav" in f for f in partial_files)
