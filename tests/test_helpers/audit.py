"""Explicit audit policies for tool fixtures."""

from typing import Any

from inspect_audit._audit import Audit
from inspect_audit._verdicts import investigation_items
from inspect_audit.dispatch.policy import AuditAuthorizer, LaunchPolicy, Models


def make_audit(**args: Any) -> Audit:
    menu = args.pop("menu")
    policy = LaunchPolicy(
        task=args["task"],
        task_package=args["task_package"],
        investigation_package=args["investigation_package"],
        investigator_image=args["investigator_image"],
        models=Models(
            investigators=tuple(menu.investigators),
            subjects=tuple(menu.subjects),
            judges=tuple(menu.judges),
        ),
        checks=tuple(skill.name for skill in investigation_items()),
        supplied_logs=tuple(args["supplied"]),
        configuration_json="{}",
    )
    return Audit(**args, authorizer=AuditAuthorizer(policy))
