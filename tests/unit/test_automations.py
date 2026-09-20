from __future__ import annotations

import pytest
from pydantic import ValidationError

from vepathos_mcp.clients.core_models import CoreAutomationCreated
from vepathos_mcp.errors.codes import DomainError
from vepathos_mcp.tools.automations import CreateAutomationInput, created_result, to_core_body


def args(**extra):
    return CreateAutomationInput.model_validate(
        dict(
            name="Morning",
            template_plan_id="template",
            use_plan_stops=True,
            operation_id="attempt-123",
            **extra,
        )
    )


def test_fill_schedule_and_vehicle_survive_serialization():
    body = to_core_body(args(look="fill", fill_by="packages", min_packages=50, vehicle_type_id="van"))
    assert body["vehicleTypeId"] == "van"
    assert body["notifyJson"] == {"look": "fill", "fillBy": "packages", "minPackages": 50, "fillPercent": 80}
    assert body["windowDays"] == list(range(7))
    assert (body["windowFromMin"], body["windowToMin"], body["everyMinutes"]) == (0, 1439, 15)
    assert body["enabled"] is False


def test_clock_and_template_default_remain_compatible():
    body = to_core_body(args(looks_at="08:30"))
    assert body["windowFromMin"] == body["windowToMin"] == 510
    assert body["vehicleTypeId"] is None


@pytest.mark.parametrize(
    "extra", [{"fill_by": "invented"}, {"min_packages": 0}, {"fill_percent": 101}, {"vehicle_type_id": ""}]
)
def test_invalid_trigger_and_vehicle_are_rejected(extra):
    with pytest.raises(ValidationError):
        args(**extra)


def test_choices_and_vehicle_survive_public_result():
    payload = CoreAutomationCreated.model_validate(
        {
            "automation": {"automation_id": "a", "vehicle_type_id": None},
            "missing": ["fleet"],
            "vehicle_options": [{"id": "van", "name": "Van"}, {"id": "bike", "name": "Bike"}],
            "enabled": False,
        }
    )
    result = created_result(payload)
    assert [v.id for v in result.vehicle_options] == ["van", "bike"]
    assert result.missing == ["fleet"]


def test_replay_reports_user_activation_but_new_creation_cannot_enable():
    raw = {"automation": {"automation_id": "a", "enabled": True}, "enabled": True}
    with pytest.raises(DomainError):
        created_result(CoreAutomationCreated.model_validate(raw))
    result = created_result(CoreAutomationCreated.model_validate({**raw, "replayed": True}))
    assert result.enabled and result.replayed
