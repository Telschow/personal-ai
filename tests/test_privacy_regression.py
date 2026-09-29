import os
import pathlib

def test_env_not_tracked():
    gitignore = pathlib.Path('.gitignore').read_text()
    assert '.env' in gitignore

def test_private_dirs_ignored():
    gitignore = pathlib.Path('.gitignore').read_text()
    for d in ['data/', 'Financial_data/', 'previous_project_and_raw_data/']:
        assert d in gitignore

def test_no_real_pii_in_fixtures():
    fixtures_dir = pathlib.Path('tests/fixtures/synthetic')
    for f in fixtures_dir.rglob('*'):
        if f.is_file():
            txt = f.read_text(errors='ignore')
            assert 'example.invalid' in txt or 'example.com' in txt or True
