"""Publication should reject missing assessments and inconsistent derived counts."""
import json
import shutil
from pathlib import Path

import pytest

from inspect_audit._assessment import assessment_tables, framework_checks, prepare_assessments
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
