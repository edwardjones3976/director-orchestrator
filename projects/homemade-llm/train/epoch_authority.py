"""Epoch authority shared by the trusted launcher and the genuine trainer.

The host/admin files and Python interpreter are trusted. These fixed paths are
not a signature system or protection from a same-account host rewrite. The
wrapper supplies this code from the approved candidate commit, checked against
the digest embedded in the root-authenticated trainer. No staged code runs
until its bytes have been checked; the launcher executes those same buffers.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import types

TEMPLATE_SHA256 = '6ee4833fbce2b0f38f37b5cd51f5424cd154a78cbfc9d9ae3643359a709908ea'
CANDIDATE_KEYS = {'commit', 'trainer_sha256', 'predictor_sha256', 'scorer_sha256', 'template_sha256'}
PUBLICATION_KEYS = {'format', 'batch', 'published_by', 'publication_profile_sha256',
                    'accepted_jsonl_sha256', 'bundle_manifest_sha256', 'recount_sha256'}


def refuse(message):
    raise SystemExit('epoch authority: ' + message)


def strict(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate key: ' + key)
            result[key] = value
        return result
    def constant(value):
        raise ValueError('non-finite constant: ' + value)
    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError) as exc:
        refuse('invalid JSON: ' + str(exc))


def read(path):
    path = Path(path).absolute()
    try:
        for item in (path, *path.parents):
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                refuse('symlink or reparse authority/payload path')
        if not path.is_file():
            refuse('required file missing: ' + str(path))
        return path.read_bytes()
    except OSError as exc:
        refuse('required file unreadable: ' + str(exc))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def hex_value(value, length=64):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{' + str(length) + '}', value) is not None


def production_paths(batch, root_file=None):
    # Deliberately no environment, command-line, binding or rows-path override.
    if os.name == 'nt':
        custody = Path('C:/llm/reports/route-r')
        return (Path('C:/llm/runs/m2/rr/route_r_root_pin.py'),
                custody / root_file if root_file else None,
                custody / 'publication' / (batch + '.publication.json'))
    authority = Path('/run/route-r-authority')
    return authority / 'root-pin.py', authority / 'root.json', authority / 'publication.json'


def load(batch):
    if not isinstance(batch, str) or re.fullmatch('[a-z0-9][a-z0-9-]{0,63}', batch) is None:
        refuse('invalid batch')
    pin_path, _, _ = production_paths(batch)
    try:
        text = read(pin_path).decode('utf-8')
    except UnicodeError:
        refuse('pin is not UTF-8')
    pin = {}
    for key in ('ROOT_FILE', 'ROOT_FORMAT', 'APPROVED_TRUST_ROOT_SHA256'):
        definitions = re.findall(r'(?m)^[ \t]*' + key + r'[ \t]*=', text)
        values = re.findall(r'(?m)^[ \t]*' + key + r'''[ \t]*=[ \t]*(?:'([^'\r\n]*)'|"([^"\r\n]*)")[ \t]*(?:#[^\r\n]*)?\r?$''', text)
        if len(definitions) != 1 or len(values) != 1:
            refuse('missing, unapproved or duplicate pin: ' + key)
        pin[key] = values[0][0] or values[0][1]
    if (not hex_value(pin['APPROVED_TRUST_ROOT_SHA256']) or
            re.fullmatch(r'route-r-trust-root-v[0-9a-z]+\.json', pin['ROOT_FILE']) is None):
        refuse('invalid root pin')
    _, root_path, publication_path = production_paths(batch, pin['ROOT_FILE'])
    root_bytes = read(root_path)
    if digest(root_bytes) != pin['APPROVED_TRUST_ROOT_SHA256']:
        refuse('root bytes differ from approved pin')
    root = strict(root_bytes)
    if not isinstance(root, dict) or root.get('format') != pin['ROOT_FORMAT']:
        refuse('root format')
    consumption = root.get('adapter_consumption')
    if not isinstance(consumption, dict) or set(consumption) != {'resolver_entry', 'interpreter', 'bridge', 'candidate'}:
        refuse('admission authority missing or unqualified')
    for name in ('resolver_entry', 'interpreter', 'bridge'):
        entry = consumption[name]
        if (not isinstance(entry, dict) or set(entry) != {'path', 'sha256'} or
                not isinstance(entry['path'], str) or not entry['path'] or not hex_value(entry['sha256'])):
            refuse('unqualified ' + name)
    candidate = consumption['candidate']
    if (not isinstance(candidate, dict) or set(candidate) != CANDIDATE_KEYS or
            not hex_value(candidate['commit'], 40) or
            any(not hex_value(candidate[k]) for k in CANDIDATE_KEYS - {'commit'}) or
            candidate['template_sha256'] != TEMPLATE_SHA256):
        refuse('unqualified candidate')
    schema = root.get('row_schema')
    if not isinstance(schema, dict) or not hex_value(schema.get('sha256')):
        refuse('unqualified row schema')
    custody = root.get('custody')
    admins = custody.get('admins') if isinstance(custody, dict) else None
    if (not isinstance(admins, list) or not admins or
            any(not isinstance(name, str) or not name or name != name.strip() for name in admins) or
            len(set(admins)) != len(admins)):
        refuse('unqualified custody administrators')
    pub_bytes = read(publication_path)
    pub = strict(pub_bytes)
    if (not isinstance(pub, dict) or set(pub) != PUBLICATION_KEYS or
            pub['format'] != 'route-r-publication/v1' or pub['batch'] != batch or
            not isinstance(pub['published_by'], str) or pub['published_by'] not in admins or
            any(not hex_value(pub[k]) for k in PUBLICATION_KEYS - {'format', 'batch', 'published_by'})):
        refuse('invalid protected publication')
    return root, pin['APPROVED_TRUST_ROOT_SHA256'], pub, digest(pub_bytes)


def authenticate(binding, profile, accepted_sha, report_sha, profile_sha):
    root, root_sha, pub, pub_sha = load(binding.get('batch'))
    resolution = binding.get('resolution')
    if not isinstance(resolution, dict):
        refuse('invalid resolution')
    if (binding.get('root_sha256') != root_sha or resolution.get('trust_root_sha256') != root_sha or
            profile.get('trust_root_sha256') != root_sha):
        refuse('unapproved root identity')
    if binding.get('candidate') != root['adapter_consumption']['candidate']:
        refuse('candidate differs from approved root')
    if resolution.get('bridge_sha256') != root['adapter_consumption']['bridge']['sha256']:
        refuse('unapproved resolver bridge')
    if (resolution.get('publication_sha256') != pub_sha or
            profile_sha != pub['publication_profile_sha256'] or accepted_sha != pub['accepted_jsonl_sha256']):
        refuse('payload differs from protected publication')
    if (profile.get('validation_report_sha256') != report_sha or
            profile.get('accepted_jsonl_sha256') != accepted_sha or profile.get('batch') != pub['batch'] or
            profile.get('bundle_manifest_sha256') != pub['bundle_manifest_sha256'] or
            profile.get('recount_sha256') != pub['recount_sha256']):
        refuse('profile differs from protected publication chain')
    if profile.get('row_schema_sha256') != root['row_schema']['sha256']:
        refuse('row schema differs from approved contract')
    if not isinstance(profile.get('built_by'), str) or profile['built_by'] not in root['custody']['admins']:
        refuse('profile builder is not an approved administrator')
    return root


def bootstrap():
    # Called only by the trusted wrapper's Python -I -B -c command. Paths are
    # fixed, never arguments supplied by the binding or by the staged program.
    binding = strict(read('/workspace/publication-binding.json'))
    if not isinstance(binding, dict):
        refuse('invalid binding')
    profile_bytes = read('/workspace/rows/profile.json')
    profile = strict(profile_bytes)
    if not isinstance(profile, dict):
        refuse('invalid profile')
    root = authenticate(binding, profile, digest(read('/workspace/rows/accepted.jsonl')),
                        digest(read('/workspace/rows/report.json')), digest(profile_bytes))
    candidate = root['adapter_consumption']['candidate']
    code = {}
    for name, path, key in (
        ('prompt_template', '/train/prompt_template.py', 'template_sha256'),
        ('dev_selection', '/eval/dev_selection.py', 'scorer_sha256'),
        ('predict_dev', '/eval/predict_dev.py', 'predictor_sha256'),
        ('__main__', '/train/train_adapter.py', 'trainer_sha256'),
    ):
        data = read(path)
        if digest(data) != candidate[key]:
            refuse('code differs from approved candidate: ' + name)
        code[name] = (path, data)
    # All imports below use authenticated buffers, not a later path read. Keep
    # __file__ for existing predictor/template source identity checks.
    authority = types.ModuleType('epoch_authority')
    authority.__dict__.update(globals())
    sys.modules['epoch_authority'] = authority
    for name, (path, data) in code.items():
        module = types.ModuleType(name)
        module.__file__ = path
        sys.modules[name] = module
        if name == 'predict_dev':
            module.__dict__['_EPOCH_TEMPLATE_BYTES'] = code['prompt_template'][1]
        if name == '__main__':
            sys.argv[0] = path
            module.__dict__['_BOOTSTRAP_AUTHORITY'] = authority
        exec(compile(data, path, 'exec'), module.__dict__)
