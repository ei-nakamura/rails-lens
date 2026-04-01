"""tests for ViewResolver"""
from __future__ import annotations

from pathlib import Path

import pytest

from rails_lens.analyzers.view_resolver import ViewResolver
from rails_lens.config import RailsLensConfig

FIXTURES = Path(__file__).parent / "fixtures" / "views"


@pytest.fixture
def config(tmp_path: Path) -> RailsLensConfig:
    """fixturesをプロジェクトルートに見立てたconfig"""
    # Symlink app/views → fixtures/views so ViewResolver can find them
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    views_link = app_dir / "views"
    views_link.symlink_to(FIXTURES.resolve())
    return RailsLensConfig(rails_project_path=tmp_path)


@pytest.fixture
def resolver(config: RailsLensConfig) -> ViewResolver:
    return ViewResolver(config)


class TestFindTemplate:
    def test_finds_erb_template(self, resolver: ViewResolver, config: RailsLensConfig) -> None:
        result = resolver.find_template("users", "show")
        assert result is not None
        assert result.endswith("show.html.erb")

    def test_finds_haml_template(self, resolver: ViewResolver) -> None:
        result = resolver.find_template("users", "index")
        assert result is not None
        assert result.endswith("index.html.haml")

    def test_finds_slim_template(self, resolver: ViewResolver) -> None:
        result = resolver.find_template("users", "new")
        assert result is not None
        assert result.endswith("new.html.slim")

    def test_returns_none_for_missing(self, resolver: ViewResolver) -> None:
        result = resolver.find_template("missing_controller", "show")
        assert result is None


class TestResolvePartials:
    def test_resolves_named_partial(self, resolver: ViewResolver, config: RailsLensConfig) -> None:
        template = config.rails_project_path / "app" / "views" / "users" / "show.html.erb"
        partials = resolver.resolve_partials(template)
        names = [p.name for p in partials]
        assert "_header" in names

    def test_resolves_abbreviated_render(
        self, resolver: ViewResolver, config: RailsLensConfig
    ) -> None:
        template = config.rails_project_path / "app" / "views" / "users" / "show.html.erb"
        partials = resolver.resolve_partials(template)
        names = [p.name for p in partials]
        assert "_profile" in names

    def test_collection_render_detected(
        self, resolver: ViewResolver, config: RailsLensConfig
    ) -> None:
        template = config.rails_project_path / "app" / "views" / "users" / "show.html.erb"
        partials = resolver.resolve_partials(template)
        collection_partials = [p for p in partials if p.collection]
        assert len(collection_partials) >= 1

    def test_no_circular_reference(
        self, resolver: ViewResolver, config: RailsLensConfig
    ) -> None:
        """循環参照で無限ループしないこと"""
        template = config.rails_project_path / "app" / "views" / "users" / "show.html.erb"
        # Should complete without recursion error
        partials = resolver.resolve_partials(template)
        assert isinstance(partials, list)

    def test_nested_partials_resolved(
        self, resolver: ViewResolver, config: RailsLensConfig
    ) -> None:
        """_header が _navigation をネストで持つこと"""
        template = config.rails_project_path / "app" / "views" / "users" / "show.html.erb"
        partials = resolver.resolve_partials(template)
        header_nodes = [p for p in partials if p.name == "_header"]
        assert header_nodes, "header partial not found"
        header = header_nodes[0]
        nested_names = [n.name for n in header.nested_partials]
        assert "_navigation" in nested_names

    def test_missing_template_returns_empty(self, resolver: ViewResolver) -> None:
        result = resolver.resolve_partials("/nonexistent/path/show.html.erb")
        assert result == []

    def test_locals_extracted(self, resolver: ViewResolver, config: RailsLensConfig) -> None:
        template = config.rails_project_path / "app" / "views" / "users" / "show.html.erb"
        partials = resolver.resolve_partials(template)
        header_nodes = [p for p in partials if p.name == "_header"]
        assert header_nodes
        assert "user" in header_nodes[0].locals_passed


class TestExtractTitle:
    def test_content_for_title(self, resolver: ViewResolver, config: RailsLensConfig) -> None:
        template = config.rails_project_path / "app" / "views" / "users" / "show.html.erb"
        title = resolver.extract_title(template)
        assert title == "ユーザー詳細"

    def test_missing_returns_none(self, resolver: ViewResolver) -> None:
        assert resolver.extract_title("/nonexistent.html.erb") is None
