"""A no-memory-controller receipt requires structurally valid proc evidence."""
import importlib
import json
from pathlib import Path

import pytest


HEADER = '#subsys_name hierarchy num_cgroups enabled\n'


@pytest.fixture
def invoke(monkeypatch, capsys, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    resources = importlib.import_module('tools.parity.resources')
    sources = resources.sources
    monkeypatch.setattr(sources.sys, 'platform', 'linux')
    monkeypatch.setattr(sources, 'address_space_limit', lambda: None)
    monkeypatch.setattr(sources.os, 'cpu_count', lambda: 2)
    monkeypatch.setattr(sources, 'disk_capacity', lambda directory: dict(
        api='fstatvfs', directory=str(tmp_path), available_bytes=10000000))

    def call(controllers):
        files = {'/proc/meminfo': 'MemTotal: 1000 kB\nMemAvailable: 800 kB\n',
                 '/proc/self/cgroup': '1:cpu:/\n', '/proc/cgroups': controllers}
        monkeypatch.setattr(sources, 'read_text', lambda path, source, **kwargs: files[str(path)])
        # The public operator argv is unchanged; only OS measurement seams are simulated.
        code = resources.main(['--reserve-fraction', '0.25', '--record-bytes', '256',
                               '--output-dir', '.'])
        output = capsys.readouterr()
        return code, output.out, output.err
    return call


def refused(invoke, text):
    code, out, err = invoke(text)
    assert code == 2, 'malformed controller evidence was accepted'
    assert out == ''
    assert json.loads(err) == dict(schema='parity-resource-preflight/v1', status='refused',
                                   platform='linux', code='invalid-source',
                                   source='/proc/cgroups', cgroup=None)


def test_empty_controller_file_refuses_public_cli(invoke):
    refused(invoke, '')


def test_reported_malformed_disabled_memory_refuses_public_cli(invoke):
    refused(invoke, 'memory not-a-hierarchy not-a-count 0\n')


def test_controller_header_required(invoke):
    for text in ('cpu 1 1 1\n', '#bad_header hierarchy num_cgroups enabled\ncpu 1 1 1\n'):
        refused(invoke, text)


def test_controller_row_requires_four_fields(invoke):
    for text in ('cpu 1 1\n', 'cpu 1 1 1 extra\n', '\n'):
        refused(invoke, HEADER + text)


def test_controller_names_are_valid(invoke):
    for name in ('cpu!', 'Memory', '1memory'):
        refused(invoke, HEADER + f'{name} 1 1 0\n')


def test_duplicate_controller_names_refuse(invoke):
    for name in ('memory', 'cpu'):
        refused(invoke, HEADER + f'{name} 0 1 0\n{name} 0 1 0\n')


def test_controller_hierarchy_is_unsigned(invoke):
    for value in ('not-a-hierarchy', '-1', '01', '1.0'):
        refused(invoke, HEADER + f'memory {value} 1 0\n')


def test_controller_count_is_unsigned(invoke):
    for value in ('not-a-count', '-1', '01', '1.0'):
        refused(invoke, HEADER + f'memory 0 {value} 0\n')


def test_unrelated_controller_fields_are_validated(invoke):
    refused(invoke, HEADER + 'cpu not-a-hierarchy 1 1\n')
    refused(invoke, HEADER + 'cpu 1 not-a-count 1\n')


def test_controller_enabled_is_binary(invoke):
    for value in ('2', '-1', 'false', '00'):
        refused(invoke, HEADER + f'cpu 1 1 {value}\n')


def test_valid_absence_and_disabled_memory_remain_supported(invoke):
    for text in (HEADER, HEADER + 'cpu 1 1 1\n', HEADER + 'cpu 1 1 1\nmemory 0 1 0\n'):
        code, out, err = invoke(text)
        assert code == 0 and err == ''
        assert json.loads(out)['cgroup'] == dict(mode='none', mount=None, mount_root=None,
                                                 process_path=None, ancestors=[])


def test_enabled_memory_without_membership_refuses_mapping(invoke):
    code, out, err = invoke(HEADER + 'cpu 1 1 1\nmemory 0 1 1\n')
    assert code == 2 and out == ''
    assert json.loads(err)['code'] == 'cgroup-mapping-unsupported'
