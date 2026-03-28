"""テストファイルマッパー（RSpec/minitest両対応）"""
from __future__ import annotations

import re
from pathlib import Path

from rails_lens.analyzers.grep_search import GrepSearch
from rails_lens.config import RailsLensConfig
from rails_lens.models import TestFile, TestMappingOutput


class TestMapper:
    """Railsプロジェクトのテストファイルをモデルやメソッドにマッピングする"""

    def __init__(self, config: RailsLensConfig) -> None:
        self.config = config
        self.project_root = Path(config.rails_project_path)
        self.grep = GrepSearch(config)

    def _detect_framework(self) -> str:
        if (self.project_root / "spec").exists():
            return "rspec"
        elif (self.project_root / "test").exists():
            return "minitest"
        return "unknown"

    def _parse_target(self, target: str) -> tuple[str, str | None]:
        """'User' or 'User#activate' → (model_name, method_name)"""
        if "#" in target:
            parts = target.split("#", 1)
            return parts[0].strip(), parts[1].strip()
        return target.strip(), None

    def map(self, target: str, include_indirect: bool = True) -> TestMappingOutput:
        """テストファイルをマッピングして TestMappingOutput を返す"""
        model_name, method_name = self._parse_target(target)
        framework = self._detect_framework()
        snake_name = _to_snake_case(model_name)

        direct_tests: list[TestFile] = []
        indirect_tests: list[TestFile] = []
        factories: list[TestFile] = []

        if framework == "rspec":
            cmd_base = "bundle exec rspec"
            # 規約ベースの直接テスト
            direct_candidates = [
                (f"spec/models/{snake_name}_spec.rb", "unit"),
                (f"spec/requests/{snake_name}s_spec.rb", "request"),
                (f"spec/controllers/{snake_name}s_controller_spec.rb", "request"),
            ]
            for candidate, test_type in direct_candidates:
                full_path = self.project_root / candidate
                if full_path.exists():
                    examples = self._extract_examples(full_path, method_name)
                    direct_tests.append(TestFile(
                        file=candidate,
                        type=test_type,
                        relevance="direct",
                        matched_examples=examples,
                    ))

            # ファクトリ
            factory_candidates = [
                f"spec/factories/{snake_name}s.rb",
                f"spec/factories/{snake_name}.rb",
            ]
            for candidate in factory_candidates:
                full_path = self.project_root / candidate
                if full_path.exists():
                    examples = self._extract_factory_traits(full_path)
                    factories.append(TestFile(
                        file=candidate,
                        type="factory",
                        relevance="direct",
                        matched_examples=examples,
                    ))

            # 間接テスト（shared_examples、feature specs）
            if include_indirect:
                indirect_tests.extend(
                    self._find_indirect_rspec(model_name, method_name, snake_name)
                )

        else:  # minitest or unknown
            cmd_base = "bundle exec rails test"
            direct_candidates = [
                (f"test/models/{snake_name}_test.rb", "unit"),
                (f"test/controllers/{snake_name}s_controller_test.rb", "request"),
            ]
            for candidate, test_type in direct_candidates:
                full_path = self.project_root / candidate
                if full_path.exists():
                    examples = self._extract_examples(full_path, method_name)
                    direct_tests.append(TestFile(
                        file=candidate,
                        type=test_type,
                        relevance="direct",
                        matched_examples=examples,
                    ))

            # 間接テスト
            if include_indirect:
                indirect_tests.extend(
                    self._find_indirect_minitest(model_name, method_name, snake_name)
                )

        # run_command を生成
        all_files = [t.file for t in direct_tests] + [t.file for t in indirect_tests]
        if all_files:
            run_command = f"{cmd_base} {' '.join(all_files)}"
        elif framework == "rspec":
            run_command = f"bundle exec rspec spec/models/{snake_name}_spec.rb"
        else:
            run_command = f"bundle exec rails test test/models/{snake_name}_test.rb"

        return TestMappingOutput(
            target=target,
            test_framework=framework,
            direct_tests=direct_tests,
            indirect_tests=indirect_tests,
            factories=factories,
            run_command=run_command,
        )

    def _extract_examples(self, path: Path, method_name: str | None) -> list[str]:
        """RSpec/minitest ファイルからテストケース名を抽出する"""
        examples: list[str] = []
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return examples

        # RSpec: describe, context, it パターン
        for m in re.finditer(r"""(?:describe|context|it)\s+['"]([^'"]+)['"]""", content):
            examples.append(m.group(1))

        # minitest: test_ メソッド
        for m in re.finditer(r"def\s+(test_\w+)", content):
            examples.append(m.group(1))

        if method_name:
            # メソッド名に関連するものだけに絞り込む
            lower_method = method_name.lower()
            examples = [e for e in examples if lower_method in e.lower() or f"#{method_name}" in e]

        return examples[:20]  # 上限

    def _extract_factory_traits(self, path: Path) -> list[str]:
        """FactoryBot ファクトリのファクトリ名とトレイトを抽出する"""
        items: list[str] = []
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return items

        for m in re.finditer(r"factory\s+:(\w+)", content):
            items.append(f"factory :{m.group(1)}")
        for m in re.finditer(r"trait\s+:(\w+)", content):
            items.append(f"trait :{m.group(1)}")

        return items

    def _find_indirect_rspec(
        self, model_name: str, method_name: str | None, snake_name: str
    ) -> list[TestFile]:
        """shared_examples・feature specs 等の間接テストを検索する"""
        indirect: list[TestFile] = []

        search_query = method_name if method_name else model_name
        matches = self.grep.search(search_query, scope="all", search_type="any")

        seen_files: set[str] = set()
        for m in matches:
            f = m.file
            if f in seen_files:
                continue
            # spec/ 配下かつ直接テスト以外
            if "spec/" not in f:
                continue
            if f"spec/models/{snake_name}_spec.rb" in f:
                continue
            if f"spec/requests/{snake_name}s_spec.rb" in f:
                continue
            if "spec/factories/" in f:
                continue

            seen_files.add(f)
            test_type = "shared_example" if "shared_example" in f else (
                "feature" if "feature" in f or "system" in f else "request"
            )
            indirect.append(TestFile(
                file=f,
                type=test_type,
                relevance="indirect",
                matched_examples=[m.context.match.strip() if m.context else ""],
            ))

        return indirect

    def _find_indirect_minitest(
        self, model_name: str, method_name: str | None, snake_name: str
    ) -> list[TestFile]:
        """minitest の間接テスト（integration/system tests 等）を検索する"""
        indirect: list[TestFile] = []

        search_query = method_name if method_name else model_name
        matches = self.grep.search(search_query, scope="all", search_type="any")

        seen_files: set[str] = set()
        for m in matches:
            f = m.file
            if f in seen_files:
                continue
            if "test/" not in f:
                continue
            if f"test/models/{snake_name}_test.rb" in f:
                continue

            seen_files.add(f)
            test_type = "feature" if "integration" in f or "system" in f else "request"
            indirect.append(TestFile(
                file=f,
                type=test_type,
                relevance="indirect",
                matched_examples=[m.context.match.strip() if m.context else ""],
            ))

        return indirect


def _to_snake_case(name: str) -> str:
    s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', name)
    return re.sub(r'([a-z])([A-Z])', r'\1_\2', s).lower()
