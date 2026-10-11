"""Source contracts only. A parsed/mock workflow is not live bundle consumption."""
from pathlib import Path

import yaml

PATH = Path('.github/workflows/dev-smoke-credential-consumer.yml')


def workflow():
    return yaml.load(PATH.read_text(), Loader=yaml.BaseLoader)


def test_consumer_is_fresh_manual_dev_job_not_another_build_or_deploy():
    w = workflow()
    assert set(w['on']) == {'workflow_dispatch'}
    assert set(w['on']['workflow_dispatch']['inputs']) == {'release_sha', 'manifest_digest', 'source_pull', 'reviewed_head'}
    job = w['jobs']['consume']
    assert job['environment'] == 'dev' and job['timeout-minutes'] == '12'
    assert "github.ref == 'refs/heads/dev'" in job['if']
    assert w['permissions'] == {'contents': 'read', 'actions': 'read', 'deployments': 'read', 'id-token': 'write'}
    text = PATH.read_text()
    assert 'docker build' not in text and 'gcloud run' not in text and 'gh workflow run' not in text
    assert 'cancel-in-progress: false' in text


def test_only_trusted_consuming_step_receives_pair_no_global_secret_inheritance():
    job = workflow()['jobs']['consume']
    assert 'env' not in job
    steps = job['steps']
    consuming = next(s for s in steps if 'consume_dev_smoke' in s.get('run', ''))
    projected = [s for s in steps if 'ODP_DEV_ADMIN_CREDENTIAL_BUNDLE' in s.get('env', {})]
    assert projected == [consuming]
    assert consuming['env']['ODP_DEV_ADMIN_CREDENTIAL_BUNDLE'] == '${{ secrets.ODP_DEV_ADMIN_CREDENTIAL_BUNDLE }}'
    assert consuming['env']['GH_TOKEN'] == '${{ github.token }}'
    assert 'ODP_DEV_ADMIN_PASSWORD' not in consuming['env']
    assert 'ODP_DEV_BOOTSTRAP_ADMIN_PASSWORD' not in consuming['env']
    setup = next(s for s in steps if 'uv sync' in s.get('run', ''))
    assert steps.index(setup) < steps.index(consuming)
    assert '--frozen --no-dev --python 3.12' in setup['run']
    assert '.venv/bin/python -m delivery_toolchain.release.consume_dev_smoke' in consuming['run']
    assert '${{ secrets.' not in consuming['run']  # never interpolate into a retained shell file


def test_existing_native_wif_does_not_materialize_new_credential_file_or_use_foreground_pat():
    steps = workflow()['jobs']['consume']['steps']
    assert not any(s.get('uses', '').startswith('google-github-actions/auth') for s in steps)
    consumer = next(s for s in steps if 'consume_dev_smoke' in s.get('run', ''))
    assert 'ODP_API_INVOKER_TOKEN' not in consumer['env']
    assert consumer['env']['GOOGLE_SERVICE_ACCOUNT'] == '${{ vars.GCP_SERVICE_ACCOUNT }}'
    assert consumer['env']['GOOGLE_WIF_PROVIDER'] == '${{ vars.GCP_WORKLOAD_IDENTITY_PROVIDER }}'
    source = Path('delivery_toolchain/release/consume_dev_smoke.py').read_text()
    assert 'https://sts.googleapis.com/v1/token' in source
    assert 'https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/' in source
    assert 'gcloud auth' not in source and 'create_credentials_file' not in source
    assert 'PAT' not in PATH.read_text()
    checkout = next(s for s in steps if s.get('uses') == 'actions/checkout@v4')
    assert checkout['with']['persist-credentials'] == 'false'
    artifact = steps[-1]
    assert artifact['with']['path'] == '.odp_data/native-consumer/consumption.json'
    assert 'if' not in artifact  # success-only, never always/upload a failed raw report
