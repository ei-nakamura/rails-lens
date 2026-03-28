"""rails-lens カスタム例外"""


class RailsLensError(Exception):
    """全てのrails-lensエラーの基底クラス"""

    code: str = "UNKNOWN_ERROR"
    suggestion: str | None = None


class ConfigurationError(RailsLensError):
    """設定エラー"""
    code = "CONFIGURATION_ERROR"


class RailsBridgeError(RailsLensError):
    """ブリッジ基底エラー"""
    code = "BRIDGE_ERROR"


class RailsProjectNotFoundError(RailsBridgeError):
    """Railsプロジェクトが見つからない"""
    code = "PROJECT_NOT_FOUND"


class RailsRunnerTimeoutError(RailsBridgeError):
    """rails runner がタイムアウトした"""
    code = "RUNNER_TIMEOUT"


class RailsRunnerExecutionError(RailsBridgeError):
    """rails runner の実行に失敗した"""
    code = "RUNNER_EXECUTION_ERROR"


class RailsRunnerOutputError(RailsBridgeError):
    """rails runner の出力解析に失敗した"""
    code = "RUNNER_OUTPUT_ERROR"


class ModelNotFoundError(RailsLensError):
    """指定されたモデルが見つからない"""
    code = "MODEL_NOT_FOUND"

    def __init__(self, message: str, similar_models: list[str] | None = None):
        super().__init__(message)
        if similar_models:
            self.suggestion = f"Did you mean: {', '.join(similar_models)}?"


class CacheError(RailsLensError):
    """キャッシュ操作エラー"""
    code = "CACHE_ERROR"
