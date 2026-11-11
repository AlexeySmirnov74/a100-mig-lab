"""Check checked-in dashboards, XML assets and local Markdown references without a GPU."""
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / 'config/grafana/dashboards').glob('*.json'):
    data = json.loads(path.read_text(encoding='utf-8'))
    assert data['panels'], f'Empty dashboard: {path}'
    assert data.get('refresh') == '5s', f'Unexpected refresh: {path}'
    for panel in data['panels']:
        for target in panel.get('targets', []):
            assert 'DCGM_FI_DEV_GPU_UTIL' not in target.get('expr', ''), path
            assert 'or vector(0)' not in target.get('expr', ''), path

for path in (ROOT / 'docs/assets').glob('*.svg'):
    ET.parse(path)

for path in [ROOT / 'README.md', ROOT / 'README.ru.md', *ROOT.glob('docs/**/*.md')]:
    content = path.read_text(encoding='utf-8')
    for target in re.findall(r'\]\(([^)\s]+)\)', content):
        if target.startswith(('https://', 'http://', '#', 'mailto:')):
            continue
        target = target.split('#')[0]
        assert (path.parent / target).exists(), f'{path}: missing {target}'
print('Dashboard JSON, SVG assets and local documentation links are valid.')
