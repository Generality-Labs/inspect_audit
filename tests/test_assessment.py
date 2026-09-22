"""Publication should reject missing assessments and inconsistent derived counts."""
import json
import shutil
from pathlib import Path

import pytest

from inspect_audit._assessment import (
    assessment_tables,
    framework_checks,
    prepare_assessments,
)
from inspect_audit._coverage import coverage_summary


def assessment_report(tmp_path):
    source = Path(__file__).parents[1] / 'src/inspect_audit/investigation/report'
    shutil.copytree(source, tmp_path, dirs_exist_ok=True)
    prepare_assessments(tmp_path)
    rows = [dict(id=key, assessment='Not assessed', result='Outside the scope of this fixture', evidence=[])
            for key in framework_checks(tmp_path)]
    (tmp_path / 'assessments.json').write_text(json.dumps(rows))
    return rows


def test_framework_and_missing_checks(tmp_path):
    rows = assessment_report(tmp_path)
    checks = framework_checks(tmp_path)
    assert len([key for key in checks if '.' not in key]) == 9
    assert checks['D.2'] == 'planning or agent loop'
    assert checks['G.4'] == 'surface-form sensitivity'
    assert 'Not assessed' in assessment_tables(tmp_path)
    (tmp_path / 'assessments.json').write_text(json.dumps(rows[:-1]))
    with pytest.raises(ValueError, match='every framework'):
        assessment_tables(tmp_path)


def test_counts_population_and_escaping(tmp_path):
    rows = assessment_report(tmp_path)
    label = dict(question_id='q', status='DEFECT', checks=['test'], evidence=['source.py:1'],
                 explanation='Mismatch', defect_types=['missing_information'])
    coverage = coverage_summary(['q'], [label])
    (tmp_path / 'coverage.json').write_text(json.dumps(coverage))
    row = rows[0]
    row.update(assessment='Major', result='<script>bad</script>', evidence=['source.py:1'],
               checked_ids=['q'], affected_ids=['q'])
    (tmp_path / 'assessments.json').write_text(json.dumps(rows))
    rendered = assessment_tables(tmp_path)
    assert '<script>' not in rendered and '&lt;script&gt;' in rendered
    assert 'assessment major' in rendered and '1/1' in rendered
    row['affected_ids'] = ['unknown']
    (tmp_path / 'assessments.json').write_text(json.dumps(rows))
    with pytest.raises(ValueError, match='affected IDs'):
        assessment_tables(tmp_path)
    row['affected_ids'] = ['q']
    (tmp_path / 'assessments.json').write_text(json.dumps(rows))
    coverage['counts']['DEFECT'] = 99
    (tmp_path / 'coverage.json').write_text(json.dumps(coverage))
    with pytest.raises(ValueError, match='export_coverage'):
        assessment_tables(tmp_path)


def test_definitions_preserve_framework_meaning(tmp_path):
    from inspect_audit._assessment import framework_definitions

    assessment_report(tmp_path)
    definitions = framework_definitions(tmp_path)
    assert 'models differently' in definitions['H.3']['definition']
    assert 'model scores cluster' in definitions['I.3']['definition']
    staged = json.loads((tmp_path / 'framework/checks.json').read_text())
    assert staged == definitions


def test_latex_escaping_and_required_finding_home(tmp_path):
    from inspect_audit._assessment import assessment_latex, validate_latex_structure

    rows = assessment_report(tmp_path)
    rows[0]['result'] = r'5% & input_name #1 {literal} \input{untrusted}'
    (tmp_path / 'assessments.json').write_text(json.dumps(rows))
    tex = assessment_latex(tmp_path)
    assert r'5\% \& input\_name \#1' in tex
    assert r'\textbackslash{}input\{untrusted\}' in tex
    assert tex.count(r'\contributionrow') == 47
    validate_latex_structure(tmp_path, [])
    with pytest.raises(ValueError, match='explain this finding'):
        validate_latex_structure(tmp_path, [('F1', 'resources')])
    p = tmp_path / 'Findings.tex'
    source = p.read_text()
    source = source.replace(r'\begin{dimensionreview}{resources}',
                            r'\begin{dimensionreview}{resources}\label{finding:F1}')
    p.write_text(source)
    validate_latex_structure(tmp_path, [('F1', 'resources')])
    p.write_text(source.replace(r'\begin{dimensionreview}{resources}', r'\begin{dimensionreview}{grading}'))
    with pytest.raises(ValueError, match='all nine'):
        validate_latex_structure(tmp_path, [])


def test_template_cannot_be_replaced_or_rubric_redefined(tmp_path):
    from inspect_audit._assessment import validate_latex_structure

    assessment_report(tmp_path)
    wrapper = tmp_path / 'report.tex'
    original = wrapper.read_text()
    wrapper.write_text(original.replace(r'\input{Findings}', r'\input{framework/ScoringCriteria}'))
    with pytest.raises(ValueError, match='wrapper unchanged'):
        validate_latex_structure(tmp_path, [])
    wrapper.write_text(original)
    rubric = tmp_path / 'framework/ScoringCriteria.tex'
    rubric.write_text(rubric.read_text() + '\nChanged rating definitions\n')
    with pytest.raises(ValueError, match='Pinned framework file changed'):
        validate_latex_structure(tmp_path, [])
