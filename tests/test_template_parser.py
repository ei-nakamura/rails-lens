"""tests for TemplateParser"""
from __future__ import annotations

from pathlib import Path

import pytest

from rails_lens.analyzers.template_parser import TemplateParser
from rails_lens.config import RailsLensConfig

FIXTURES = Path(__file__).parent / "fixtures" / "views"


@pytest.fixture
def config(tmp_path: Path) -> RailsLensConfig:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    views_link = app_dir / "views"
    views_link.symlink_to(FIXTURES.resolve())
    return RailsLensConfig(rails_project_path=tmp_path)


@pytest.fixture
def parser(config: RailsLensConfig) -> TemplateParser:
    return TemplateParser(config)


# ---------------------------------------------------------------------------
# ERB
# ---------------------------------------------------------------------------

class TestERBParsing:
    ERB_SOURCE = """\
<% content_for :title, "ユーザー詳細" %>
<h1>ユーザー詳細</h1>
<div data-controller="users clipboard">
  <%= render partial: "users/header", locals: { user: @user } %>
  <p>名前: <%= @user.name %></p>
  <%= render "users/profile" %>
  <%= render @posts %>
  <%= user_status_badge(@user) %>
</div>
"""

    def test_engine_detected_as_erb(self, parser: TemplateParser) -> None:
        a = parser.parse_source(self.ERB_SOURCE, file="show.html.erb", engine="erb")
        assert a.engine == "erb"

    def test_renders_extracted(self, parser: TemplateParser) -> None:
        a = parser.parse_source(self.ERB_SOURCE, file="show.html.erb", engine="erb")
        partials = [r.partial for r in a.renders]
        assert "users/header" in partials
        assert "users/profile" in partials

    def test_collection_render_detected(self, parser: TemplateParser) -> None:
        a = parser.parse_source(self.ERB_SOURCE, file="show.html.erb", engine="erb")
        collection_renders = [r for r in a.renders if r.partial == "@posts"]
        assert len(collection_renders) == 1

    def test_helpers_extracted(self, parser: TemplateParser) -> None:
        a = parser.parse_source(self.ERB_SOURCE, file="show.html.erb", engine="erb")
        methods = [h.method for h in a.helpers]
        assert "user_status_badge" in methods

    def test_model_refs_extracted(self, parser: TemplateParser) -> None:
        a = parser.parse_source(self.ERB_SOURCE, file="show.html.erb", engine="erb")
        refs = {(r.variable, r.attribute) for r in a.model_refs}
        assert ("@user", "name") in refs

    def test_title_content_for(self, parser: TemplateParser) -> None:
        a = parser.parse_source(self.ERB_SOURCE, file="show.html.erb", engine="erb")
        titles = [t.text for t in a.titles]
        assert "ユーザー詳細" in titles

    def test_stimulus_extracted(self, parser: TemplateParser) -> None:
        a = parser.parse_source(self.ERB_SOURCE, file="show.html.erb", engine="erb")
        assert "users" in a.stimulus_controllers
        assert "clipboard" in a.stimulus_controllers

    def test_hardcoded_japanese_detected(self, parser: TemplateParser) -> None:
        a = parser.parse_source(self.ERB_SOURCE, file="show.html.erb", engine="erb")
        ja_texts = [h.text for h in a.hardcoded_text if h.lang == "ja"]
        assert any("名前" in t for t in ja_texts)


# ---------------------------------------------------------------------------
# Haml
# ---------------------------------------------------------------------------

class TestHamlParsing:
    HAML_SOURCE = """\
%h1 ユーザー一覧
= render partial: "users/search_form"
%ul
  = render @users
= provide :title, "ユーザー一覧"
"""

    def test_renders_haml(self, parser: TemplateParser) -> None:
        a = parser.parse_source(self.HAML_SOURCE, file="index.html.haml", engine="haml")
        partials = [r.partial for r in a.renders]
        assert "users/search_form" in partials

    def test_collection_haml(self, parser: TemplateParser) -> None:
        a = parser.parse_source(self.HAML_SOURCE, file="index.html.haml", engine="haml")
        collection_renders = [r for r in a.renders if r.partial == "@users"]
        assert len(collection_renders) == 1

    def test_title_provide_haml(self, parser: TemplateParser) -> None:
        a = parser.parse_source(self.HAML_SOURCE, file="index.html.haml", engine="haml")
        titles = [t.text for t in a.titles]
        assert "ユーザー一覧" in titles


# ---------------------------------------------------------------------------
# Slim
# ---------------------------------------------------------------------------

class TestSlimParsing:
    SLIM_SOURCE = """\
h1 ユーザー新規作成
= render partial: "users/form", locals: { user: @user }
= render "shared/error_messages"
"""

    def test_renders_slim(self, parser: TemplateParser) -> None:
        a = parser.parse_source(self.SLIM_SOURCE, file="new.html.slim", engine="slim")
        partials = [r.partial for r in a.renders]
        assert "users/form" in partials
        assert "shared/error_messages" in partials

    def test_locals_line_slim(self, parser: TemplateParser) -> None:
        a = parser.parse_source(self.SLIM_SOURCE, file="new.html.slim", engine="slim")
        assert len(a.renders) >= 2


# ---------------------------------------------------------------------------
# File-based parse (engine detection)
# ---------------------------------------------------------------------------

class TestFileParsing:
    def test_parse_erb_file(self, parser: TemplateParser, config: RailsLensConfig) -> None:
        path = config.rails_project_path / "app" / "views" / "users" / "show.html.erb"
        a = parser.parse(path)
        assert a.engine == "erb"
        assert any(r.partial == "users/header" for r in a.renders)

    def test_parse_haml_file(self, parser: TemplateParser, config: RailsLensConfig) -> None:
        path = config.rails_project_path / "app" / "views" / "users" / "index.html.haml"
        a = parser.parse(path)
        assert a.engine == "haml"
        assert any(r.partial == "users/search_form" for r in a.renders)

    def test_parse_slim_file(self, parser: TemplateParser, config: RailsLensConfig) -> None:
        path = config.rails_project_path / "app" / "views" / "users" / "new.html.slim"
        a = parser.parse(path)
        assert a.engine == "slim"

    def test_missing_file_returns_unknown(self, parser: TemplateParser) -> None:
        a = parser.parse("/nonexistent/path/show.html.erb")
        assert a.engine == "unknown"
        assert a.renders == []
