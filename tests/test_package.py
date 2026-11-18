"""Audit packages: a rubric and its formats, found by name, from inside or outside inspect_audit."""

import json
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest
from test_audit import git_repo

from inspect_audit import package as packages
from inspect_audit._findings import AuditorFindings, Findings, Rating
from inspect_audit._verdicts import Evidence


@pytest.fixture
def toy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install the toy package beside gl, as another distribution's entry point would."""
    real = packages.entry_points

    def entry_points(*, group: str) -> list[EntryPoint]:
        return [
            *real(group=group),
            EntryPoint(name="toy", value="test_helpers.toy_package:package", group=group),
        ]

    monkeypatch.setattr(packages, "entry_points", entry_points)


def rated(**labels: str) -> Findings:
    evidence = [Evidence(observed="o", source="s")]
    return Findings(
        auditor=AuditorFindings(
            summary="s",
            ratings={
                k: Rating(label=v, explanation="e", evidence=evidence) for k, v in labels.items()
            },
        )
    )


def test_gl_ships_as_a_package_with_latex_first() -> None:
    assert "gl" in packages.installed()
    chosen = packages.select(None, None)
    assert (chosen.name, chosen.format) == ("gl", "latex")
    assert chosen.package.rubric.skill.name == "audit-framework"
    # configs written before packages named the web format `gl`
    assert packages.select(None, "gl").format == "web"


def test_an_unknown_package_or_format_names_what_is_installed(toy: None) -> None:
    with pytest.raises(ValueError, match=r"No audit package 'nope'.*installed: .*gl, toy"):
        packages.select("nope", None)
    with pytest.raises(ValueError, match=r"'pdf' is not a format of this package: page"):
        packages.select("toy", "pdf")


def test_submission_is_checked_by_the_rubric_whatever_the_format(toy: None) -> None:
    """The GL framework's ratings are required for the web format too, not only LaTeX."""
    web = packages.select("gl", "web")
    with pytest.raises(ValueError, match=r"needs ratings\.overall"):
        web.validate(Findings())
    page = packages.select("toy", None)
    with pytest.raises(ValueError, match="needs ratings for q2"):
        page.validate(rated(q1="Pass"))
    page.validate(rated(q1="Pass", q2="Flag"))


def test_an_outside_package_sets_what_the_auditor_rates_against(toy: None, tmp_path: Path) -> None:
    from inspect_audit import audit

    target = audit(
        str(git_repo(tmp_path / "repo")), package="toy", output_dir=str(tmp_path / "runs")
    )
    root = Path(target.metadata["audit_dir"])
    framework = root / "work/analysis/framework"
    assert sorted(p.name for p in framework.iterdir()) == ["questions.json"]
    configuration = json.loads(json.loads((root / "policy.json").read_text())["configuration_json"])
    assert configuration["package"] == "toy"


def test_investigators_load_the_packages_rubric_skill(toy: None) -> None:
    from inspect_audit._audit import _skill_paths

    toy_skill = packages.load_package("toy").rubric.skill
    paths = _skill_paths(toy_skill, [])
    assert str(toy_skill) in paths
    assert not any(Path(p).name == "audit-framework" for p in paths)


def test_the_package_listing_prints_every_installed_package(
    toy: None, capsys: pytest.CaptureFixture[str]
) -> None:
    packages.main()
    lines = capsys.readouterr().out.splitlines()
    names = [line.split("\t")[0] for line in lines]
    assert names == sorted(names) and {"gl", "toy"} <= set(names)
    assert "formats: latex, web" in lines[names.index("gl")]


def test_investigation_task_never_rebinds_its_audit_package() -> None:
    """Smoke round of 17 Nov: the parameter was `package`, which the function also uses for
    the benchmark's package name, so every registry-named investigation looked for an audit
    package called `inspect_evals`. Building one needs a registered benchmark, so this reads
    the code instead: the parameter is only read."""
    import ast
    import inspect

    from inspect_audit._task import investigation_task

    tree = ast.parse(inspect.getsource(investigation_task))
    stores = [
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    ]
    assert "audit_package" not in stores
