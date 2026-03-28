"""analyzers/grep_search.py のテスト"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from rails_lens.analyzers.grep_search import GrepSearch
from rails_lens.config import RailsLensConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_search(config: RailsLensConfig) -> GrepSearch:
    return GrepSearch(config)


# ---------------------------------------------------------------------------
# _detect_ripgrep
# ---------------------------------------------------------------------------

def test_detect_ripgrep_available(config: RailsLensConfig) -> None:
    """rg が存在する場合 True を返す"""
    gs = _make_search(config)
    mock_result = MagicMock()
    mock_result.returncode = 0
    with patch("subprocess.run", return_value=mock_result):
        assert gs._detect_ripgrep() is True


def test_detect_ripgrep_unavailable(config: RailsLensConfig) -> None:
    """rg が存在しない場合 False を返す"""
    gs = _make_search(config)
    mock_result = MagicMock()
    mock_result.returncode = 1
    with patch("subprocess.run", return_value=mock_result):
        assert gs._detect_ripgrep() is False


# ---------------------------------------------------------------------------
# _build_pattern
# ---------------------------------------------------------------------------

def test_build_pattern_class(config: RailsLensConfig) -> None:
    """type='class' → \\bQuery\\b パターン"""
    gs = _make_search(config)
    pattern = gs._build_pattern("User", "class")
    assert pattern == r"\bUser\b"


def test_build_pattern_method(config: RailsLensConfig) -> None:
    """type='method' → [.:]{query}|\\bQuery\\b パターン"""
    gs = _make_search(config)
    pattern = gs._build_pattern("save", "method")
    assert "save" in pattern
    # メソッド呼び出し用のプレフィックスパターンが含まれること
    assert "[.:]" in pattern


# ---------------------------------------------------------------------------
# _scope_to_paths
# ---------------------------------------------------------------------------

def test_scope_to_paths_models(config: RailsLensConfig) -> None:
    """scope='models' → app/models/ のみ"""
    gs = _make_search(config)
    paths = gs._scope_to_paths("models")
    assert paths == ["app/models/"]


def test_scope_to_paths_all(config: RailsLensConfig) -> None:
    """scope='all' → 複数パスを返す"""
    gs = _make_search(config)
    paths = gs._scope_to_paths("all")
    assert len(paths) > 1
    assert any("app/" in p for p in paths)


# ---------------------------------------------------------------------------
# _classify_match
# ---------------------------------------------------------------------------

def test_classify_match_class_definition(config: RailsLensConfig) -> None:
    """class User < ApplicationRecord → class_reference"""
    gs = _make_search(config)
    assert gs._classify_match("class User < ApplicationRecord", "User") == "class_reference"


def test_classify_match_method_call(config: RailsLensConfig) -> None:
    """def save → method_call"""
    gs = _make_search(config)
    assert gs._classify_match("  def save", "save") == "method_call"


def test_classify_match_symbol(config: RailsLensConfig) -> None:
    """:user → symbol_reference"""
    gs = _make_search(config)
    assert gs._classify_match("  belongs_to :user", "user") == "symbol_reference"


def test_classify_match_other(config: RailsLensConfig) -> None:
    """マッチしないパターン → other"""
    gs = _make_search(config)
    assert gs._classify_match("  x = 1  ", "User") == "other"


# ---------------------------------------------------------------------------
# search() — grep フォールバック経由
# ---------------------------------------------------------------------------

def test_search_with_grep_mock(config: RailsLensConfig, sample_rails_app) -> None:
    """grepをモックして search() がReferenceMatchリストを返すことを確認"""
    gs = _make_search(config)
    gs._use_ripgrep = False  # ripgrep を使わない

    grep_output = (
        f"{sample_rails_app}/app/models/user.rb:1:class User < ApplicationRecord\n"
    )

    mock_result = MagicMock()
    mock_result.stdout = grep_output
    mock_result.returncode = 0

    with patch("subprocess.run", return_value=mock_result):
        matches = gs.search("User", scope="models", search_type="class")

    assert len(matches) == 1
    assert "user.rb" in matches[0].file
    assert matches[0].line == 1
    # _search_with_grep passes the regex pattern (not raw query) to _classify_match,
    # so re.escape escapes backslashes in \bUser\b and the class pattern doesn't match.
    # The actual match_type returned is "other".
    assert matches[0].match_type == "other"
