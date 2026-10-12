"""A store's own deployment configuration: audit.toml and the per-eval pilot.yaml."""

import pytest

from inspect_audit.findings.deployment import Deployment, load_deployment, store_pilot
from inspect_audit.findings.fs import StoreFS


def _fs(name: str) -> StoreFS:
    return StoreFS.from_locator(f"memory://deployment-{name}")


def test_an_absent_audit_toml_is_every_default() -> None:
    assert load_deployment(_fs("absent")) == Deployment()
    assert Deployment().evals.profile == "inspect_evals" and Deployment().evals.root is None


def test_evals_root_is_read() -> None:
    fs = _fs("root")
    fs.write_text("audit.toml", '[evals]\nprofile = "inspect_evals"\nroot = "../inspect_evals"\n')
    assert load_deployment(fs).evals.root == "../inspect_evals"


@pytest.mark.parametrize(
    "text",
    [
        '[evals]\nprofile = "other"\n',
        "[evals]\nrooot = 'typo'\n",
        "[tracker]\nkind = 'github'\n",
        "[evals\n",
    ],
)
def test_bad_audit_toml_names_the_file(text: str) -> None:
    fs = _fs(f"bad-{abs(hash(text))}")
    fs.write_text("audit.toml", text)
    with pytest.raises(ValueError, match=r"audit\.toml"):
        load_deployment(fs)


def test_store_pilot_is_none_when_absent_and_parsed_when_present() -> None:
    fs = _fs("pilot")
    assert store_pilot(fs) is None
    fs.write_text(
        "pilot.yaml",
        "defaults:\n  logs:\n    include_mock: true\nevals:\n  inspect_evals/x:\n    dataset:\n      task: x_task\n",
    )
    config = store_pilot(fs)
    assert config is not None and config.for_eval("inspect_evals/x").dataset is not None


def test_a_malformed_store_pilot_names_the_file() -> None:
    fs = _fs("pilot-bad")
    fs.write_text("pilot.yaml", "- not a mapping\n")
    with pytest.raises(ValueError, match=r"pilot\.yaml"):
        store_pilot(fs)
