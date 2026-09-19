from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from agy_session import stream_command

SESSION = Path(__file__).with_name("agy_session.py")
FAKE_CLI = r'''
import json, os, select, subprocess, sys, time
from pathlib import Path
scenario = os.environ['SCENARIO']
conversation = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
def emit(event, **data):
 print(json.dumps({'event': event, **data}), flush=True)
message = json.loads(sys.stdin.readline())
assert message['event'] == 'user'
assert '--input-format' in sys.argv and '--output-format' in sys.argv
assert '--print=' in sys.argv
emit('init', conversation_id=conversation)
if scenario == 'missing':
 sys.exit(0)
emit('step_update', step_update={'conversation_id': conversation, 'step_index': 2,
 'step_type': 'tool', 'tool_name': 'run_command', 'state': 'ACTIVE'})
if scenario == 'signal':
 time.sleep(60)
if scenario == 'pending':
 for _ in range(3):
  emit('result', result={'status': 'SUCCESS'})
  if not sys.stdin.readline(): break
 sys.exit(0)
start = time.monotonic()
duration = 6.1 if scenario == 'long' else .05
code = 7 if scenario == 'nonzero' else 0
p = subprocess.Popen([sys.executable, '-c', 'import time,sys; time.sleep(float(sys.argv[1])); sys.exit(int(sys.argv[2]))', str(duration), str(code)])
while p.poll() is None:
 if select.select([sys.stdin], [], [], .01)[0]:
  # EOF before the command completes is the original lifecycle defect.
  p.terminate(); p.wait(); sys.exit(90)
p.wait()
header = Path(os.environ['ANTIGRAVITY_HOME']) / '.gemini/antigravity-cli/brain' / conversation / '.system_generated/steps/2/output.txt'
header.parent.mkdir(parents=True, exist_ok=True)
if scenario != 'unknown':
 header.write_text('\nThe command exited with code %s.\nOutput:\nSECRET_OUTPUT\n' % code)
emit('step_update', step_update={'conversation_id': conversation, 'step_index': 2,
 'step_type': 'tool', 'tool_name': 'run_command', 'state': 'CANCELLED' if scenario == 'cancel' else 'DONE',
 'duration_seconds': time.monotonic()-start,
 'tool_info': {'output': 'terminating 1 background task(s) on exit'}})
if scenario == 'stderr_cancel':
 print('terminating 1 background task(s) on exit', file=sys.stderr, flush=True)
emit('result', result={'status': 'SUCCESS'})
assert sys.stdin.read() == ''
'''


def launch(tmp_path: Path, scenario: str) -> tuple[subprocess.Popen, Path]:
    fake = tmp_path / 'fake.py'
    fake.write_text(FAKE_CLI)
    marker = tmp_path / 'runner.json'
    env = {**os.environ, 'SCENARIO': scenario, 'ANTIGRAVITY_HOME': str(tmp_path / 'agy'),
           'ORCH_RUNNER_STATUS_PATH': str(marker)}
    child = subprocess.Popen([sys.executable, str(SESSION), '--', sys.executable, str(fake),
                              '--model', 'unchanged', '--print-timeout', '2h', '--prompt', 'SECRET_PROMPT'],
                             env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return child, Path(str(marker) + '.agy.json')


@pytest.mark.parametrize('scenario,expected,status', [
    ('short', 0, 'completed'), ('long', 0, 'completed'),
    ('nonzero', 0, 'completed'),  # The receipt preserves command exit 7; it is never reported as command success.
    ('cancel', 75, 'interrupted'), ('stderr_cancel', 75, 'interrupted'),
    ('unknown', 75, 'interrupted'), ('missing', 75, 'failed'), ('pending', 75, 'interrupted'),
])
def test_stream_waits_for_terminal_command(tmp_path, scenario, expected, status):
    child, marker = launch(tmp_path, scenario)
    stdout, stderr = child.communicate(timeout=20)
    assert child.returncode == expected, (stdout, stderr)
    receipt = json.loads(marker.read_text())
    assert receipt['status'] == status
    assert 'SECRET_PROMPT' not in marker.read_text()
    assert 'SECRET_OUTPUT' not in marker.read_text()
    if scenario in {'short', 'long', 'nonzero'}:
        assert receipt['tools'][0]['exit_code'] == (7 if scenario == 'nonzero' else 0)
        assert receipt['tools'][0]['state'] == 'DONE'
    if scenario == 'long':
        assert receipt['duration_seconds'] >= 6
        assert receipt['tools'][0]['duration_seconds'] >= 6


def test_signal_cancels_session_and_preserves_receipt(tmp_path):
    child, marker = launch(tmp_path, 'signal')
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if marker.exists() and json.loads(marker.read_text()).get('tools'):
            break
        time.sleep(.02)
    else:
        child.kill()
        pytest.fail('session did not start its command')
    child.send_signal(signal.SIGTERM)
    child.communicate(timeout=15)
    assert child.returncode == 143
    receipt = json.loads(marker.read_text())
    assert receipt['status'] == 'interrupted'
    assert receipt['signal'] == signal.SIGTERM
    assert receipt['tools'][0]['exit_code'] is None


def test_stream_preserves_original_options_without_shell_interpolation():
    command, prompt = stream_command(['agy', '--model', 'model space', '--print-timeout', '2h',
                                      '--prompt', 'literal $(not-a-command)'])
    assert command[:5] == ['agy', '--model', 'model space', '--print-timeout', '2h']
    assert prompt == 'literal $(not-a-command)'
    assert command[-5:] == ['--print=', '--input-format', 'stream-json', '--output-format', 'stream-json']
    with pytest.raises(ValueError):
        stream_command(['agy', '--prompt', 'a', '--input-format', 'text'])
