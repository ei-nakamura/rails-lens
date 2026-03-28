"""Pydantic モデルのシリアライズテスト"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from rails_lens.models import (
    Conditions,
    ErrorResponse,
    IntrospectModelInput,
)


def test_introspect_model_input_validation() -> None:
    """model_name バリデーション"""
    # 正常
    inp = IntrospectModelInput(model_name="User")
    assert inp.model_name == "User"

    # 空文字はバリデーションエラー
    with pytest.raises(ValidationError):
        IntrospectModelInput(model_name="")

    # 空白のみはstrip後に空文字→エラー
    with pytest.raises(ValidationError):
        IntrospectModelInput(model_name="   ")


def test_introspect_model_output_defaults() -> None:
    """デフォルト値確認: sections=None"""
    inp = IntrospectModelInput(model_name="Post")
    assert inp.sections is None


def test_error_response() -> None:
    """ErrorResponse の JSON 出力"""
    err = ErrorResponse(code="MODEL_NOT_FOUND", message="User not found")
    data = json.loads(err.model_dump_json())
    assert data["code"] == "MODEL_NOT_FOUND"
    assert data["message"] == "User not found"
    assert data["suggestion"] is None


def test_conditions_alias() -> None:
    """if/unless フィールドのエイリアス動作"""
    cond = Conditions.model_validate({"if": "some_condition", "unless": None})
    assert cond.if_condition == "some_condition"
    assert cond.unless_condition is None

    # populate_by_name=True なので内部名でもアクセス可能
    cond2 = Conditions(if_condition="check", unless_condition="skip")
    assert cond2.if_condition == "check"
