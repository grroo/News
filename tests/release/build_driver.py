"""Offline integration boundary: real guard/builder/provider parsing, fake HTTP provider.

Reads one JSON command from stdin. Only temporary paths supplied by the test
are written. No credential or external network is used.
"""
import json
import os
from pathlib import Path
import sys
from datetime import datetime
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import build
import check_slot
import requests
import yaml

spec = json.load(sys.stdin)
root = Path(spec['root'])
root.mkdir(exist_ok=True)
config = yaml.safe_load((ROOT / 'config.yml').read_text())
# The fake HTTP below speaks Anthropic; pin it whatever production uses.
config.update({'provider': 'anthropic', 'model': 'claude-haiku-4-5'})
config.update(spec.get('config_changes', {}))
(root / 'config.yml').write_text(yaml.safe_dump(config))
inputs = spec['inputs']
now = spec['now']
local = json.loads((root / 'briefing.json').read_text()) if (root / 'briefing.json').exists() else {}
allowed, publish, reason = check_slot.decide(
    inputs.get('scheduled_slot'), datetime.fromisoformat(now.replace('Z', '+00:00')),
    build.schedule_metadata(), spec.get('live', {}), local,
    strict_gate=True, request_id=inputs.get('request_id'), reservation_id=inputs.get('reservation_id'),
    require_reservation=True, has_api_key=not spec.get('missing_key'),
    deploy_only=inputs.get('deploy_only', False),
)
calls = []
def response(url, **kwargs):
    if url != 'https://api.anthropic.com/v1/messages':
        raise AssertionError('Unexpected external request')
    body = kwargs['json']
    section = body['messages'][0]['content'].split('\n', 1)[0]
    calls.append(section)
    if spec.get('fail_sport') and section == 'SECTION: sport':
        raise requests.Timeout('synthetic timeout')
    value = requests.Response()
    value.status_code = 200
    value.headers['request-id'] = 'fixture-request'
    value._content = json.dumps({'usage': {'input_tokens': 100, 'output_tokens': 50}, 'content': [{
        'type': 'tool_use', 'name': 'submit_briefing', 'input': {
            'briefing': [{'text': 'Fixture claim.', 'source_ids': [0]}],
            'items': [{'id': 0, 'title': 'Fixture headline', 'summary': 'Fixture summary.'}],
        },
    }]}).encode()
    return value
paths = dict(DATA_DIR=root, PAST_DIR=root/'past', SEEN_PATH=root/'seen.json',
             OUT_PATH=root/'briefing.json', CONFIG_PATH=root/'config.yml')
env = {'ANTHROPIC_API_KEY': 'offline-placeholder', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
       'REQUEST_ID': inputs.get('request_id', '')}
if inputs.get('scheduled_slot'):
    env['SCHEDULED_SLOT'] = inputs['scheduled_slot']
if allowed:
    with patch.multiple(build, **paths), patch.dict(os.environ, env, clear=True), \
         patch.object(sys, 'argv', ['build.py', '--fixtures', str(root/'fixtures'), '--now', now]), \
         patch('requests.post', side_effect=response), patch('requests.get', side_effect=AssertionError('network forbidden')), \
         patch('llm_provider.time.sleep'):
        build.main()
print(json.dumps({'allowed': allowed, 'publish': publish, 'reason': reason, 'calls': calls,
                  'edition': json.loads((root/'briefing.json').read_text()) if (root/'briefing.json').exists() else None}))
