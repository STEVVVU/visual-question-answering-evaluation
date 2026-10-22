"""Re-score saved validation exports without generation or modifying the originals."""
import hashlib
import json
from pathlib import Path
import sys
from data_formulator.vqa import SCORER, score

if len(sys.argv) != 3:
    raise SystemExit('Usage: python tools/rescore-vqa-validation.py <retained-validation-directory> <new-output.json>')
root, target = (Path(value).resolve() for value in sys.argv[1:])
if target.exists():
    raise SystemExit('Refusing to overwrite existing evidence.')
campaign = json.loads((root / 'benchmark.json').read_text())
assert campaign['dataset']['split'] == 'validation' and campaign['dataset']['count'] == 16
result = {'source_run': root.name, 'source_sha': campaign['source'], 'split': 'validation',
          'source_benchmark_sha256': hashlib.sha256((root / 'benchmark.json').read_bytes()).hexdigest(),
          'original_scorer': 'mode-exact-v2', 'revised_scorer': SCORER, 'new_generation': False, 'repeats': []}
for repeat in campaign['repeats']:
    rows, exports = [], []
    for batch in repeat['batches']:
        path = root / batch['export']
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == batch['export_sha256']
        exported = json.loads(path.read_text())
        assert exported['run']['scorer'] == 'mode-exact-v2'
        annotations = {item['id']: item for item in exported['version']['manifest']['items']}
        exports.append({'file': path.name, 'sha256': digest})
        for prediction in exported['run']['results']:
            item = annotations[prediction['id']]
            revised = score(item, prediction['prediction'])['correct']
            recognized = any(score({**item, 'answer_label': index}, prediction['prediction'])['correct'] for index in range(len(item['choices'])))
            rows.append({'source_id': batch['source_ids'][prediction['id']], 'prediction': prediction['prediction'],
                         'before': prediction['score']['correct'], 'after': revised, 'recognized': recognized})
    assert len(rows) == 16 and len({row['source_id'] for row in rows}) == 16
    result['repeats'].append({'repeat': repeat['repeat'], 'before_correct': sum(row['before'] for row in rows),
        'after_correct': sum(row['after'] for row in rows), 'before_unrecognized': sum(b['unrecognized_answers'] for b in repeat['batches']),
        'after_unrecognized': sum(not row['recognized'] for row in rows), 'total': len(rows), 'exports': exports, 'rows': rows})
target.write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({'evidence': str(target), 'repeats': [{k: v for k, v in row.items() if k not in ('rows', 'exports')} for row in result['repeats']]}))
