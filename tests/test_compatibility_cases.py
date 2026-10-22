"""Every added case participates in native Hawk config and mock fixture validation."""

from pathlib import Path

import pytest
import yaml
from compatibility.run_suite import materialize
from hawk.core.types import EvalSetConfig
from ia_compatibility.judges import mock_judge

CASES = Path(__file__).parent / "compatibility" / "cases"


@pytest.mark.parametrize("path", sorted(CASES.glob("*.eval-set.yaml")), ids=lambda p: p.name)
def test_case_is_a_native_hawk_config(path: Path) -> None:
    config = materialize(yaml.safe_load(path.read_text()), "a" * 40)
    EvalSetConfig.model_validate(config)
    for task in config["tasks"]:
        for item in task["items"]:
            spec = item["args"]["spec"]
            assert spec["task"] and spec["source"] and spec["package"]
            for response in spec.get("mock_judges", {}).values():
                mock_judge(response)
