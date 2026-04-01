"""テンプレート内のrenderパターンからパーシャルを再帰的に解決するアナライザ"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from rails_lens.config import RailsLensConfig

# Template file extensions to check (in order of preference)
_TEMPLATE_EXTS = [".html.erb", ".html.haml", ".html.slim", ".erb", ".haml", ".slim"]

# render patterns to extract partial references
# Matches: render partial: "...", render "...", render @collection
_RENDER_PATTERNS = [
    # render partial: "path/to/partial", optional extra args
    re.compile(r"""render\s+partial:\s+['"]([^'"]+)['"]"""),
    # render "path/to/partial" (abbreviated form)
    re.compile(r"""render\s+['"]([^'"]+)['"]"""),
    # render @variable (collection shorthand) — captures variable name
    re.compile(r"""render\s+@(\w+)"""),
    # render partial: "...", collection: @items
    re.compile(r"""render\s+partial:\s+['"]([^'"]+)['"]\s*,\s*collection:"""),
]

# content_for / provide :title patterns
_TITLE_PATTERNS = [
    re.compile(r"""content_for\s+:title\s*(?:,\s*['"]([^'"]+)['"]|do\s*\n\s*['"]([^'"]+)['"])"""),
    re.compile(r"""provide\s+:title\s*,\s*['"]([^'"]+)['"]"""),
    re.compile(r"""<title>([^<]+)</title>"""),
    re.compile(r"""<h1[^>]*>([^<]+)</h1>"""),
]


@dataclass
class PartialNode:
    """パーシャル解決ノード（ネスト構造）"""
    name: str
    file: str
    called_from: str  # "path/to/template.html.erb:LINE"
    locals_passed: list[str] = field(default_factory=list)
    collection: bool = False
    note: str = ""
    nested_partials: list[PartialNode] = field(default_factory=list)


class ViewResolver:
    """テンプレート内のrenderパターンからパーシャルを再帰的に解決する"""

    def __init__(self, config: RailsLensConfig) -> None:
        self.config = config
        self.project_root = Path(config.rails_project_path)
        self._views_root = self.project_root / "app" / "views"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve_partials(
        self,
        template_path: str | Path,
        visited: set[str] | None = None,
    ) -> list[PartialNode]:
        """テンプレートファイルからパーシャルを再帰的に解決する。

        Args:
            template_path: テンプレートファイルのパス（プロジェクトルート相対 or 絶対パス）
            visited: 循環参照検出用の訪問済みセット（内部再帰で使用）

        Returns:
            PartialNodeのリスト（ネスト済み）
        """
        if visited is None:
            visited = set()

        abs_path = self._resolve_abs(template_path)
        if abs_path is None or not abs_path.exists():
            return []

        path_key = str(abs_path)
        if path_key in visited:
            return []
        visited.add(path_key)

        try:
            source = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []

        rel_template = str(abs_path.relative_to(self.project_root))
        partials: list[PartialNode] = []

        for line_no, line in enumerate(source.splitlines(), start=1):
            nodes = self._extract_render_from_line(line, line_no, rel_template, visited)
            partials.extend(nodes)

        return partials

    def extract_title(self, template_path: str | Path) -> str | None:
        """テンプレートから画面タイトルを抽出する（h1, title, content_for/provide）"""
        abs_path = self._resolve_abs(template_path)
        if abs_path is None or not abs_path.exists():
            return None

        try:
            source = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

        for pattern in _TITLE_PATTERNS:
            m = pattern.search(source)
            if m:
                for group in m.groups():
                    if group:
                        return group.strip()
        return None

    def find_template(self, controller: str, action: str) -> str | None:
        """コントローラ・アクションから規約ベースのテンプレートファイルを探す。

        Args:
            controller: snake_case controller path (e.g. "users", "admin/users")
            action: アクション名 (e.g. "show")

        Returns:
            プロジェクトルート相対パス、または None
        """
        base = self._views_root / controller / action
        for ext in _TEMPLATE_EXTS:
            candidate = base.parent / (base.name + ext)
            if candidate.exists():
                return str(candidate.relative_to(self.project_root))
        return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_abs(self, path: str | Path) -> Path | None:
        p = Path(path)
        if p.is_absolute():
            return p
        # Try relative to project root first
        candidate = self.project_root / p
        if candidate.exists():
            return candidate
        # Try relative to views root
        candidate2 = self._views_root / p
        if candidate2.exists():
            return candidate2
        return self.project_root / p  # return even if not exists (caller checks)

    def _extract_render_from_line(
        self,
        line: str,
        line_no: int,
        rel_template: str,
        visited: set[str],
    ) -> list[PartialNode]:
        """1行からrenderパターンを抽出しPartialNodeを生成する"""
        nodes: list[PartialNode] = []
        called_from = f"{rel_template}:{line_no}"

        # Check collection variant first (to set collection=True)
        is_collection = bool(re.search(r"collection:", line))

        # render @variable shorthand
        for m in _RENDER_PATTERNS[2].finditer(line):
            var_name = m.group(1)
            # @users → app/views/users/_user.html.{erb,haml,slim}
            singular = self._singularize(var_name)
            partial_path = self._resolve_partial_by_convention(
                rel_template, singular, in_plural_dir=var_name
            )
            node = PartialNode(
                name=f"_{singular}",
                file=partial_path or f"app/views/{var_name}/_{singular}.html.erb",
                called_from=called_from,
                collection=True,
                note=f"collection shorthand: @{var_name}",
            )
            # Recurse
            if partial_path:
                node.nested_partials = self.resolve_partials(partial_path, set(visited))
            nodes.append(node)

        # render partial: "..." and render "..."
        for pattern in (_RENDER_PATTERNS[0], _RENDER_PATTERNS[1]):
            for m in pattern.finditer(line):
                partial_name = m.group(1)
                # Skip if already captured as @variable
                if partial_name.startswith("@"):
                    continue
                partial_file = self._resolve_partial_path(partial_name, rel_template)
                # Extract locals from line
                locals_passed = self._extract_locals(line)

                node = PartialNode(
                    name=f"_{partial_name.split('/')[-1]}",
                    file=partial_file or self._conventional_partial_path(partial_name),
                    called_from=called_from,
                    locals_passed=locals_passed,
                    collection=is_collection,
                )
                if partial_file:
                    node.nested_partials = self.resolve_partials(partial_file, set(visited))
                nodes.append(node)

        return nodes

    def _resolve_partial_path(self, partial_name: str, caller_template: str) -> str | None:
        """パーシャル名をファイルパスに解決する。

        Rules:
        - "shared/navigation" → app/views/shared/_navigation.html.{erb,haml,slim}
        - "users/header" → app/views/users/_header.html.{erb,haml,slim}
        - "form" → same dir as caller → app/views/{caller_dir}/_form.html.{erb,haml,slim}
        """
        if "/" in partial_name:
            dir_part, name_part = partial_name.rsplit("/", 1)
            base_dir = self._views_root / dir_part
        else:
            # Same directory as caller
            caller_dir = (self.project_root / caller_template).parent
            base_dir = caller_dir
            name_part = partial_name

        partial_file = f"_{name_part}"
        for ext in _TEMPLATE_EXTS:
            candidate = base_dir / (partial_file + ext)
            if candidate.exists():
                return str(candidate.relative_to(self.project_root))
        return None

    def _conventional_partial_path(self, partial_name: str) -> str:
        """ファイルが存在しない場合の慣習的パーシャルパス文字列を返す"""
        if "/" in partial_name:
            dir_part, name_part = partial_name.rsplit("/", 1)
            return f"app/views/{dir_part}/_{name_part}.html.erb"
        return f"_{partial_name}.html.erb"

    def _resolve_partial_by_convention(
        self, caller_template: str, singular: str, in_plural_dir: str
    ) -> str | None:
        """@collection → app/views/{plural}/_{singular} を探す"""
        base_dir = self._views_root / in_plural_dir
        for ext in _TEMPLATE_EXTS:
            candidate = base_dir / f"_{singular}{ext}"
            if candidate.exists():
                return str(candidate.relative_to(self.project_root))
        return None

    def _extract_locals(self, line: str) -> list[str]:
        """locals: { key: val } からキーを抽出する"""
        m = re.search(r"locals:\s*\{([^}]+)\}", line)
        if not m:
            return []
        pairs = m.group(1)
        return re.findall(r":?(\w+)\s*:", pairs)

    def _singularize(self, plural: str) -> str:
        """単純な英語単数化（完全ではないが一般的なケースを処理）"""
        if plural.endswith("ies"):
            return plural[:-3] + "y"
        if plural.endswith("ses") or plural.endswith("xes") or plural.endswith("zes"):
            return plural[:-2]
        if plural.endswith("s") and not plural.endswith("ss"):
            return plural[:-1]
        return plural
