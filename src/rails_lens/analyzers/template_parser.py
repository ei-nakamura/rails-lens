"""ERB / Haml / Slim テンプレートの静的解析パーサ"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from rails_lens.config import RailsLensConfig

# ---------------------------------------------------------------------------
# Render detection patterns per engine
# ---------------------------------------------------------------------------

_ERB_RENDER = re.compile(
    r"""<%=?\s*render\s+(?:partial:\s*['"]([^'"]+)['"]|['"]([^'"]+)['"]|(@\w+))""",
    re.MULTILINE,
)
_HAML_RENDER = re.compile(
    r"""^[ \t]*=\s+render\s+(?:partial:\s*['"]([^'"]+)['"]|['"]([^'"]+)['"]|(@\w+))""",
    re.MULTILINE,
)
_SLIM_RENDER = re.compile(
    r"""^[ \t]*==?\s+render\s+(?:partial:\s*['"]([^'"]+)['"]|['"]([^'"]+)['"]|(@\w+))""",
    re.MULTILINE,
)

# Helper method calls
_ERB_HELPER = re.compile(r"""<%=\s*([a-z_][a-z0-9_]*)\s*\(""", re.MULTILINE)
_HAML_HELPER = re.compile(r"""^[ \t]*=\s+([a-z_][a-z0-9_]*)\s*[\(\s]""", re.MULTILINE)
_SLIM_HELPER = re.compile(r"""^[ \t]*==?\s+([a-z_][a-z0-9_]*)\s*[\(\s]""", re.MULTILINE)

# Model attribute / method references  @model.attr or model.attr
_MODEL_ATTR = re.compile(r"""@([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_?!]*)""")

# Screen title patterns
_TITLE_TAG = re.compile(r"""<title>([^<]+)</title>""")
_H1_TAG = re.compile(r"""<h1[^>]*>([^<]+)</h1>""")
_CONTENT_FOR_TITLE = re.compile(
    r"""content_for\s+:title[,\s]+['"]([^'"]+)['"]""", re.MULTILINE
)
_PROVIDE_TITLE = re.compile(r"""provide\s+:title\s*,\s*['"]([^'"]+)['"]""", re.MULTILINE)

# Hardcoded text: Japanese (CJK) or English plain text outside tags
_HARDCODED_JA = re.compile(r"""[\u3040-\u30FF\u4E00-\u9FFF\uFF00-\uFFEF]{2,}""")
_HARDCODED_EN = re.compile(r"""(?<![<"'=])(?<!\w)[A-Z][a-zA-Z ]{4,}(?![>'"=])""")

# Stimulus controller — captures all space-separated controller names in one attribute
_STIMULUS = re.compile(r"""data-controller=['"]([^'"]+)['"]""")

# Rails render keywords to skip (not helper methods)
_RAILS_KEYWORDS = frozenset({
    "render", "redirect_to", "respond_to", "format", "flash", "yield",
    "content_for", "provide", "link_to", "form_for", "form_with",
    "image_tag", "javascript_include_tag", "stylesheet_link_tag",
    "tag", "concat", "capture", "haml_tag",
})


@dataclass
class RenderRef:
    partial: str  # "shared/navigation" or "@users"
    line: int
    collection: bool = False


@dataclass
class HelperRef:
    method: str
    line: int


@dataclass
class ModelRef:
    variable: str   # "@user"
    attribute: str  # "name"
    line: int


@dataclass
class TitleInfo:
    text: str
    source: str  # "title_tag" | "h1_tag" | "content_for_title" | "provide_title"
    line: int


@dataclass
class HardcodedText:
    text: str
    line: int
    lang: str  # "ja" | "en"


@dataclass
class TemplateAnalysis:
    """テンプレート解析結果"""
    file: str
    engine: str  # "erb" | "haml" | "slim" | "unknown"
    renders: list[RenderRef] = field(default_factory=list)
    helpers: list[HelperRef] = field(default_factory=list)
    model_refs: list[ModelRef] = field(default_factory=list)
    titles: list[TitleInfo] = field(default_factory=list)
    hardcoded_text: list[HardcodedText] = field(default_factory=list)
    stimulus_controllers: list[str] = field(default_factory=list)
    decorator_files: list[str] = field(default_factory=list)
    presenter_files: list[str] = field(default_factory=list)


class TemplateParser:
    """ERB / Haml / Slim テンプレートを静的解析するパーサ"""

    def __init__(self, config: RailsLensConfig) -> None:
        self.config = config
        self.project_root = Path(config.rails_project_path)

    def parse(self, template_path: str | Path) -> TemplateAnalysis:
        """テンプレートファイルを解析して TemplateAnalysis を返す"""
        abs_path = self._resolve(template_path)
        try:
            rel_path = (
                str(abs_path.relative_to(self.project_root))
                if abs_path.is_absolute()
                else str(template_path)
            )
        except ValueError:
            rel_path = str(template_path)

        if not abs_path.exists():
            return TemplateAnalysis(file=rel_path, engine="unknown")

        try:
            source = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return TemplateAnalysis(file=rel_path, engine="unknown")

        engine = self._detect_engine(abs_path)
        lines = source.splitlines()

        analysis = TemplateAnalysis(file=rel_path, engine=engine)
        analysis.renders = self._extract_renders(source, lines, engine)
        analysis.helpers = self._extract_helpers(source, lines, engine)
        analysis.model_refs = self._extract_model_refs(source, lines)
        analysis.titles = self._extract_titles(source, lines)
        analysis.hardcoded_text = self._extract_hardcoded(lines)
        analysis.stimulus_controllers = self._extract_stimulus(source)
        analysis.decorator_files, analysis.presenter_files = self._scan_decorators_presenters()

        return analysis

    def parse_source(
        self, source: str, file: str = "<string>", engine: str = "erb"
    ) -> TemplateAnalysis:
        """ソース文字列から直接解析する（テスト用）"""
        lines = source.splitlines()
        analysis = TemplateAnalysis(file=file, engine=engine)
        analysis.renders = self._extract_renders(source, lines, engine)
        analysis.helpers = self._extract_helpers(source, lines, engine)
        analysis.model_refs = self._extract_model_refs(source, lines)
        analysis.titles = self._extract_titles(source, lines)
        analysis.hardcoded_text = self._extract_hardcoded(lines)
        analysis.stimulus_controllers = self._extract_stimulus(source)
        return analysis

    # ------------------------------------------------------------------
    # Engine detection
    # ------------------------------------------------------------------

    def _detect_engine(self, path: Path) -> str:
        name = path.name
        if ".html.erb" in name or name.endswith(".erb"):
            return "erb"
        if ".html.haml" in name or name.endswith(".haml"):
            return "haml"
        if ".html.slim" in name or name.endswith(".slim"):
            return "slim"
        return "unknown"

    # ------------------------------------------------------------------
    # Render extraction
    # ------------------------------------------------------------------

    def _extract_renders(self, source: str, lines: list[str], engine: str) -> list[RenderRef]:
        pattern = {"erb": _ERB_RENDER, "haml": _HAML_RENDER, "slim": _SLIM_RENDER}.get(
            engine, _ERB_RENDER
        )
        results: list[RenderRef] = []
        for m in pattern.finditer(source):
            partial = m.group(1) or m.group(2) or m.group(3) or ""
            if not partial:
                continue
            line_no = source[: m.start()].count("\n") + 1
            is_collection = "collection:" in (lines[line_no - 1] if line_no <= len(lines) else "")
            results.append(RenderRef(partial=partial, line=line_no, collection=is_collection))
        return results

    # ------------------------------------------------------------------
    # Helper extraction
    # ------------------------------------------------------------------

    def _extract_helpers(self, source: str, lines: list[str], engine: str) -> list[HelperRef]:
        pattern = {"erb": _ERB_HELPER, "haml": _HAML_HELPER, "slim": _SLIM_HELPER}.get(
            engine, _ERB_HELPER
        )
        results: list[HelperRef] = []
        seen: set[tuple[str, int]] = set()
        for m in pattern.finditer(source):
            method = m.group(1)
            if method in _RAILS_KEYWORDS:
                continue
            line_no = source[: m.start()].count("\n") + 1
            key = (method, line_no)
            if key not in seen:
                seen.add(key)
                results.append(HelperRef(method=method, line=line_no))
        return results

    # ------------------------------------------------------------------
    # Model reference extraction
    # ------------------------------------------------------------------

    def _extract_model_refs(self, source: str, lines: list[str]) -> list[ModelRef]:
        results: list[ModelRef] = []
        seen: set[tuple[str, str, int]] = set()
        for m in _MODEL_ATTR.finditer(source):
            var = m.group(1)
            attr = m.group(2)
            line_no = source[: m.start()].count("\n") + 1
            key = (var, attr, line_no)
            if key not in seen:
                seen.add(key)
                results.append(ModelRef(variable=f"@{var}", attribute=attr, line=line_no))
        return results

    # ------------------------------------------------------------------
    # Title extraction
    # ------------------------------------------------------------------

    def _extract_titles(self, source: str, lines: list[str]) -> list[TitleInfo]:
        results: list[TitleInfo] = []
        for pattern, source_label in [
            (_CONTENT_FOR_TITLE, "content_for_title"),
            (_PROVIDE_TITLE, "provide_title"),
            (_TITLE_TAG, "title_tag"),
            (_H1_TAG, "h1_tag"),
        ]:
            m = pattern.search(source)
            if m:
                text = m.group(1).strip()
                line_no = source[: m.start()].count("\n") + 1
                results.append(TitleInfo(text=text, source=source_label, line=line_no))
        return results

    # ------------------------------------------------------------------
    # Hardcoded text extraction
    # ------------------------------------------------------------------

    def _extract_hardcoded(self, lines: list[str]) -> list[HardcodedText]:
        results: list[HardcodedText] = []
        for line_no, line in enumerate(lines, start=1):
            # Skip comment lines and tag-only lines
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("//"):
                continue
            for m in _HARDCODED_JA.finditer(line):
                results.append(HardcodedText(text=m.group(0), line=line_no, lang="ja"))
            for m in _HARDCODED_EN.finditer(line):
                results.append(HardcodedText(text=m.group(0).strip(), line=line_no, lang="en"))
        return results

    # ------------------------------------------------------------------
    # Stimulus controller extraction
    # ------------------------------------------------------------------

    def _extract_stimulus(self, source: str) -> list[str]:
        controllers: list[str] = []
        seen: set[str] = set()
        for m in _STIMULUS.finditer(source):
            for name in m.group(1).split():
                name = name.strip()
                if name and name not in seen:
                    seen.add(name)
                    controllers.append(name)
        return controllers

    # ------------------------------------------------------------------
    # Decorator / Presenter scan
    # ------------------------------------------------------------------

    def _scan_decorators_presenters(self) -> tuple[list[str], list[str]]:
        decorators: list[str] = []
        presenters: list[str] = []
        for path in (self.project_root / "app" / "decorators").glob("**/*.rb"):
            decorators.append(str(path.relative_to(self.project_root)))
        for path in (self.project_root / "app" / "presenters").glob("**/*.rb"):
            presenters.append(str(path.relative_to(self.project_root)))
        return sorted(decorators), sorted(presenters)

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _resolve(self, path: str | Path) -> Path:
        p = Path(path)
        if p.is_absolute():
            return p
        return self.project_root / p
