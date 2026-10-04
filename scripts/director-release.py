"""Pinned R1 preparation, read-only doctor and sibling rehearsal command plan.

No command here reserves a generation, dispatches a provider, starts a service,
or manufactures acceptance. The existing control script owns execution and gates.
"""
# sys is built in. Refuse before importing anything that an unverified scripts/
# directory, current directory or PYTHONPATH could shadow. The operator must use
# interpreter isolation from process startup; re-exec after imports is too late.
import sys
if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.dont_write_bytecode):
    raise SystemExit("Run director-release.py with Python -I -B (isolated startup without cache writes required).")

import argparse
import base64
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
import pathlib
from pathlib import Path
import re
import signal
import stat
import subprocess
import tempfile
import time
from types import ModuleType
import zlib

from jsonschema import Draft202012Validator
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "docs/schemas/director-release-descriptor-v1.schema.json"
MAX_BYTES = 4 * 1024 * 1024
DEPENDENCIES = {
    "fresh_generation_authority": "primary_generation.require_generation has no read-only fresh-manifest verification path",
    "paid_admission": "the validation CLI has no per-lane campaign/daily-dollar reservation and settlement contract",
}
COMPOSE_SERVICE_FIELDS = {
    "image", "build", "command", "entrypoint", "restart", "environment", "ports", "volumes",
    "depends_on", "healthcheck", "networks", "pull_policy", "deploy", "scale", "working_dir", "logging",
}
HOST_ENVIRONMENT_KEYS = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP",
    "TMPDIR", "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432"}


class ReleaseError(RuntimeError):
    """Only fixed, non-secret reason codes may be reported to the operator."""


def require(condition, reason):
    if not condition:
        raise ReleaseError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def pairs(items):
    result = {}
    for name, value in items:
        require(name not in result, "duplicate_json_key")
        result[name] = value
    return result


def document(raw):
    require(len(raw) <= MAX_BYTES, "oversized_json")
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ReleaseError("nonfinite_json")))


def bounded_read(path):
    path = Path(path)
    unlinked(path)
    require(path.is_file(), "input_path_invalid")
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    require(len(raw) <= MAX_BYTES, "input_too_large")
    return raw


def unlinked(path):
    """Reject symlinks and Windows junctions anywhere in an input/output path."""
    path = Path(path)
    require(path.is_absolute(), "input_path_invalid")
    for part in (path, *path.parents):
        try:
            metadata = part.lstat()
        except FileNotFoundError:
            continue
        require(not stat.S_ISLNK(metadata.st_mode) and not (
            getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)),
            "linked_path_refused")


def launcher_module(path, *, raw=None):
    """Load exact source bytes without consulting or writing an installed .pyc."""
    unlinked(path)
    require(path.is_file(), "trusted_descriptor_consumer_unavailable")
    if raw is None:
        raw = bounded_read(path)
    module = ModuleType("director_release_owner_launcher")
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec"), module.__dict__)
    return module, raw


def account_home():
    """The same OS account anchor as A2; no checkout import or environment claim."""
    if os.name == "nt":
        import ctypes
        import uuid
        folder = (ctypes.c_byte * 16).from_buffer_copy(
            uuid.UUID("5E6C858F-0E22-4760-9AFE-EA3317B67173").bytes_le)
        path = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(folder), 0, None, ctypes.byref(path)) != 0:
            raise OSError("account profile unavailable")
        try:
            return Path(path.value)
        finally:
            ctypes.windll.ole32.CoTaskMemFree(path)
    import pwd
    home = pwd.getpwuid(os.geteuid()).pw_dir
    require(home and os.path.isabs(home), "owner_root_invalid")
    return Path(home)


def installed_owner_launcher():
    """Load captured installed A2 bytes at its existing OS-rooted authority."""
    try:
        root = account_home() / ".director-scorecard"
        require(isinstance(root, Path) and root.is_absolute(), "owner_root_invalid")
        path = root / "launcher.py"
        require(path.is_file(), "trusted_descriptor_consumer_unavailable")
        raw = bounded_read(path)
        installed, _ = launcher_module(path, raw=raw)
        require(all(callable(getattr(installed, name, None)) for name in
                    ("owner_root", "release_descriptor", "read_descriptor")), "trusted_descriptor_consumer_unavailable")
        require(installed.owner_root() == root and
                installed.release_descriptor() == root / "release-descriptor.json", "owner_root_changed")
        return installed, root, raw
    except (OSError, ValueError, TypeError, AttributeError, SyntaxError, ImportError, KeyError):
        raise ReleaseError("trusted_descriptor_consumer_unavailable") from None


def pinned_file(pin):
    raw = bounded_read(pin["path"])
    require(sha(raw) == pin["sha256"], "input_digest_changed")
    return raw


def dotenv(raw):
    result = {}
    for line in raw.decode("utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, separator, value = line.partition("=")
        require(separator and re.fullmatch(r"[A-Z][A-Z0-9_]*", name), "invalid_env_assignment")
        require(name not in result and "\x00" not in value, "duplicate_or_invalid_env_assignment")
        # Match the existing control scripts: raw values, never shell expansion.
        result[name] = value
    return result


class ComposeInputLoader(yaml.SafeLoader):
    """A small, unambiguous YAML subset; no aliases, merges or custom tags."""

    def compose_node(self, parent, index):
        require(not self.check_event(yaml.AliasEvent), "compose_yaml_unsupported")
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):
        keys = [key.value for key, _ in node.value]
        require(all(key.tag == "tag:yaml.org,2002:str" for key, _ in node.value) and
                len(keys) == len(set(keys)), "compose_yaml_unsupported")
        return super().construct_mapping(node, deep=deep)


def compose_input_check(pins, runtime_pin):
    """Close file inputs before Compose can load and erase their provenance."""
    pinned_file(runtime_pin)
    runtime_path = Path(runtime_pin["path"]).resolve()
    for pin in pins:
        try:
            model = yaml.load(pinned_file(pin), Loader=ComposeInputLoader)
        except (yaml.YAMLError, RecursionError):
            raise ReleaseError("compose_yaml_unsupported") from None
        require(isinstance(model, dict) and set(model) <= {"name", "version", "services", "networks", "volumes"},
                "compose_external_input_unsupported")
        try:
            compose_interpolation_check(model)
        except ValueError as error:
            raise ReleaseError(str(error)) from None
        services = model.get("services", {})
        require(isinstance(services, dict), "compose_yaml_unsupported")
        for service in services.values():
            require(isinstance(service, dict) and set(service) <= COMPOSE_SERVICE_FIELDS | {"env_file"},
                    "compose_external_input_unsupported")
            files = service.get("env_file", [])
            files = [files] if isinstance(files, str) else files
            require(isinstance(files, list), "compose_env_file_unbound")
            for entry in files:
                if isinstance(entry, dict):
                    require(set(entry) <= {"path", "required"} and entry.get("required", True) is True,
                            "compose_env_file_unbound")
                    entry = entry.get("path")
                require(isinstance(entry, str) and bool(entry), "compose_env_file_unbound")
                if entry in ("$DIRECTOR_ENV_FILE", "${DIRECTOR_ENV_FILE}", "${DIRECTOR_ENV_FILE:-.env}"):
                    continue  # Every planning and emitted consumer sets this to the bound input.
                require("$" not in entry, "compose_env_file_unbound")
                path = Path(entry)
                if not path.is_absolute():
                    path = Path(pins[0]["path"]).parent / path
                unlinked(path)
                require(str(path.resolve()) == str(runtime_path), "compose_env_file_unbound")


def child_environment():
    # Never let an inherited COMPOSE_FILE, DOCKER_HOST, PYTHONPATH, provider key,
    # or Git override silently redirect a release check.
    env = {key: value for key, value in os.environ.items() if key.upper() in HOST_ENVIRONMENT_KEYS}
    env.update(PYTHONPATH=str(ROOT / "src"), PYTHONDONTWRITEBYTECODE="1",
               GIT_NO_REPLACE_OBJECTS="1", GIT_GRAFT_FILE=os.devnull,
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_COUNT="3", GIT_CONFIG_KEY_0="core.fsmonitor", GIT_CONFIG_VALUE_0="false",
               GIT_CONFIG_KEY_1="core.hooksPath", GIT_CONFIG_VALUE_1=os.devnull,
               GIT_CONFIG_KEY_2="core.untrackedCache", GIT_CONFIG_VALUE_2="false")
    return env


# The same small constructor runs in planning and the emitted launcher. Only the
# exact selectors copied by primary-control enter a Compose host environment;
# Docker parses every other value from the bound --env-file on both paths.
COMPOSE_ENVIRONMENT = r"""
CONTROL_SELECTORS = (
    'DIRECTOR_ASGI_APP','LANGGRAPH_PRIMARY_ACCEPTANCE_JOURNAL_PATH',
    'LANGGRAPH_PRIMARY_GENERATION_MANIFEST_PATH','LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256',
    'DIRECTOR_RUNTIME_IMAGE','DIRECTOR_RUNTIME_IMAGE_ID','DIRECTOR_RESOLVED_DEPLOYMENT_SHA256',
    'ALLOW_PAID_RUNS','LANGGRAPH_PRIMARY_ENABLED','LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED',
    'LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED','LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED',
    'LANGGRAPH_PARALLEL_KILL_SWITCH')
def compose_interpolation_check(value):
    if isinstance(value,dict):
        for key,item in value.items():
            if str(key).upper()=='PSMODULEPATH': raise ValueError('compose_startup_interpolation_unsupported')
            compose_interpolation_check(key); compose_interpolation_check(item)
    elif isinstance(value,list):
        for item in value: compose_interpolation_check(item)
    elif isinstance(value,str) and re.search(r'\$\{?PSModulePath\b',value,re.I):
        # Include escaped/default forms: PowerShell adds this machine-specific
        # variable at startup, so it cannot select any supported input path.
        raise ValueError('compose_startup_interpolation_unsupported')
def compose_environment(base,runtime):
    compose_interpolation_check(runtime)
    env=dict(base)
    for key in CONTROL_SELECTORS:
        env.pop(key,None)
        value=runtime.get(key,'')
        if not isinstance(value,str) or value and not re.fullmatch(r'[A-Za-z0-9_./:@+-]+',value):
            raise ValueError('compose_control_selector_unsupported')
        if value: env[key]=value
    return env
"""
exec(COMPOSE_ENVIRONMENT)


def command(argv, *, cwd, env, deadline, input_bytes=None, executable=None, containment=None):
    """One deadline, closed stdin, bounded output, no shell and no raw-output logging."""
    require(time.monotonic() < deadline, "doctor_deadline_exhausted")
    executable = executable or argv[0]
    require(Path(executable).is_absolute(), "untrusted_executable")
    with tempfile.TemporaryFile() as stdin, tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        if input_bytes:
            stdin.write(input_bytes)
            stdin.seek(0)
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        launch = containment[0] if containment else subprocess.Popen
        launched = launch(argv, executable=executable, cwd=cwd, env=env, stdin=stdin, stdout=stdout, stderr=stderr, **options)
        process, guard = launched if containment else (launched, None)
        try:
            while process.poll() is None:
                require(time.monotonic() < deadline, "doctor_deadline_exhausted")
                require(os.fstat(stdout.fileno()).st_size <= MAX_BYTES and
                        os.fstat(stderr.fileno()).st_size <= MAX_BYTES, "command_output_exceeded")
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
            require(process.returncode == 0, "platform_check_failed")
            require(time.monotonic() <= deadline, "doctor_deadline_exhausted")
            require(os.fstat(stderr.fileno()).st_size <= MAX_BYTES, "command_output_exceeded")
            stdout.seek(0)
            raw = stdout.read(MAX_BYTES + 1)
            require(len(raw) <= MAX_BYTES, "command_output_exceeded")
            return raw
        finally:
            if containment:
                containment[1](process, guard, grace_seconds=5)
            elif process.poll() is None:
                try:
                    if os.name == "nt":
                        import ctypes
                        directory = ctypes.create_unicode_buffer(32768)
                        require(ctypes.windll.kernel32.GetSystemDirectoryW(directory, len(directory)), "system_directory_unavailable")
                        subprocess.run([str(Path(directory.value) / "taskkill.exe"), "/PID", str(process.pid), "/T", "/F"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=5)


BRIDGE_PROBE = """
import json,ssl,sys,urllib.request
v=json.load(sys.stdin)
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None
context=ssl.create_default_context(cafile=v['ca'])
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect,
                                 urllib.request.HTTPSHandler(context=context))
request=urllib.request.Request(v['url'],headers={'Authorization':'Bearer '+v['token']})
with opener.open(request,timeout=v['timeout']) as response:
    raw=response.read(65537)
if len(raw)>65536: raise ValueError('oversized bridge reply')
sys.stdout.buffer.write(raw)
"""

DATABASE_PROBE = """
import json,sys
from director_orchestrator.langgraph_postgres import database_binding
from director_orchestrator.provider_validation import PostgresValidationStore,SCHEMA
v=json.load(sys.stdin)
binding=database_binding(v['dsn'],schema=v['schema'],allow_insecure_local_transport=False)
with PostgresValidationStore(v['validation_dsn']).connect() as db:
    db.execute('SET TRANSACTION READ ONLY')
    db.execute("SET LOCAL statement_timeout = '5s'")
    rows=db.execute(f'SELECT provider,state FROM {SCHEMA}.attempts WHERE generation=%s',
                    (v['generation'],)).fetchall()
print(json.dumps({'database_identity':binding[0],'database_instance_id':binding[1],
                  'occupied_slots':[row[0] for row in rows]}))
"""

LOCAL_DATABASE_PROBE = """
import json
from director_orchestrator.config import Settings
from director_orchestrator.langgraph_postgres import database_binding
s=Settings()
b=database_binding(s.langgraph_primary_postgres_dsn,schema=s.langgraph_primary_postgres_schema,
                   allow_insecure_local_transport=s.langgraph_primary_allow_insecure_local_postgres,bootstrap=False)
print(json.dumps({'database_identity':b[0],'database_instance_id':b[1]}))
"""

VALIDATION_PROBE = """
import json,sys
from director_orchestrator.provider_validation import PostgresValidationStore,SCHEMA
v=json.load(sys.stdin)
with PostgresValidationStore(v['validation_dsn']).connect() as db:
    db.execute('SET TRANSACTION READ ONLY')
    db.execute("SET LOCAL statement_timeout = '5s'")
    rows=db.execute(f'SELECT provider FROM {SCHEMA}.attempts WHERE generation=%s',(v['generation'],)).fetchall()
print(json.dumps({'occupied_slots':[row[0] for row in rows]}))
"""

# Only these successful existing control actions intentionally edit the profile.
# Every other value remains part of the per-step semantic digest after that edit.
RUNTIME_CHANGES = {
    "StageRuntime": {"DIRECTOR_ASGI_APP": "director_orchestrator.primary_staging:app", "ALLOW_PAID_RUNS": "false",
        "LANGGRAPH_PRIMARY_ENABLED": "false", "LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED": "false",
        "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED": "false", "LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED": "false",
        "LANGGRAPH_PARALLEL_KILL_SWITCH": "true"},
    "ActivateOffline": {"DIRECTOR_ASGI_APP": "director_orchestrator.main:app", "ALLOW_PAID_RUNS": "false",
        "LANGGRAPH_PRIMARY_ENABLED": "true", "LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED": "true",
        "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED": "false", "LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED": "true",
        "LANGGRAPH_PARALLEL_KILL_SWITCH": "true"},
    "Rollback": {"DIRECTOR_ASGI_APP": "director_orchestrator.main:app", "ALLOW_PAID_RUNS": "false",
        "LANGGRAPH_PRIMARY_ENABLED": "false", "LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED": "false",
        "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED": "false", "LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED": "false",
        "LANGGRAPH_PARALLEL_KILL_SWITCH": "true"},
}

# One fixed helper is shared by planning and the emitted launcher so their
# inventory digests have the same meaning. Only keyed Slack identity digests are
# retained; raw container environment values stay inside the inspection call.
RESOURCE_INVENTORY = r"""
SLACK_IDENTITY_KEYS=('SLACK_APP_TOKEN','SLACK_BOT_TOKEN','SLACK_DIRECTOR_CHANNEL_ID')
def inspected_environment(config):
    entries=config.get('Env')
    if not isinstance(entries,list): raise ValueError('inspected_environment_unavailable')
    values={}
    for entry in entries:
        if not isinstance(entry,str) or '=' not in entry: raise ValueError('inspected_environment_unavailable')
        key,value=entry.split('=',1)
        if not key or key in values: raise ValueError('inspected_environment_unavailable')
        values[key]=value
    return values
def slack_identity_digest(key,name,value):
    return hmac.new(bytes.fromhex(key),(name+'\0'+value).encode(),hashlib.sha256).hexdigest()
def volume_identity(row):
    return {key:row.get(key) for key in ('Name','Driver','Options','Labels','Mountpoint','CreatedAt','Scope')}
def resource_inventory(read,project,identity_key):
    ids=sorted(read(['docker','ps','-a','--no-trunc','--filter','label=com.docker.compose.project='+project,'--format','{{.ID}}']).decode().splitlines())
    result={'containers':ids,'binds':[],'volumes':[],'networks':[],'ports':[]}
    identities={name:[] for name in SLACK_IDENTITY_KEYS}
    result['slack_identities']=identities
    if not ids: return result
    rows=json.loads(read(['docker','inspect',*ids]))
    if not isinstance(rows,list) or len(rows)!=len(ids) or len(ids)!=len(set(ids)) or any(not isinstance(row,dict) for row in rows):
        raise ValueError('final resource inventory unavailable')
    if {row.get('Id') for row in rows}!=set(ids) or any(not isinstance(row.get('Config'),dict) or
            row['Config'].get('Labels',{}).get('com.docker.compose.project')!=project for row in rows):
        raise ValueError('final resource inventory unavailable')
    for row in rows:
        values=inspected_environment(row['Config'])
        for name in SLACK_IDENTITY_KEYS:
            if values.get(name): identities[name].append(slack_identity_digest(identity_key,name,values[name]))
        for mount in row['Mounts']:
            if mount['Type']=='bind': result['binds'].append(mount['Source'])
            elif mount['Type']=='volume': result['volumes'].append(mount['Name'])
        result['networks'].extend(row['NetworkSettings']['Networks'])
        for bindings in (row['HostConfig'].get('PortBindings') or {}).values():
            result['ports'].extend(item['HostPort'] for item in (bindings or []))
    return {key:({name:sorted(set(hashes)) for name,hashes in value.items()} if key=='slack_identities' else
                 sorted(set(value))) for key,value in result.items()}
"""
exec(RESOURCE_INVENTORY)

# Shared by the host gate and emitted launcher. Git hashes actual files in one
# batch with all conversions disabled; only a narrowly checked CRLF difference
# may be normalized locally. No index stat-cache entry authenticates file bytes.
SOURCE_PROOF = r"""
def source_proof(read,root,tree):
    root=pathlib.Path(root)
    index=read(['git','ls-files','-v','-z']).split(b'\0')
    if any(row and not row.startswith(b'H ') for row in index):
        raise ValueError('source_hidden_index_flags')
    files={}
    for row in read(['git','ls-tree','-r','-z',tree]).split(b'\0'):
        if not row: continue
        meta,name=row.split(b'\t',1)
        mode,kind,oid=meta.decode().split()
        name=name.decode('utf-8')
        if mode not in ('100644','100755') or kind!='blob' or pathlib.PurePosixPath(name).is_absolute() or '..' in pathlib.PurePosixPath(name).parts:
            raise ValueError('source_file_type_unsupported')
        path=root/name
        for part in (path,*path.parents):
            info=part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0):
                raise ValueError('source_link_refused')
        files[name]=oid
    if files:
        names=list(files)
        attrs=read(['git','check-attr','-z','--stdin','text','eol','ident','working-tree-encoding','filter'],
                   input=('\0'.join(names)+'\0').encode()).decode().split('\0')[:-1]
        attributes={name:{} for name in names}
        if len(attrs)!=len(names)*15: raise ValueError('source_attributes_unavailable')
        for pos in range(0,len(attrs),3): attributes[attrs[pos]][attrs[pos+1]]=attrs[pos+2]
        hashes=read(['git','hash-object','--no-filters','--stdin-paths'],
                    input=('\n'.join(json.dumps(name,ensure_ascii=False) for name in names)+'\n').encode()).decode().splitlines()
        if len(hashes)!=len(names): raise ValueError('source_hashes_unavailable')
        for name,actual in zip(names,hashes):
            attr=attributes[name]
            if any(attr[key] not in ('unset','unspecified') for key in ('ident','working-tree-encoding','filter')):
                raise ValueError('source_transform_unsupported')
            if actual==files[name]: continue
            with (root/name).open('rb') as stream: raw=stream.read(4*1024*1024+1)
            if len(raw)>4*1024*1024 or b'\0' in raw or attr['text']=='unset' or attr['eol'] not in ('unspecified','lf','crlf'):
                raise ValueError('source_bytes_changed')
            raw.decode('utf-8')
            normalized=raw.replace(b'\r\n',b'\n')
            oid=hashlib.sha1(b'blob '+str(len(normalized)).encode()+b'\0'+normalized).hexdigest()
            if oid!=files[name]: raise ValueError('source_bytes_changed')
    # -B prevents writes, not loading unchecked caches. Refuse cache/native or
    # untracked Python import artifacts only inside the two checkout import roots.
    for directory in ('src','scripts'):
        for path in (root/directory).rglob('*'):
            info=path.lstat()
            suffix=path.suffix.lower()
            if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0):
                raise ValueError('source_import_artifact_refused')
            if suffix in ('.pyc','.pyo','.pyd','.so') or (suffix in ('.py','.pyw') and path.relative_to(root).as_posix() not in files):
                raise ValueError('source_import_artifact_refused')
"""
exec(SOURCE_PROOF)

# The emitted argv executes this fixed launcher, so environment replacement is
# performed by a real consumer rather than depending on JSON overlay semantics.
REHEARSAL_LAUNCH = r"""
import base64,hashlib,hmac,json,os,pathlib,re,socket,stat,subprocess,sys,tempfile,time,uuid
v=json.loads(base64.b64decode(sys.argv[1]))
env=v['environment']
def read(args,**kwargs):
    return subprocess.check_output(args,executable=v['executables'].get(args[0],args[0]),env=env,cwd=v['cwd'],timeout=30,**kwargs)
def digest(raw): return hashlib.sha256(raw).hexdigest()
def encoded(value): return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def file_bytes(name):
    path=pathlib.Path(name)
    if not path.is_absolute(): raise SystemExit('input path changed')
    for part in (path,*path.parents):
        metadata=part.lstat()
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0):
            raise SystemExit('linked input refused')
    if not path.is_file(): raise SystemExit('regular input required')
    with path.open('rb') as stream: raw=stream.read(4*1024*1024+1)
    if len(raw)>4*1024*1024: raise SystemExit('input too large')
    return raw
def dotenv(raw):
    result={}
    for line in raw.decode('utf-8-sig').splitlines():
        if not line.strip() or line.lstrip().startswith('#'): continue
        name,sep,value=line.partition('=')
        if not sep or not re.fullmatch(r'[A-Z][A-Z0-9_]*',name) or name in result or chr(0) in value:
            raise SystemExit('invalid runtime profile')
        result[name]=value
    return result
def input_check():
    global env
    if set(v['executables'])!={'python','powershell','git','docker','gh'}: raise SystemExit('trusted executable changed')
    for path in v['executables'].values():
        p=pathlib.Path(path)
        if not p.is_absolute() or not p.is_file() or any(p.resolve().is_relative_to(pathlib.Path(root).resolve())
                for root in (v['cwd'],v['snapshot_path'])):
            raise SystemExit('trusted executable changed')
        for part in (p,*p.parents):
            info=part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0):
                raise SystemExit('trusted executable changed')
    for pin in v['pins']:
        if digest(file_bytes(pin['path']))!=pin['sha256']: raise SystemExit('pinned input changed')
    pin=v['runtime_pin']
    raw=file_bytes(pin['path'])
    if 'sha256' in pin:
        if digest(raw)!=pin['sha256']: raise SystemExit('initial runtime profile changed')
    elif digest(encoded(dotenv(raw)))!=pin['values_sha256']:
        raise SystemExit('runtime profile differs from expected control transition')
    try: env=compose_environment(v['environment'],dotenv(raw))
    except ValueError as error: raise SystemExit(str(error)) from None

def offline_attempt_check():
    if (v['first_step'] or v['action']=='ActivateOffline') and os.path.lexists(v['prior_images'].replace('.prior-images.','.offline-attempt.')):
        raise SystemExit('stale offline attempt requires reconciliation')
def probe_read(args,deadline):
    # Reserve the last five seconds for verified host-client tree cleanup.
    if time.monotonic()>=deadline-5: raise RuntimeError('probe deadline')
    with tempfile.TemporaryFile() as out,tempfile.TemporaryFile() as err:
        p,g=launch_contained_process(args,executable=v['executables'].get(args[0],args[0]),env=env,cwd=v['cwd'],
            stdin=subprocess.DEVNULL,stdout=out,stderr=err)
        try:
            while p.poll() is None:
                if time.monotonic()>=deadline-5 or max(os.fstat(out.fileno()).st_size,os.fstat(err.fileno()).st_size)>4*1024*1024:
                    raise RuntimeError('probe deadline or output limit')
                time.sleep(.02)
            if p.returncode or time.monotonic()>=deadline-5 or max(os.fstat(out.fileno()).st_size,os.fstat(err.fileno()).st_size)>4*1024*1024:
                raise RuntimeError('probe client failed')
            out.seek(0)
            return out.read(4*1024*1024+1)
        finally: stop_contained_process(p,g,grace_seconds=max(0,min(5,deadline-time.monotonic())))
def owned_database_probe(db):
    deadline=time.monotonic()+30; work=deadline-10
    owner=uuid.uuid4().hex; name=v['project']+'-probe-'+owner; selected=None; failed=False
    print('database probe name='+name+' owner='+owner,file=sys.stderr,flush=True)
    query=['docker','ps','-a','--no-trunc','--filter','name=^/'+name+'$','--format','{{.ID}}']
    def identity(container,limit):
        if not re.fullmatch('[0-9a-f]{64}',container) or selected and container!=selected: raise RuntimeError('probe identity')
        rows=json.loads(probe_read(['docker','inspect',container],limit))
        if len(rows)!=1: raise RuntimeError('probe identity')
        row=rows[0]; labels=row.get('Config',{}).get('Labels',{})
        if row.get('Id')!=container or row.get('Name')!='/'+name or row.get('Image')!=v['runtime_image'] or any(labels.get(k)!=value for k,value in
            {'org.director.release.probe':owner,'com.docker.compose.project':v['project'],'com.docker.compose.service':'orchestrator','com.docker.compose.oneoff':'True'}.items()):
            raise RuntimeError('probe identity')
        return row
    try:
        if probe_read(query,work).strip(): raise RuntimeError('probe name occupied')
        selected=probe_read([*db['compose'],'run','--detach','--no-deps','--pull','never','-T','--name',name,
            '--label','org.director.release.probe='+owner,'--entrypoint','python','orchestrator','-c',LOCAL_DATABASE_PROBE],work).decode().strip()
        if identity(selected,work).get('HostConfig',{}).get('RestartPolicy',{}).get('Name') not in ('','no'):
            raise RuntimeError('probe restart policy')
        if probe_read(['docker','wait',selected],work).strip()!=b'0': raise RuntimeError('probe exit')
        raw=probe_read(['docker','logs','--tail','100',selected],work)
    except BaseException: failed=True
    finally:
        try:
            ids=probe_read(query,deadline).decode().splitlines()
            if ids:
                if len(ids)!=1: raise RuntimeError('probe identity')
                identity(ids[0],deadline)
                probe_read(['docker','rm','--force',ids[0]],deadline)
            if probe_read(query,deadline).strip(): raise RuntimeError('probe remains')
        except BaseException: raise SystemExit('database probe cleanup unverified; reconcile name='+name+' owner='+owner) from None
    if failed: raise SystemExit('database probe failed; reconcile name='+name+' owner='+owner) from None
    return raw
def database_check():
    db=v.get('local_database')
    if not db: return
    actual=json.loads(owned_database_probe(db))
    if any(actual.get(key)!=db['expected'][key] or actual.get(key)==db['final'][key]
           for key in ('database_identity','database_instance_id')):
        raise SystemExit('sibling database identity not isolated')
    profile=dotenv(file_bytes(db['bridge_path']))
    slots=json.loads(read([sys.executable,'-B','-c',VALIDATION_PROBE],input=encoded({
        'validation_dsn':profile['DIRECTOR_PROVIDER_VALIDATION_POSTGRES_DSN'],'generation':db['generation']})))
    if slots.get('occupied_slots')!=[]: raise SystemExit('validation slot already occupied')
receipt_digest=None
def intake_identity(name,key,reader):
    rows=json.loads(reader(['docker','volume','inspect',name]))
    if len(rows)!=1: raise ValueError('intake volume identity')
    row=rows[0]; binding=v['intake']['binding']
    labels={'com.docker.compose.project':binding['project'],'com.docker.compose.volume':key,
        'org.director.release.intake':binding['nonce']}
    if row.get('Name')!=name or row.get('Driver')!='local' or row.get('Options') or row.get('Scope')!='local' or any(
        row.get('Labels',{}).get(k)!=value for k,value in labels.items()) or not all(
        isinstance(row.get(k),str) and row[k] for k in ('Mountpoint','CreatedAt')):
        raise ValueError('intake volume identity')
    return digest(encoded(volume_identity(row)))

def intake_check():
    global receipt_digest
    if v['action']=='InitializeIntakeVolumes': return
    try:
        raw=file_bytes(v['intake']['path']); rows=[json.loads(line) for line in raw.splitlines()]
        binding=v['intake']['binding']
        if not raw.endswith(b'\n') or len(rows)!=2 or rows[0]!={'event':'intent','binding':binding} or set(rows[1])!={'event','volumes'} or rows[1]['event']!='complete' or set(rows[1]['volumes'])!=set(binding['names']):
            raise ValueError('intake receipt')
        if receipt_digest is not None and digest(raw)!=receipt_digest: raise ValueError('intake receipt changed')
        for name,key in binding['names'].items():
            if intake_identity(name,key,read)!=rows[1]['volumes'][name]: raise ValueError('intake volume changed')
        receipt_digest=digest(raw)
    except Exception: raise SystemExit('intake volume receipt or identity changed; reconciliation required') from None

def initialize_intake_volumes():
    path=pathlib.Path(v['intake']['path']); binding=v['intake']['binding']; deadline=time.monotonic()+35; name='unstarted'
    try:
        if not path.is_absolute(): raise ValueError('intake receipt path')
        for parent in path.parents:
            info=parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0):
                raise ValueError('linked receipt parent')
        # The exclusive file remains open through both flushed records. Partial
        # creation leaves the intent in place, without replay or adoption.
        with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as stream:
            intent=encoded({'event':'intent','binding':binding})+b'\n'
            stream.write(intent); stream.flush(); os.fsync(stream.fileno())
            identities={}
            for command in v['initialize_argvs']:
                name=command[-1]; key=binding['names'][name]
                if name in probe_read(['docker','volume','ls','--format','{{.Name}}'],deadline).decode().splitlines():
                    raise ValueError('intake name occupied')
                if probe_read(command,deadline).decode().strip()!=name: raise ValueError('intake create result')
                identities[name]=intake_identity(name,key,lambda args:probe_read(args,deadline))
            current=path.lstat(); owned=os.fstat(stream.fileno())
            if (current.st_dev,current.st_ino)!=(owned.st_dev,owned.st_ino) or not stat.S_ISREG(current.st_mode):
                raise ValueError('intake receipt changed')
            complete=encoded({'event':'complete','volumes':identities})+b'\n'
            stream.write(complete); stream.flush(); os.fsync(stream.fileno())
        if file_bytes(str(path))!=intent+complete: raise ValueError('intake receipt readback')
    except Exception: raise SystemExit('intake volume initialization uncertain; reconcile receipt='+str(path)+' nonce='+binding['nonce']+' name='+name) from None

def source_check():
    names=read(['git','config','--null','--name-only','--list']).decode().split(chr(0))
    if any(re.match(r'filter\..*\.(clean|smudge|process)$',name,re.I) for name in names):
        raise SystemExit('executable Git filter refused')
    for kind in ('commit','tree'):
        if read(['git','rev-parse','--verify','HEAD^{'+kind+'}']).decode().strip()!=v['candidate'][kind]:
            raise SystemExit('verified source identity changed')
    if (pathlib.Path(v['cwd'])/'.gitmodules').exists():
        raise SystemExit('source submodules unsupported')
    try: source_proof(read,v['cwd'],v['candidate']['tree'])
    except (ValueError,OSError,KeyError): raise SystemExit('verified source bytes or import artifacts changed') from None
    if read(['git','status','--porcelain=v2','--untracked-files=all']).strip():
        raise SystemExit('verified source no longer clean')
input_check()
source_check()
offline_attempt_check()
sys.path.insert(0,str(pathlib.Path(v['cwd'])/'src'))
from director_orchestrator.process_isolation import launch_contained_process,stop_contained_process
if read(['docker','info','--format','{{.ID}}']).decode().strip()!=v['daemon_id']:
    raise SystemExit('verified Docker daemon changed')
try: final_resources=resource_inventory(read,v['final_project'],v['slack_identity_key'])
except (ValueError,KeyError,TypeError,AttributeError): raise SystemExit('final project resources changed') from None
if digest(encoded(final_resources))!=v['final_resources_sha256']:
    raise SystemExit('final project resources changed')
for reference,expected in v['images'].items():
    actual=json.loads(read(['docker','image','inspect',reference]))
    if len(actual)!=1 or actual[0]['Id']!=expected:
        raise SystemExit('verified cached image changed')
for name,expected in v['state_volumes'].items():
    rows=json.loads(read(['docker','volume','inspect',name]))
    if len(rows)!=1 or digest(encoded(volume_identity(rows[0])))!=expected:
        raise SystemExit('verified state volume changed')
if v['first_step']:
    if read(['docker','ps','-a','--filter','label=com.docker.compose.project='+v['project'],'--format','{{.ID}}']).strip():
        raise SystemExit('sibling project no longer fresh')
    names=set(read(['docker','volume','ls','--format','{{.Name}}']).decode().splitlines())
    if names.intersection(v['fresh_volumes']):
        raise SystemExit('n8n state no longer fresh')
    networks=set(read(['docker','network','ls','--format','{{.Name}}']).decode().splitlines())
    if networks.intersection(v['fresh_networks']):
        raise SystemExit('sibling network no longer fresh')
prior=pathlib.Path(v['prior_images'])
if prior.exists():
    saved=json.loads(prior.read_text(encoding='utf-8-sig'))
    if any(saved.get(key)!=v['runtime_image'] for key in ('orchestrator_image_id','gateway_image_id')):
        raise SystemExit('rollback image outside verified rehearsal')
intake_check()
if v['action'] not in ('InitializeIntakeVolumes','StartDatabase','Stop'): database_check()
input_check()
source_check()
offline_attempt_check()
intake_check()
if v['first_step'] or v['action']=='StageRuntime':
    for port in v['ports']:
        kind=socket.SOCK_STREAM if port['protocol']=='tcp' else socket.SOCK_DGRAM
        with socket.socket(socket.AF_INET,kind) as sock:
            if hasattr(socket,'SO_EXCLUSIVEADDRUSE'): sock.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            try: sock.bind((port['host_ip'],int(port['published'])))
            except OSError: raise SystemExit('sibling port is occupied') from None
if v['action']=='InitializeIntakeVolumes': initialize_intake_volumes()
else:
    process,guard=launch_contained_process(v['control_argv'],env=env,cwd=v['cwd'],stdin=subprocess.DEVNULL,
        executable=v['executables'].get(v['control_argv'][0],v['control_argv'][0]))
    try:
        code=process.wait(timeout=v['control_timeout_seconds'])
        if code: raise subprocess.CalledProcessError(code,v['control_argv'])
    finally: stop_contained_process(process,guard,grace_seconds=5)
if v['action']=='StartDatabase':
    input_check()
    database_check()
if v['action'] in ('ActivateOffline','Rollback'):
    ids=read(['docker','ps','-a','--no-trunc','--filter','label=com.docker.compose.project='+v['project'],
        '--filter','label=com.docker.compose.service=slackbot','--filter','label=com.docker.compose.oneoff=False','--format','{{.ID}}']).decode().splitlines()
    if len(ids)!=1: raise SystemExit('sibling gateway not proved running')
    rows=json.loads(read(['docker','inspect',ids[0]]))
    if len(rows)!=1: raise SystemExit('sibling gateway not proved running')
    row=rows[0]; labels=row.get('Config',{}).get('Labels',{}); state=row.get('State',{})
    if row.get('Image')!=v['runtime_image'] or labels.get('com.docker.compose.project')!=v['project'] or labels.get('com.docker.compose.service')!='slackbot' or labels.get('com.docker.compose.oneoff')!='False' or not state.get('Running') or state.get('Paused') or state.get('Restarting'):
        raise SystemExit('sibling gateway not proved running')
if v['action']=='Stop':
    ids=read(['docker','ps','-a','--filter','label=com.docker.compose.project='+v['project'],'--format','{{.ID}}']).decode().splitlines()
    rows=json.loads(read(['docker','inspect',*ids])) if ids else []
    if len(rows)!=len(ids): raise SystemExit('sibling intake not proved stopped')
    for row in rows:
        labels=row.get('Config',{}).get('Labels',{})
        if labels.get('com.docker.compose.project')!=v['project']: raise SystemExit('sibling intake not proved stopped')
        if labels.get('com.docker.compose.service') not in ('langgraph-primary-db','n8n-db'):
            state=row.get('State',{})
            if state.get('Running') or state.get('Paused') or state.get('Restarting') or state.get('Status') not in ('created','exited','dead'):
                raise SystemExit('sibling intake not proved stopped')
"""
REHEARSAL_LAUNCH = ("LOCAL_DATABASE_PROBE=" + repr(LOCAL_DATABASE_PROBE) + "\nVALIDATION_PROBE=" +
                   repr(VALIDATION_PROBE) + "\n" + COMPOSE_ENVIRONMENT + RESOURCE_INVENTORY + SOURCE_PROOF + REHEARSAL_LAUNCH)
REHEARSAL_BOOTSTRAP = ("import base64,zlib;exec(zlib.decompress(base64.b64decode(" +
                       repr(base64.b64encode(zlib.compress(REHEARSAL_LAUNCH.encode())).decode("ascii")) + ")))")


class Release:
    def __init__(self, path):
        self.path = Path(path)
        self.raw = bounded_read(self.path)
        self.spec = document(self.raw)
        require(isinstance(self.spec, dict) and isinstance(self.spec.get("release_id"), str), "invalid_release_descriptor")
        self.descriptor_digest = sha(self.raw)
        self.owner_descriptor_digest = None
        self.owner_launcher, self.owner_root, self.owner_launcher_raw = installed_owner_launcher()
        self.owner_launcher_digest = sha(self.owner_launcher_raw)
        self.owner_check()  # Authenticate the exact input before trusting its candidate.
        self.source = Path(self.spec["source"])
        require(self.source.is_absolute() and self.source.resolve() == ROOT.resolve(), "wrong_runner_checkout")
        unlinked(self.source)
        timeout = self.spec.get("timeout_seconds")
        require(type(timeout) is int and 1 <= timeout <= 900, "invalid_release_descriptor")
        self.deadline = time.monotonic() + timeout
        self.executables = self.spec.get("executables", {})
        require(isinstance(self.executables, dict) and set(self.executables) == {"python", "powershell", "git", "docker", "gh"} and
                all(isinstance(value, str) and value for value in self.executables.values()), "trusted_executables_required")
        for path in self.executables.values():
            unlinked(Path(path))
            require(Path(path).is_file() and not paths_overlap(path, self.source) and
                    not paths_overlap(path, self.spec["snapshot"]["path"]), "trusted_executable_path_invalid")
        self.host_environment = child_environment()
        # PowerShell prepends its own directory when absent. Normalize it once
        # for direct Docker and control children alike; no profile PATH is used.
        path_key = next((key for key in self.host_environment if key.upper() == "PATH"), "PATH")
        directories = self.host_environment.get(path_key, "").split(os.pathsep)
        powershell_directory = str(Path(self.executables["powershell"]).parent)
        self.host_environment[path_key] = os.pathsep.join([powershell_directory, *[
            item for item in directories if item and os.path.normcase(item) != os.path.normcase(powershell_directory)]])
        # No checkout locator runs. Candidate helpers are imported only after
        # the installed reader has bound this exact input and Git has verified it.
        self.source_check()
        # -I intentionally ignores PYTHONPATH; only the now-authenticated source
        # may be added for the existing candidate helper imports.
        if str(self.source / "src") not in sys.path:
            sys.path.insert(0, str(self.source / "src"))
        from director_orchestrator.process_isolation import launch_contained_process, stop_contained_process
        self.containment = (launch_contained_process, stop_contained_process)
        validator = Draft202012Validator(document(bounded_read(SCHEMA)))
        require(not list(validator.iter_errors(self.spec)), "invalid_release_descriptor")
        self.output = Path(self.spec["output_directory"])
        self.output_check()
        for name in ("python", "powershell"):
            unlinked(Path(self.spec["executables"][name]))
            require(Path(self.spec["executables"][name]).is_absolute() and
                    Path(self.spec["executables"][name]).is_file(), "executable_unavailable")
        self.runtime = dotenv(pinned_file(self.spec["runtime_env"]))
        try:
            compose_environment(self.host_environment, self.runtime)
        except ValueError as error:
            raise ReleaseError(str(error)) from None
        self.bridge = dotenv(pinned_file(self.spec["bridge_env"]))
        self.manifest = document(pinned_file(self.spec["generation_manifest"]))
        self.validation = document(pinned_file(self.spec["validation_manifest"]))
        pinned_file(self.spec["bridge_ca"])
        compose_input_check(self.spec["compose_files"], self.spec["runtime_env"])
        self.rendered = None
        self.observed_database = None
        self.docker_environment = None
        self.daemon_id = None

    def output_check(self):
        unlinked(self.output)
        require(self.output.is_dir(), "output_directory_unavailable")
        require(not paths_overlap(self.output, self.source), "output_must_be_outside_checkout")
        snapshot = Path(self.spec["snapshot"]["path"])
        unlinked(snapshot)
        require(not paths_overlap(self.output, snapshot), "output_overlaps_snapshot")

    def run(self, arguments, *, input_bytes=None, env=None):
        if arguments[0] == "docker" and getattr(self, "docker_environment", None):
            env = dict(env or self.host_environment)
            env = {key: value for key, value in env.items() if not key.upper().startswith("DOCKER_")}
            env.update(self.docker_environment)
        return command(arguments, cwd=self.source, env=env or self.host_environment,
                       deadline=self.deadline, input_bytes=input_bytes,
                       executable=self.executables.get(arguments[0], arguments[0]), containment=getattr(self, "containment", None))

    def powershell(self, name, arguments):
        self.owner_check()
        self.source_check()
        return self.run([self.spec["executables"]["powershell"], "-NoProfile", "-NonInteractive", "-File",
                         str(self.source / "scripts" / name), *arguments,
                         *(["-GitExecutable", self.executables["git"]] if name == "windows-primary-snapshot.ps1" else [])])

    def source_check(self):
        # Reading config names does not run hooks. Refuse custom executable
        # filters before status can consult tracked .gitattributes.
        names = self.run(["git", "config", "--null", "--name-only", "--list"]).decode().split("\0")
        require(not any(re.match(r"filter\..*\.(clean|smudge|process)$", name, re.I) for name in names),
                "executable_git_filter_refused")
        expected = self.spec["candidate"]
        for suffix, wanted in (("commit", expected["commit"]), ("tree", expected["tree"])):
            require(self.run(["git", "rev-parse", "--verify", "HEAD^{" + suffix + "}"]).decode().strip() == wanted,
                    "source_identity_changed")
        # Submodule status can invoke commands under a different local config.
        # This release checkout does not support submodules; do not hide their
        # dirtiness with --ignore-submodules.
        require(not (self.source / ".gitmodules").exists(), "source_submodules_unsupported")
        try:
            source_proof(lambda args, **kw: self.run(args, input_bytes=kw.get("input")), self.source, expected["tree"])
        except (ValueError, OSError, KeyError) as error:
            raise ReleaseError(str(error) if isinstance(error, ValueError) and str(error).startswith("source_")
                               else "source_bytes_unavailable") from None
        require(not self.run(["git", "status", "--porcelain=v2", "--untracked-files=all"]).strip(), "source_not_clean")

    def image_check(self):
        if not self.docker_environment:
            contexts = document(self.run(["docker", "context", "inspect"]))
            host = contexts[0]["Endpoints"]["docker"]["Host"]
            require(isinstance(host, str) and (host.startswith("npipe:////./pipe/") or host.startswith("unix:///")),
                    "docker_target_not_local")
            self.docker_environment = {"DOCKER_HOST": host}
            self.daemon_id = self.run(["docker", "info", "--format", "{{.ID}}"]).decode().strip()
            require(bool(re.fullmatch(r"[A-Za-z0-9:-]+", self.daemon_id)), "docker_daemon_identity_missing")
        images = document(self.run(["docker", "image", "inspect", self.spec["compose_project"] + "-orchestrator"]))
        require(isinstance(images, list) and len(images) == 1, "image_unavailable")
        image = images[0]
        labels = image["Config"]["Labels"]
        require(image["Id"] == self.spec["image_id"] and
                labels.get("org.director.candidate.commit") == self.spec["candidate"]["commit"] and
                labels.get("org.director.candidate.tree") == self.spec["candidate"]["tree"] and
                image["Config"].get("Entrypoint") in (None, []), "image_identity_mismatch")

    def selectors_check(self):
        self.owner_check()
        self.source_check()
        from director_orchestrator.primary_activation_evidence import ActivationIdentity
        activation = ActivationIdentity(**self.manifest["activation"])
        candidate, pin = self.spec["candidate"], self.spec["generation_manifest"]["sha256"]
        require(activation.candidate_commit == candidate["commit"] and activation.candidate_tree == candidate["tree"]
                and activation.compose_project == self.spec["compose_project"], "generation_identity_mismatch")
        require(self.runtime.get("LANGGRAPH_PRIMARY_PINNED_COMMIT_SHA") == candidate["commit"] and
                self.runtime.get("LANGGRAPH_PRIMARY_PINNED_TREE_SHA") == candidate["tree"], "runtime_candidate_mismatch")
        require(self.bridge.get("DIRECTOR_PROVIDER_VALIDATION_CANDIDATE") == candidate["commit"] and
                self.bridge.get("DIRECTOR_PROVIDER_VALIDATION_TREE") == candidate["tree"], "bridge_candidate_mismatch")
        for values in (self.runtime, self.bridge):
            require(values.get("LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256") == pin and
                    values.get("DIRECTOR_PROVIDER_VALIDATION_GENERATION", pin) in ("", pin), "generation_selector_mismatch")
        require(self.runtime.get("DIRECTOR_RUNTIME_IMAGE") == self.spec["image_id"] and
                self.manifest.get("image_id") == self.spec["image_id"], "runtime_image_mismatch")
        snapshot = self.spec["snapshot"]
        commit = self.runtime.get("LANGGRAPH_PRIMARY_PROJECT_EXECUTION_PINNED_COMMIT_SHA", "")
        tree = self.runtime.get("LANGGRAPH_PRIMARY_PROJECT_EXECUTION_PINNED_TREE_SHA", "")
        require(not (commit or tree) or bool(re.fullmatch(r"[0-9a-f]{40}", commit) and
                                           re.fullmatch(r"[0-9a-f]{40}", tree)), "runtime_snapshot_authority_invalid")
        require(snapshot["repository"] == self.runtime.get("REPOSITORY_ALLOWLIST") and
                snapshot["starting_ref"] == self.runtime.get("ALLOWED_STARTING_REFS") and
                snapshot["commit"] == (commit or candidate["commit"]) and
                snapshot["tree"] == (tree or candidate["tree"]), "runtime_snapshot_authority_mismatch")
        acceptance_inputs(self.runtime)
        v = self.validation
        require(set(v) == {"generation", "candidate", "tree", "expires_at", "budget_units", "lanes"} and
                v["generation"] == pin and v["candidate"] == candidate["commit"] and v["tree"] == candidate["tree"],
                "validation_identity_mismatch")
        require(type(v["expires_at"]) is int and 0 < v["expires_at"] - time.time() <= 86400,
                "validation_expired_or_over_24_hours")
        require(isinstance(v["lanes"], dict) and bool(v["lanes"]), "validation_lanes_missing")
        for lane in v["lanes"].values():
            require(isinstance(lane, dict) and re.fullmatch(r"[0-9a-f]{64}", lane.get("configuration_digest", "")),
                    "validation_configuration_pin_invalid")
        # This is a selector comparison, not acceptance or full manifest verification.

    def compose_base_environment(self):
        env = dict(self.host_environment)
        env.update(self.docker_environment or {})
        env.update(COMPOSE_FILE=os.pathsep.join(pin["path"] for pin in self.spec["compose_files"]),
                   DIRECTOR_ENV_FILE=self.spec["runtime_env"]["path"], COMPOSE_PATH_SEPARATOR=os.pathsep,
                   DIRECTOR_COMPOSE_PROJECT_NAME=self.spec["compose_project"],
                   DIRECTOR_HOST_PORT=self.runtime.get("DIRECTOR_HOST_PORT", "8080"),
                   ALLOW_PAID_RUNS="false", LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED="false",
                   LANGGRAPH_PARALLEL_KILL_SWITCH="true")
        return env

    def compose_document(self, runtime):
        compose_input_check(self.spec["compose_files"], self.spec["runtime_env"])
        try:
            env = compose_environment(self.compose_base_environment(), runtime)
        except ValueError as error:
            raise ReleaseError(str(error)) from None
        args = ["docker", "compose", "--project-name", self.spec["compose_project"],
                "--env-file", self.spec["runtime_env"]["path"]]
        for pin in self.spec["compose_files"]:
            args.extend(["-f", pin["path"]])
        return document(self.run([*args, "config", "--format", "json"], env=env))

    def compose_check(self):
        self.owner_check()
        self.source_check()
        from director_orchestrator.primary_generation import resolved_deployment_digest
        composed = self.compose_document(self.runtime)
        require(composed.get("name") == self.spec["compose_project"], "compose_project_mismatch")
        require(resolved_deployment_digest(composed, self.spec["image_id"]) == self.manifest["resolved_compose_sha256"],
                "resolved_compose_mismatch")
        service = composed["services"]["orchestrator"]
        mounts = [item for item in service.get("volumes", []) if item.get("target") == "/repository"]
        snapshot = self.spec["snapshot"]
        require(len(mounts) == 1 and mounts[0].get("type") == "bind" and mounts[0].get("read_only") is True and
                Path(mounts[0]["source"]).resolve() == Path(snapshot["path"]).resolve() and
                Path(self.runtime.get("REPOSITORY_SNAPSHOT_HOST_PATH", "")).resolve() == Path(snapshot["path"]).resolve(),
                "snapshot_mount_mismatch")
        require(Path(snapshot["path"]).is_dir() and any(Path(snapshot["path"]).iterdir()), "snapshot_empty")
        require(composed["services"]["slackbot"].get("pull_policy") == "never", "gateway_can_pull_unreviewed_image")
        self.rendered = encoded(composed) + b"\n"

    def snapshot_check(self):
        s = self.spec["snapshot"]
        self.powershell("windows-primary-snapshot.ps1", ["-Action", "Verify", "-Source", s["path"],
            "-Target", s["path"], "-SourceRemoteName", s["remote"], "-ExpectedRepository", s["repository"],
            "-StartingRef", s["starting_ref"], "-ExpectedCommit", s["commit"], "-ExpectedTree", s["tree"]])

    def reviewers_check(self):
        for kind in ("independent", "security"):
            key = "LANGGRAPH_PRIMARY_ACTIVATION_" + kind.upper() + "_REVIEWER_ALLOWLIST_JSON"
            actors = document(self.runtime.get(key, "null").encode())
            require(isinstance(actors, list) and actors and len(set(actors)) == len(actors) and
                    all(isinstance(actor, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", actor) for actor in actors),
                    "reviewer_allowlist_invalid")
            require(self.spec["reviewers"][kind] in actors, "planned_reviewer_not_allowlisted")

    def bridge_check(self):
        from urllib.parse import urlsplit
        url = urlsplit(self.spec["bridge_url"])
        require(url.scheme == "https" and url.hostname and not url.username and not url.password and
                url.path == "/v1/provider/configuration-digest" and not url.query and not url.fragment,
                "bridge_url_invalid")
        runtime_url = urlsplit(self.runtime.get("LANGGRAPH_PRIMARY_HOST_BRIDGE_ENDPOINT", ""))
        require((url.scheme, url.netloc) == (runtime_url.scheme, runtime_url.netloc), "bridge_origin_mismatch")
        self.powershell("windows-host-provider-bridge.ps1", ["-Action", "Preflight", "-EnvironmentFile",
            self.spec["bridge_env"]["path"], "-Workspace", self.spec["snapshot"]["path"],
            "-Python", self.spec["executables"]["python"]])
        require(self.bridge.get("DIRECTOR_HOST_PROVIDER_PROCESS_ENVIRONMENT_FILE"), "bridge_environment_not_pinned")
        token = self.bridge.get("DIRECTOR_PROVIDER_VALIDATION_OWNER_TOKEN", "")
        require(len(token) >= 32, "bridge_owner_token_missing")
        request = {"url": self.spec["bridge_url"], "ca": self.spec["bridge_ca"]["path"],
                   "token": token, "timeout": max(0.01, self.deadline - time.monotonic())}
        data = document(self.run([self.spec["executables"]["python"], "-B", "-c", BRIDGE_PROBE], input_bytes=encoded(request)))
        require(data.get("ok") is True and data.get("pinned_environment") is True, "bridge_not_ready")
        self.owner_check()
        self.source_check()
        from director_orchestrator.host_provider_bridge_server import _configured_selection
        expected = _configured_selection(
            candidate_commit=self.spec["candidate"]["commit"], candidate_tree=self.spec["candidate"]["tree"],
            generation_sha256=self.spec["generation_manifest"]["sha256"],
            workspace=self.spec["snapshot"]["path"],
            validation_dsn=self.bridge.get("DIRECTOR_PROVIDER_VALIDATION_POSTGRES_DSN", ""))
        require(data.get("configured_selection") == expected, "running_bridge_selection_mismatch")
        observed = data.get("configuration_digests", {})
        require(all(observed.get(name) == spec["configuration_digest"] for name, spec in self.validation["lanes"].items()),
                "running_bridge_configuration_mismatch")

    def database_check(self):
        a = self.manifest["activation"]
        request = {"dsn": self.bridge.get("DIRECTOR_HOST_PROVIDER_POSTGRES_DSN", ""),
                   "validation_dsn": self.bridge.get("DIRECTOR_PROVIDER_VALIDATION_POSTGRES_DSN", ""),
                   "schema": a["postgres_schema"], "generation": self.spec["generation_manifest"]["sha256"]}
        data = document(self.run([self.spec["executables"]["python"], "-B", "-c", DATABASE_PROBE], input_bytes=encoded(request)))
        require(data.get("database_identity") == a["database_identity"] and
                data.get("database_instance_id") == a["database_instance_id"], "database_binding_mismatch")
        require(data.get("occupied_slots") == [], "validation_slot_already_occupied")
        self.observed_database = {key: data[key] for key in ("database_identity", "database_instance_id")}

    def live_config_check(self):
        # Existing read-only validator deliberately tests the paid target configuration
        # without constructing its activation or opening providers/control-plane stores.
        reserved = HOST_ENVIRONMENT_KEYS | {"BASH_ENV", "ENV", "GCONV_PATH", "GLIBC_TUNABLES", "LOCPATH",
            "OPENSSL_CONF", "OPENSSL_MODULES", "SSL_CERT_FILE", "SSL_CERT_DIR", "PSMODULEPATH", "PSEXECUTIONPOLICYPREFERENCE",
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "GITHUB_HOST", "GITHUB_ENTERPRISE_HOST"}
        require(not any(key.upper() in reserved or key.upper().startswith(
            ("PYTHON", "LD_", "DYLD_", "GIT_", "GH_", "DOCKER_", "COMPOSE_"))
            for key in self.runtime), "runtime_host_environment_unsupported")
        env = dict(self.host_environment)
        env.update(self.runtime)
        # Only this read-only child's hypothetical target enables the switches.
        # Neither the protected profile nor any Compose/control child receives it.
        env.update(ALLOW_PAID_RUNS="true", LANGGRAPH_PRIMARY_ENABLED="true",
                   LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED="true",
                   LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED="true",
                   LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED="false")
        self.owner_check()
        self.source_check()
        self.run([self.spec["executables"]["python"], "-B", str(self.source / "scripts/live-dispatch-preflight.py")], env=env)

    def verify_inputs(self):
        self.owner_check()
        require(bounded_read(self.path) == self.raw, "descriptor_changed")
        self.output_check()
        for name in ("runtime_env", "bridge_env", "generation_manifest", "validation_manifest", "bridge_ca"):
            pinned_file(self.spec[name])
        for pin in self.spec["compose_files"]:
            pinned_file(pin)
        if "rehearsal" in self.spec:
            for key in ("descriptor", "acceptance_artifact"):
                pinned_file(self.spec["rehearsal"][key])

    def owner_check(self):
        path = self.owner_root / "release-descriptor.json"
        require(bounded_read(self.owner_root / "launcher.py") == self.owner_launcher_raw, "owner_launcher_changed")
        require(self.owner_launcher.owner_root() == self.owner_root and
                self.owner_launcher.release_descriptor() == path, "owner_root_changed")
        unlinked(path)
        read = self.owner_launcher.read_descriptor(path)
        require(isinstance(read, tuple) and len(read) == 2 and isinstance(read[0], str) and
                re.fullmatch(r"[0-9a-f]{64}", read[0]) and isinstance(read[1], dict), "owner_descriptor_unavailable")
        digest, owner = read
        require(self.owner_descriptor_digest is None or digest == self.owner_descriptor_digest,
                "owner_descriptor_changed")
        pins = owner.get("release_preparations")
        require(isinstance(pins, dict) and all(isinstance(name, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,47}", name)
                and isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) for name, value in pins.items()),
                "owner_preparation_pins_invalid")
        require(pins.get(self.spec["release_id"]) == self.descriptor_digest, "preparation_not_owner_pinned")
        self.owner_descriptor_digest = digest

    def doctor(self, *, defer_local_database=False):
        checks = []
        for name in ("owner", "source", "image", "selectors", "compose", "snapshot", "reviewers", "bridge", "database", "live_config"):
            try:
                if name == "database" and defer_local_database and self.rendered and \
                        "langgraph-primary-db" in document(self.rendered)["services"]:
                    checks.append({"check": name, "status": "DEFERRED", "reason": "local_database_start_required"})
                    continue
                getattr(self, name + "_check")()
                checks.append({"check": name, "status": "PASS"})
            except Exception as error:
                checks.append({"check": name, "status": "FAIL", "reason": str(error) if isinstance(error, ReleaseError) else type(error).__name__})
                if name in ("owner", "source") or (isinstance(error, ReleaseError) and str(error) in {
                        "source_not_clean", "source_identity_changed", "owner_launcher_changed", "owner_descriptor_changed",
                        "owner_root_changed", "owner_descriptor_unavailable", "preparation_not_owner_pinned"}):
                    break
        if checks[0]["status"] == "PASS":
            try:
                self.verify_inputs()
            except Exception:
                checks.append({"check": "input_readback", "status": "FAIL", "reason": "input_changed_during_doctor"})
        return {"schema_version": "director-release-doctor/v1", "descriptor_sha256": self.descriptor_digest,
                "owner_descriptor_sha256": self.owner_descriptor_digest,
                "owner_launcher_sha256": self.owner_launcher_digest,
                "checks": checks, "preparation_ready": all(item["status"] == "PASS" for item in checks),
                "release_ready": False, "provider_calls": 0, "dependencies": DEPENDENCIES}

    def prepare(self, *, resume=False):
        self.owner_check()  # Refuse before even creating the local lock file.
        self.output_check()
        journal = self.output / (self.spec["release_id"] + ".jsonl")
        target = self.output / (self.spec["release_id"] + ".activation.compose.json")
        with locked(journal.with_suffix(".lock")):
            records = read_journal(journal, self.descriptor_digest, self.owner_descriptor_digest, self.owner_launcher_digest)
            if records:
                require(resume, "use_resume_for_existing_journal")
                self.verify_inputs()
                # Only a proven complete local write can be recovered. A missing,
                # partial or different artifact stays held; no effect is replayed.
                reason = "completed_output_changed" if len(records) == 2 else "reconciliation_required_no_replay"
                require(target.is_file() and sha(bounded_read(target)) == records[0]["output_sha256"], reason)
                if len(records) == 1:
                    self.complete_render(journal, records[0]["output_sha256"])
                return {"status": "PREPARED", "resumed": True, "recovered_receipt": len(records) == 1,
                        "owner_descriptor_sha256": self.owner_descriptor_digest,
                        "owner_launcher_sha256": self.owner_launcher_digest,
                        "release_ready": False, "provider_calls": 0}
            require(not target.exists(), "unrecorded_output_requires_reconciliation")
            report = self.doctor()
            require(report["preparation_ready"] and self.rendered is not None, "doctor_refused_preparation")
            self.verify_inputs()
            append_record(journal, {"event": "intent", "descriptor_sha256": self.descriptor_digest,
                                   "owner_descriptor_sha256": self.owner_descriptor_digest,
                                   "owner_launcher_sha256": self.owner_launcher_digest,
                                   "step": "render_compose", "sequence": 1, "output_sha256": sha(self.rendered)})
            try:
                with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
                    stream.write(self.rendered)
                    stream.flush()
                    os.fsync(stream.fileno())
                require(bounded_read(target) == self.rendered, "compose_output_readback_failed")
                self.complete_render(journal, sha(self.rendered))
            except BaseException:
                # A pending intent is sufficient to refuse replay even if this append fails.
                raise ReleaseError("reconciliation_required_no_replay") from None
            return {"status": "PREPARED", "resumed": False, "release_ready": False, "provider_calls": 0}

    def complete_render(self, journal, digest):
        self.verify_inputs()  # Also required when recovering a completed local write.
        append_record(journal, {"event": "receipt", "descriptor_sha256": self.descriptor_digest,
                               "owner_descriptor_sha256": self.owner_descriptor_digest,
                               "owner_launcher_sha256": self.owner_launcher_digest,
                               "step": "render_compose", "sequence": 2, "status": "COMPLETE", "output_sha256": digest})

    def rehearsal(self):
        """Return an unexecuted plan; existing control actions retain every gate."""
        require("rehearsal" in self.spec, "sibling_descriptor_required")
        self.verify_inputs()
        plan = self.spec["rehearsal"]
        sibling = Release(plan["descriptor"]["path"])
        require(sibling.owner_descriptor_digest == self.owner_descriptor_digest, "owner_descriptor_changed")
        require(sibling.owner_launcher_digest == self.owner_launcher_digest, "owner_launcher_changed")
        require("rehearsal" not in sibling.spec, "recursive_rehearsal_refused")
        require(sibling.spec["candidate"] == self.spec["candidate"] and
                sibling.spec["image_id"] == self.spec["image_id"], "sibling_candidate_mismatch")
        require(sibling.spec["compose_project"] != self.spec["compose_project"] and
                sibling.spec["release_id"] != self.spec["release_id"] and
                not paths_overlap(sibling.output, self.output), "sibling_project_not_isolated")
        for output, other in ((self.output, sibling), (sibling.output, self)):
            require(all(not paths_overlap(output, path) for path in other.protected_paths()),
                    "cross_release_output_overlap")
        for key in ("runtime_env", "bridge_env", "generation_manifest", "validation_manifest"):
            require(Path(sibling.spec[key]["path"]).resolve() != Path(self.spec[key]["path"]).resolve(),
                    "sibling_profile_not_isolated")
        for key in ("ALLOW_PAID_RUNS", "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED"):
            require(sibling.runtime.get(key) == "false", "sibling_must_be_unpaid")
        require(sibling.runtime.get("LANGGRAPH_PARALLEL_KILL_SWITCH") == "true", "sibling_must_be_unpaid")
        require(sibling.runtime.get("DIRECTOR_PROVIDER_VALIDATION_ENABLED") == "false",
                "sibling_offline_handoff_profile_invalid")
        require(sibling.runtime.get("LANGGRAPH_PRIMARY_ACCEPTANCE_MODE") in ("provider-v2", "provider-v3-two-party"),
                "sibling_offline_acceptance_mode_unsupported")
        for key in ("SLACK_APP_TOKEN", "SLACK_BOT_TOKEN", "SLACK_DIRECTOR_CHANNEL_ID"):
            require(not sibling.runtime.get(key) or sibling.runtime[key] != self.runtime.get(key),
                    "sibling_slack_identity_not_isolated")
        require(sibling.runtime.get("DIRECTOR_HOST_PORT") == str(plan["host_port"]), "sibling_host_port_mismatch")
        backup = Path(plan["backup_path"])
        unlinked(backup)
        require(backup.parent.resolve() == sibling.output.resolve() and not backup.exists(), "sibling_backup_path_invalid")
        self.source_check()
        self.selectors_check()
        self.image_check()
        self.compose_check()
        self.database_check()
        report = sibling.doctor(defer_local_database=True)
        require(all(item["status"] == "PASS" or item == {
            "check": "database", "status": "DEFERRED", "reason": "local_database_start_required"}
            for item in report["checks"]), "sibling_doctor_refused")
        local_database = sibling.observed_database is None
        for key in ("database_identity", "database_instance_id"):
            expected = sibling.manifest["activation"] if local_database else sibling.observed_database
            require(self.observed_database[key] != expected[key], "sibling_database_not_isolated")
        final_graph, sibling_graph = document(self.rendered), document(sibling.rendered)
        # The existing Backup/RestoreDrill controls operate on this exact service;
        # an external-only database has no supported complete rehearsal contract.
        require("langgraph-primary-db" in sibling_graph["services"], "sibling_backup_database_required")
        health = sibling_graph["services"]["langgraph-primary-db"].get("healthcheck", {})
        require(not health.get("disable") and
                health.get("test") == ["CMD-SHELL", "pg_isready -U director_primary -d director_primary"],
                "sibling_primary_database_readiness_required")
        dependency = sibling_graph["services"]["orchestrator"].get("depends_on", {}).get("langgraph-primary-db", {})
        require(isinstance(dependency, dict) and set(dependency) <= {"condition", "required", "restart"} and
                dependency.get("condition") == "service_healthy" and dependency.get("required", True) is True and
                dependency.get("restart", False) is False, "sibling_primary_database_dependency_required")
        from urllib.parse import urlsplit
        database_service = sibling_graph["services"]["langgraph-primary-db"].get("environment", {})
        database_url = urlsplit(sibling_graph["services"]["orchestrator"].get("environment", {}).get(
            "LANGGRAPH_PRIMARY_POSTGRES_DSN", ""))
        require(database_service.get("POSTGRES_USER") == database_service.get("POSTGRES_DB") == "director_primary" and
                database_url.scheme in ("postgres", "postgresql") and database_url.hostname == "langgraph-primary-db" and
                (database_url.port or 5432) == 5432 and database_url.username == "director_primary" and
                database_url.path == "/director_primary", "sibling_backup_database_route_unsupported")
        identity_key = os.urandom(32).hex()
        try:
            final_resources = resource_inventory(self.run, self.spec["compose_project"], identity_key)
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ReleaseError("final_resource_inventory_unavailable") from None
        isolated_sibling(final_graph, sibling_graph, plan["host_port"],
                         n8n_host_port=sibling.runtime.get("N8N_HOST_PORT", "5678"),
                         protected_paths=self.protected_paths() + sibling.protected_paths(),
                         final_resources=final_resources, docker_host=self.docker_environment["DOCKER_HOST"],
                         registry_path=sibling.runtime.get("LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_DIRECTORY"),
                         identity_key=identity_key)
        require(self.daemon_id == sibling.daemon_id and self.docker_environment == sibling.docker_environment,
                "sibling_docker_target_mismatch")
        images = sibling.rehearsal_images(sibling_graph)
        require(sibling.application_environments["orchestrator"].get("LANGGRAPH_PRIMARY_ALLOW_INSECURE_LOCAL_POSTGRES") == "true",
                "sibling_primary_database_transport_unsupported")
        isolated_slack_identities(final_graph, sibling.application_environments, final_resources, identity_key)
        sibling.rehearsal_modes()
        intake = {"path": str(sibling.intake_receipt_path()), "binding": {
            "nonce": os.urandom(16).hex(), "candidate": sibling.spec["candidate"],
            "descriptor_sha256": sibling.descriptor_digest, "final_descriptor_sha256": self.descriptor_digest,
            "owner_descriptor_sha256": self.owner_descriptor_digest,
            "daemon_id": sibling.daemon_id, "project": sibling.spec["compose_project"],
            "names": {sibling_graph["volumes"][mount["source"]]["name"]: mount["source"]
                      for name in ("n8n", "n8n-db")
                      for mount in sibling_graph["services"].get(name, {}).get("volumes", [])}}}
        initialize_argvs = [[sibling.executables["docker"], "volume", "create", "--driver", "local",
            "--label", "com.docker.compose.project=" + sibling.spec["compose_project"],
            "--label", "com.docker.compose.volume=" + key,
            "--label", "org.director.release.intake=" + intake["binding"]["nonce"], name]
            for name, key in sorted(intake["binding"]["names"].items())]
        environment = sibling.compose_base_environment()
        base = [sibling.spec["executables"]["powershell"], "-NoProfile", "-NonInteractive", "-File",
                str(sibling.source / "scripts/windows-langgraph-primary-control.ps1")]
        common = ["-EnvironmentFile", sibling.spec["runtime_env"]["path"], "-ComposeProject",
                  sibling.spec["compose_project"], "-HostPort", str(plan["host_port"]), "-TimeoutSeconds", "120",
                  "-GitExecutable", sibling.executables["git"], "-DockerExecutable", sibling.executables["docker"],
                  "-GitHubExecutable", sibling.executables["gh"]]
        compose = ["docker", "compose", "--project-name", sibling.spec["compose_project"],
                   "--env-file", sibling.spec["runtime_env"]["path"]]
        immutable_pins = self.input_pins() + sibling.input_pins(include_runtime=False)
        require(all(Path(pin["path"]).resolve() != Path(sibling.spec["runtime_env"]["path"]).resolve()
                    for pin in immutable_pins), "sibling_runtime_aliases_immutable_input")
        pins = {pin["path"]: pin for pin in immutable_pins}
        expected_runtime = dict(sibling.runtime)
        runtime_changed = False
        actions = ["InitializeIntakeVolumes"] + (["StartDatabase"] if local_database else []) + [
            "Preflight", "Accept", "Migrate", "Backup", "RestoreDrill", "StageRuntime",
            "PrepareOfflineHandoff", "ActivateOffline", "Health", "Rollback", "Health", "Stop"]
        steps = []
        for action in actions:
            arguments = [*base, "-Action", action, *common]
            if action == "InitializeIntakeVolumes":
                arguments = []  # The explicit bounded commands are exposed below.
            elif action == "StartDatabase":
                arguments = [*compose, "up", "-d", "--no-deps", "--no-build", "--pull", "never", "--wait",
                             "--wait-timeout", "120", "langgraph-primary-db"]
            elif action == "Stop":
                arguments = [*compose, "stop", "--timeout", "30", "slackbot", "n8n", "orchestrator"]
            elif action == "Accept":
                arguments += ["-AcceptanceArtifactPath", plan["acceptance_artifact"]["path"]]
            elif action in ("Backup", "RestoreDrill"):
                arguments += ["-BackupPath", str(backup)]
            if arguments:
                arguments[0] = sibling.executables.get(arguments[0], arguments[0])
            payload = {"environment": environment, "cwd": str(sibling.source), "daemon_id": sibling.daemon_id,
                       "executables": sibling.executables,
                       "snapshot_path": sibling.spec["snapshot"]["path"],
                       "ports": [{"host_ip": port["host_ip"], "published": str(port["published"]),
                                  "protocol": port.get("protocol", "tcp"), "service": name}
                                 for name, service in sibling_graph["services"].items() for port in service.get("ports", [])],
                       "candidate": sibling.spec["candidate"], "action": action, "pins": list(pins.values()),
                       "runtime_pin": ({"path": sibling.spec["runtime_env"]["path"],
                                        "values_sha256": sha(encoded(expected_runtime))}
                                       if runtime_changed else sibling.spec["runtime_env"]),
                       "final_project": self.spec["compose_project"],
                       "slack_identity_key": identity_key,
                       "final_resources_sha256": sha(encoded(final_resources)),
                       # Activation can wait 75s for leases; rollback drains for
                       # 65s. Preserve their full 120s health wait plus overhead.
                       "control_timeout_seconds": 260 if action in ("ActivateOffline", "Rollback") else 140,
                       "images": images, "control_argv": arguments, "runtime_image": sibling.spec["image_id"],
                       "intake": intake,
                       "state_volumes": sibling.state_volumes,
                       "prior_images": sibling.prior_images_path(), "first_step": not steps,
                       "project": sibling.spec["compose_project"],
                       "fresh_networks": [item["name"] for item in sibling_graph["networks"].values()],
                       "fresh_volumes": list(intake["binding"]["names"])}
            if action == "InitializeIntakeVolumes":
                payload["initialize_argvs"] = initialize_argvs
            if local_database:
                payload["local_database"] = {"compose": compose, "bridge_path": sibling.spec["bridge_env"]["path"],
                    "generation": sibling.spec["generation_manifest"]["sha256"], "final": self.observed_database,
                    "expected": {key: sibling.manifest["activation"][key] for key in
                                 ("database_identity", "database_instance_id")}}
            argv = [sibling.spec["executables"]["python"], "-I", "-B", "-c", REHEARSAL_BOOTSTRAP,
                    base64.b64encode(encoded(payload)).decode("ascii")]
            # CreateProcess counts the terminating NUL and UTF-16 code units,
            # including quoting added by Python's actual Windows serializer.
            for command_argv in (argv, arguments, *(initialize_argvs if action == "InitializeIntakeVolumes" else [])):
                require(len(subprocess.list2cmdline(command_argv).encode("utf-16-le")) // 2 + 1 <= 32767,
                        "rehearsal_command_line_too_long")
            steps.append({"action": action, "control_argv": arguments, "argv": argv})
            if action == "InitializeIntakeVolumes":
                steps[-1]["initialize_argvs"] = initialize_argvs
                steps[-1]["timeout_seconds"] = 35
                steps[-1]["receipt_path"] = intake["path"]
            if action in RUNTIME_CHANGES:
                expected_runtime.update(RUNTIME_CHANGES[action])
                runtime_changed = True
        self.verify_inputs()
        sibling.verify_inputs()
        return {"schema_version": "director-release-rehearsal-plan/v1", "status": "PLANNED", "executed": False,
                "owner_descriptor_sha256": self.owner_descriptor_digest,
                "owner_launcher_sha256": self.owner_launcher_digest,
                "descriptor_sha256": self.descriptor_digest, "sibling_descriptor_sha256": sibling.descriptor_digest,
                "compose_project": sibling.spec["compose_project"], "candidate": self.spec["candidate"],
                "image_id": self.spec["image_id"], "cwd": str(sibling.source), "timeout_seconds": 1499,
                "environment_mode": "replace", "environment": environment,
                "steps": steps, "checks": report["checks"], "preparation_ready": report["preparation_ready"],
                "release_ready": False, "provider_calls": 0,
                "dependencies": DEPENDENCIES, "on_uncertain_effect": "STOP_RECONCILE_WITHOUT_REPLAY"}

    def rehearsal_modes(self):
        # The control actions intentionally change these switches. Resolve each
        # future graph before emission: a pinned template can interpolate them
        # into commands or mounts that are absent from its initial render.
        from director_orchestrator.primary_generation import resolved_deployment_digest
        from director_orchestrator.primary_activation_evidence import ActivationEvidenceError
        runtime = dict(self.runtime)
        for changes in RUNTIME_CHANGES.values():
            runtime.update(changes)
            graph = self.compose_document(runtime)
            try:
                require(all(graph["services"]["orchestrator"]["environment"].get(key) == value
                            for key, value in changes.items()) and
                        resolved_deployment_digest(graph, self.spec["image_id"]) == self.manifest["resolved_compose_sha256"],
                        "sibling_runtime_graph_changed")
            except (ActivationEvidenceError, KeyError, TypeError):
                raise ReleaseError("sibling_runtime_graph_changed") from None

    def rehearsal_images(self, graph):
        require(not self.run(["docker", "ps", "-a", "--filter",
                              "label=com.docker.compose.project=" + self.spec["compose_project"],
                              "--format", "{{.ID}}"]).strip(), "sibling_project_already_exists")
        require(not Path(self.prior_images_path()).exists(), "sibling_prior_images_exist")
        require(not os.path.lexists(self.prior_images_path().replace(".prior-images.", ".offline-attempt.")),
                "sibling_offline_attempt_exists")
        unlinked(self.intake_receipt_path())
        require(not os.path.lexists(self.intake_receipt_path()), "sibling_intake_receipt_exists")
        networks = set(self.run(["docker", "network", "ls", "--format", "{{.Name}}"]).decode().splitlines())
        require(not networks.intersection(item["name"] for item in graph["networks"].values()),
                "sibling_network_not_fresh")
        images = {}
        self.application_environments = {}
        for name, service in graph["services"].items():
            reference = service["image"]
            inspected = document(self.run(["docker", "image", "inspect", reference]))
            require(len(inspected) == 1 and re.fullmatch(r"sha256:[0-9a-f]{64}", inspected[0]["Id"]),
                    "rehearsal_image_not_cached")
            require(not reference.startswith("sha256:") or inspected[0]["Id"] == reference,
                    "rehearsal_cached_image_mismatch")
            healthcheck = inspected[0].get("Config", {}).get("Healthcheck", {})
            override = service.get("healthcheck", {})
            if override.get("disable"):
                healthcheck = {"test": ["NONE"]}
            elif "test" in override:
                healthcheck = override
            reviewed_healthcheck(name, healthcheck)
            if name in ("orchestrator", "slackbot"):
                self.application_environments[name] = reviewed_application_environment(service, inspected[0].get("Config", {}))
            if name == "orchestrator":
                # The control's direct digest probe runs this image without
                # Compose overrides; masking unsafe image defaults is insufficient.
                reviewed_application_environment({}, inspected[0].get("Config", {}))
                reviewed_orchestrator(service, inspected[0].get("Config", {}))
            image_mounts = set((inspected[0].get("Config", {}).get("Volumes") or {}))
            require(image_mounts <= {mount["target"] for mount in service.get("volumes", [])} and
                    image_mounts <= set(STOCK_MOUNTS[name]), "sibling_mount_unsupported")
            images[reference] = inspected[0]["Id"]
        # n8n must start without persisted workflows or credentials. The caller
        # may provision fresh isolated volumes; reused n8n state is unsupported.
        existing = set(self.run(["docker", "volume", "ls", "--format", "{{.Name}}"]).decode().splitlines())
        for name in ("n8n", "n8n-db"):
            for mount in graph["services"].get(name, {}).get("volumes", []):
                require(mount.get("type") == "volume" and mount["source"] in graph.get("volumes", {}) and
                        graph["volumes"][mount["source"]]["name"] not in existing, "sibling_n8n_state_not_fresh")
        self.state_volumes = {}
        for service, key in (("orchestrator", "director-data"), ("langgraph-primary-db", "langgraph-primary-db-data")):
            if service not in graph["services"]:
                continue
            name = graph["volumes"][key]["name"]
            require(name in existing, "sibling_state_volume_not_provisioned")
            rows = document(self.run(["docker", "volume", "inspect", name]))
            require(len(rows) == 1 and rows[0].get("Name") == name and rows[0].get("Driver") == "local" and
                    not rows[0].get("Options") and rows[0].get("Labels", {}).get("com.docker.compose.project") == graph["name"] and
                    rows[0].get("Labels", {}).get("com.docker.compose.volume") == key, "sibling_state_volume_identity_mismatch")
            self.state_volumes[name] = sha(encoded(volume_identity(rows[0])))
        return images

    def input_pins(self, *, include_runtime=True):
        pins = [{"path": str(self.path), "sha256": self.descriptor_digest},
                {"path": str(self.owner_root / "launcher.py"), "sha256": self.owner_launcher_digest},
                {"path": str(self.owner_root / "release-descriptor.json"), "sha256": self.owner_descriptor_digest}]
        pins += [self.spec[key] for key in ("bridge_env", "generation_manifest", "validation_manifest", "bridge_ca")]
        if include_runtime:
            pins.append(self.spec["runtime_env"])
        pins += self.spec["compose_files"]
        if "rehearsal" in self.spec:
            pins += [self.spec["rehearsal"][key] for key in ("descriptor", "acceptance_artifact")]
        return pins

    def prior_images_path(self):
        return (self.spec["runtime_env"]["path"] + ".prior-images." +
                self.spec["generation_manifest"]["sha256"] + "." + self.spec["image_id"][7:] + ".json")

    def intake_receipt_path(self):
        return self.output / (self.spec["release_id"] + ".intake-volumes.jsonl")

    def protected_paths(self):
        paths = [self.source, self.output, self.path, self.owner_root, self.spec["snapshot"]["path"]]
        paths += [self.spec[key]["path"] for key in
                  ("runtime_env", "bridge_env", "generation_manifest", "validation_manifest", "bridge_ca")]
        paths += [pin["path"] for pin in self.spec["compose_files"]]
        if self.bridge.get("DIRECTOR_HOST_PROVIDER_PROCESS_ENVIRONMENT_FILE"):
            paths.append(self.bridge["DIRECTOR_HOST_PROVIDER_PROCESS_ENVIRONMENT_FILE"])
        if "rehearsal" in self.spec:
            paths += [self.spec["rehearsal"][key]["path"] for key in ("descriptor", "acceptance_artifact")]
        for path in paths:
            unlinked(Path(path))
        return paths


def paths_overlap(left, right):
    left, right = Path(left).resolve(), Path(right).resolve()
    return left.is_relative_to(right) or right.is_relative_to(left)


def acceptance_inputs(runtime):
    mode = runtime.get("LANGGRAPH_PRIMARY_ACCEPTANCE_MODE", "github-v1")
    if mode == "owner-local-evidence-v1":
        require(not runtime.get("LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_SHA256") and
                runtime.get("LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_PATH", "") in
                ("", "/run/director-acceptance-registry/registry.json"), "acceptance_registry_conflict")
        required = ("LOCAL_EVIDENCE_ADOPTION_PATH", "LOCAL_EVIDENCE_ADOPTION_SHA256", "ACCEPTANCE_JOURNAL_PATH",
                    "ACTIVATION_SECURITY_REVIEWER_ALLOWLIST_JSON", "ACTIVATION_INDEPENDENT_REVIEWER_ALLOWLIST_JSON")
    elif mode in ("provider-v2", "provider-v3-two-party"):
        required = ("ACCEPTANCE_REGISTRY_PATH", "ACCEPTANCE_REGISTRY_SHA256", "ACCEPTANCE_JOURNAL_PATH",
                    "ACCEPTANCE_REGISTRY_DIRECTORY")
    else:
        raise ReleaseError("generation_acceptance_mode_unsupported")
    require(all(isinstance(runtime.get("LANGGRAPH_PRIMARY_" + key), str) and
                runtime["LANGGRAPH_PRIMARY_" + key].strip() and
                not runtime["LANGGRAPH_PRIMARY_" + key].startswith("REPLACE_WITH_") for key in required),
            "generation_acceptance_inputs_missing")
    digest_key = "LOCAL_EVIDENCE_ADOPTION_SHA256" if mode == "owner-local-evidence-v1" else "ACCEPTANCE_REGISTRY_SHA256"
    require(bool(re.fullmatch(r"[0-9a-f]{64}", runtime["LANGGRAPH_PRIMARY_" + digest_key])),
            "generation_acceptance_digest_invalid")


def reviewed_healthcheck(name, healthcheck):
    tests = {
        "orchestrator": ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"],
        "langgraph-primary-db": ["CMD-SHELL", "pg_isready -U director_primary -d director_primary"],
        "n8n-db": ["CMD-SHELL", "pg_isready -U n8n -d n8n"],
    }
    test = (healthcheck or {}).get("test", (healthcheck or {}).get("Test"))
    require(test in (None, [], ["NONE"]) or test == tests.get(name), "sibling_healthcheck_unsupported")


def reviewed_orchestrator(service, image=None):
    app = service.get("environment", {}).get("DIRECTOR_ASGI_APP", "director_orchestrator.main:app")
    require(app in ("director_orchestrator.main:app", "director_orchestrator.primary_staging:app"),
            "sibling_orchestrator_command_unsupported")
    entrypoint = service.get("entrypoint")
    command = service.get("command")
    if image is not None:
        entrypoint = image.get("Entrypoint") if entrypoint is None else entrypoint
        command = image.get("Cmd") if command is None else command
    require(entrypoint in (None, [], "") and
            (command is None and image is None or command == ["uvicorn", app, "--host", "0.0.0.0", "--port", "8080"]),
            "sibling_orchestrator_command_unsupported")


def reviewed_application_environment(service, image=None):
    expected = {"PYTHONPATH": "/app/src", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}
    values = {}
    if image is not None:
        try:
            values = inspected_environment(image)
        except (ValueError, TypeError, AttributeError):
            raise ReleaseError("sibling_loader_environment_unsupported") from None
    override = service.get("environment", {})
    require(isinstance(override, dict) and all(isinstance(key, str) and key and
            (value is None or isinstance(value, str)) for key, value in override.items()), "sibling_loader_environment_unsupported")
    values.update(override)
    values = {key: value for key, value in values.items() if value is not None}
    for key, value in values.items():
        require(key.upper() not in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"} or not value,
                "sibling_proxy_environment_unsupported")
        require(not key.startswith(("LD_", "DYLD_")) and key not in
                {"GCONV_PATH", "GLIBC_TUNABLES", "LOCPATH", "BASH_ENV", "ENV"}, "sibling_loader_environment_unsupported")
        if key.startswith("PYTHON"):
            require(key in expected and value == expected[key] or key in {"PYTHON_VERSION", "PYTHON_SHA256"},
                    "sibling_loader_environment_unsupported")
    if "PATH" in values:
        directories = str(values["PATH"]).split(":")
        require(directories[0] == "/usr/local/bin" and set(directories) <=
                {"/usr/local/bin", "/usr/local/sbin", "/usr/sbin", "/usr/bin", "/sbin", "/bin"},
                "sibling_loader_environment_unsupported")
    directory = service.get("working_dir", (image or {}).get("WorkingDir"))
    require(directory in (None, "/app") if image is None else directory == "/app",
            "sibling_working_directory_unsupported")
    if image is not None:
        require(all(values.get(key) == value for key, value in expected.items()) and "PATH" in values,
                "sibling_loader_environment_unsupported")
    return values


STOCK_MOUNTS = {
    "orchestrator": {"/data": "director-data", "/repository": None, "/run/director-acceptance-registry": None},
    "slackbot": {}, "n8n": {"/home/node/.n8n": "n8n-data"},
    "n8n-db": {"/var/lib/postgresql/data": "n8n-db-data"},
    "langgraph-primary-db": {"/var/lib/postgresql/data": "langgraph-primary-db-data"},
}


def stock_mounts(graph, *, registry_path=None):
    allowed = {key for mounts in STOCK_MOUNTS.values() for key in mounts.values() if key}
    require(set(graph.get("volumes", {})) <= allowed, "sibling_mount_unsupported")
    for key, volume in graph.get("volumes", {}).items():
        require(volume.get("name") == graph["name"] + "_" + key, "sibling_mount_unsupported")
    for name, service in graph["services"].items():
        seen = set()
        for mount in service.get("volumes", []):
            target = mount.get("target")
            require(target in STOCK_MOUNTS[name] and target not in seen, "sibling_mount_unsupported")
            seen.add(target)
            key = STOCK_MOUNTS[name][target]
            if key:
                require(mount.get("type") == "volume" and mount.get("source") == key and
                        key in graph.get("volumes", {}) and not mount.get("read_only") and not mount.get("volume") and
                        set(mount) <= {"type", "source", "target", "read_only", "volume"}, "sibling_mount_unsupported")
            else:
                require(mount.get("type") == "bind" and mount.get("read_only") is True and
                        mount.get("bind") == {"create_host_path": False} and
                        set(mount) <= {"type", "source", "target", "read_only", "bind"}, "sibling_mount_unsupported")
                source = Path(mount["source"])
                unlinked(source)
                require(source.is_dir(), "sibling_bind_source_unavailable")
                if target == "/run/director-acceptance-registry":
                    require(registry_path and source.resolve() == Path(registry_path).resolve(), "sibling_registry_mount_mismatch")
        require({target for target, key in STOCK_MOUNTS[name].items() if key} <= seen, "sibling_mount_unsupported")


def isolated_slack_identities(final, environments, final_resources, identity_key):
    for key in SLACK_IDENTITY_KEYS:
        final_values = {service.get("environment", {}).get(key) for service in final["services"].values()} - {None, ""}
        sibling_values = {values.get(key) for values in environments.values()} - {None, ""}
        require(not final_values.intersection(sibling_values), "sibling_slack_identity_not_isolated")
        if identity_key:
            require(not set(final_resources["slack_identities"][key]).intersection(
                slack_identity_digest(identity_key, key, value) for value in sibling_values), "sibling_slack_identity_not_isolated")


def isolated_sibling(final, sibling, host_port, *, protected_paths=(), final_resources=None, docker_host="", registry_path=None,
                     n8n_host_port="5678", identity_key=None):
    """Check resolved Compose resources, not just a different project name."""
    project = sibling["name"]
    allowed_dependencies = {"orchestrator": {"langgraph-primary-db"}, "slackbot": {"orchestrator", "n8n"},
                            "n8n": {"n8n-db"}, "n8n-db": set(), "langgraph-primary-db": set()}
    require({"orchestrator", "slackbot", "n8n"} <= set(sibling["services"]) <= set(allowed_dependencies),
            "sibling_service_graph_unsupported")
    for name, service in sibling["services"].items():
        require(set(service) <= COMPOSE_SERVICE_FIELDS,
                "sibling_service_option_unsupported")
        require(service.get("restart", "no") in ("no", "unless-stopped") and
                set(service.get("deploy", {})) <= {"replicas", "mode"}, "sibling_restart_or_deploy_unsupported")
        require(all(value in (None, {}) for value in service.get("networks", {}).values()), "sibling_network_option_unsupported")
        logging = service.get("logging", {})
        require(logging.get("driver") in ("local", "json-file") and set(logging) <= {"driver", "options"} and
                set(logging.get("options", {})) <= {"max-size", "max-file", "compress"}, "sibling_logging_unsupported")
        for key, value in logging.get("options", {}).items():
            require(isinstance(value, str) and bool(re.fullmatch(
                {"max-size": r"[1-9][0-9]*[kmg]?", "max-file": r"[1-9][0-9]*", "compress": r"true|false"}[key], value)),
                "sibling_logging_unsupported")
        deploy = service.get("deploy", {})
        require(all(type(value) is int and value == 1 for value in
                    (service.get("scale", 1), deploy.get("replicas", 1))) and
                deploy.get("mode", "replicated") == "replicated", "sibling_service_not_singleton")
        reviewed_healthcheck(name, service.get("healthcheck", {}))
        if name in ("orchestrator", "slackbot"):
            reviewed_application_environment(service)
        if name == "orchestrator":
            reviewed_orchestrator(service)
        require(set(service.get("depends_on", {})) <= allowed_dependencies[name] and
                set(service.get("depends_on", {})) <= set(sibling["services"]) and
                not any(service.get(key) for key in ("links", "external_links", "extends", "post_start", "pre_stop")),
                "sibling_service_graph_unsupported")
        require(bool(re.fullmatch(r"(?:sha256:[0-9a-f]{64}|[^\s]+@sha256:[0-9a-f]{64})", service.get("image", ""))) and
                service.get("pull_policy") == "never", "sibling_image_not_immutable_offline")
        require(not service.get("build") or name in ("orchestrator", "slackbot"), "sibling_build_unsupported")
        if name in ("n8n", "n8n-db", "langgraph-primary-db"):
            prefix = "docker.n8n.io/n8nio/n8n:" if name == "n8n" else "postgres:"
            require(service["image"].startswith(prefix) and not service.get("command") and
                    not service.get("entrypoint"), "sibling_service_graph_unsupported")
    # A reused workflow database or alternate startup command can bypass the
    # Director's paid switches. Support the stock n8n/Postgres route only, with
    # fresh local state checked below before a plan is emitted.
    n8n = sibling["services"]["n8n"].get("environment", {})
    n8n_keys = {"DB_TYPE", "DB_POSTGRESDB_HOST", "DB_POSTGRESDB_PORT", "DB_POSTGRESDB_DATABASE",
                "DB_POSTGRESDB_USER", "DB_POSTGRESDB_PASSWORD", "N8N_ENCRYPTION_KEY", "N8N_HOST", "N8N_PORT",
                "N8N_PROTOCOL", "N8N_SECURE_COOKIE", "N8N_DIAGNOSTICS_ENABLED", "N8N_PERSONALIZATION_ENABLED",
                "N8N_BLOCK_ENV_ACCESS_IN_NODE", "GENERIC_TIMEZONE"}
    require(set(n8n) <= n8n_keys and n8n.get("DB_TYPE", "sqlite") in ("sqlite", "postgresdb") and
            (n8n.get("DB_TYPE", "sqlite") != "postgresdb" or
             (n8n.get("DB_POSTGRESDB_HOST") == "n8n-db" and "n8n-db" in sibling["services"])),
            "sibling_n8n_configuration_unsupported")
    require(all(str(n8n.get(key)) == value for key, value in {
        "N8N_HOST": "localhost", "N8N_PORT": "5678", "N8N_PROTOCOL": "http", "N8N_SECURE_COOKIE": "false",
        "N8N_DIAGNOSTICS_ENABLED": "false", "N8N_PERSONALIZATION_ENABLED": "false", "N8N_BLOCK_ENV_ACCESS_IN_NODE": "true"}.items()),
        "sibling_n8n_configuration_unsupported")
    if n8n.get("DB_TYPE", "sqlite") == "postgresdb":
        database = sibling["services"]["n8n-db"].get("environment", {})
        require(str(n8n.get("DB_POSTGRESDB_PORT", "5432")) == "5432" and
                n8n.get("DB_POSTGRESDB_DATABASE") == database.get("POSTGRES_DB") == "n8n" and
                n8n.get("DB_POSTGRESDB_USER") == database.get("POSTGRES_USER") == "n8n" and
                bool(database.get("POSTGRES_PASSWORD")) and
                n8n.get("DB_POSTGRESDB_PASSWORD") == database["POSTGRES_PASSWORD"], "sibling_n8n_database_mismatch")
        dependency = sibling["services"]["n8n"].get("depends_on", {}).get("n8n-db", {})
        health = sibling["services"]["n8n-db"].get("healthcheck", {})
        require(isinstance(dependency, dict) and set(dependency) <= {"condition", "required", "restart"} and
                dependency.get("condition") == "service_healthy" and dependency.get("required", True) is True and
                dependency.get("restart", False) is False and not health.get("disable") and
                health.get("test") == ["CMD-SHELL", "pg_isready -U n8n -d n8n"], "sibling_n8n_database_dependency_missing")
    for name in ("n8n-db", "langgraph-primary-db"):
        if name in sibling["services"]:
            require(set(sibling["services"][name].get("environment", {})) <=
                    {"POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"}, "sibling_database_configuration_unsupported")
    final_resources = final_resources or {}
    for kind in ("networks", "volumes"):
        final_names = {item.get("name") for item in final.get(kind, {}).values()} | set(final_resources.get(kind, []))
        for item in sibling.get(kind, {}).values():
            if kind == "networks":
                defaults = {"external": False, "internal": False, "attachable": False, "enable_ipv4": True, "enable_ipv6": False}
                require(set(item) <= {"name", "ipam", *defaults} and type(item.get("ipam", {})) is dict and not item.get("ipam", {}) and
                        all(item.get(key, value) is value for key, value in defaults.items()), "sibling_network_option_unsupported")
            require(not item.get("external") and not item.get("driver_opts") and not item.get("driver") and
                    isinstance(item.get("name"), str) and
                    item["name"].startswith(project + "_") and item["name"] not in final_names,
                    "sibling_shared_" + kind)
    require(bool(sibling.get("networks")), "sibling_network_missing")
    final_ports = {str(port.get("published")) for service in final["services"].values() for port in service.get("ports", [])}
    final_ports.update(final_resources.get("ports", []))
    final_binds = [mount["source"] for service in final["services"].values()
                   for mount in service.get("volumes", []) if mount.get("type") == "bind"]
    final_binds += final_resources.get("binds", [])
    engine_sockets = ["/var/run/docker.sock", "/run/docker.sock"]
    if docker_host.startswith("unix://"):
        engine_sockets.append(docker_host[len("unix://"):])
    # Compose overrides and env_file expansion can replace profile identities.
    # Compare the values the resolved services will actually receive.
    isolated_slack_identities(final, {name: service.get("environment", {}) for name, service in sibling["services"].items()},
                             final_resources, identity_key)
    for name in ("orchestrator", "slackbot"):
        effective = sibling["services"][name].get("environment", {})
        require(all(effective.get(key) == value for key, value in
                (("ALLOW_PAID_RUNS", "false"), ("LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED", "false"),
                 ("LANGGRAPH_PARALLEL_KILL_SWITCH", "true"))), "sibling_must_be_unpaid")
        require(effective.get("CHATGPT_BRIDGE_ALLOW_PAID_RUNS", "false") == "false" and
                effective.get("CHATGPT_BRIDGE_ENABLED", "false") == "false", "sibling_bridge_must_be_disabled")
    gateway = sibling["services"]["slackbot"].get("environment", {})
    require(all(isinstance(gateway.get(key), str) and gateway[key].strip() and not gateway[key].startswith("REPLACE_WITH_")
                for key in ("SLACK_APP_TOKEN", "SLACK_BOT_TOKEN", "SLACK_DIRECTOR_CHANNEL_ID")), "sibling_gateway_identity_missing")
    require(gateway.get("ORCHESTRATOR_BASE_URL", "http://orchestrator:8080") == "http://orchestrator:8080" and
            gateway.get("N8N_DIRECTOR_WEBHOOK_URL", "") in ("", "http://n8n:5678/webhook/director-intake"),
            "sibling_gateway_route_not_local")
    require(sibling["services"]["slackbot"].get("command") == ["python", "-m", "director_orchestrator.slack_bot"] and
            not sibling["services"]["slackbot"].get("entrypoint"), "sibling_gateway_command_unsupported")
    sibling_ports = set()
    for name, service in sibling["services"].items():
        require(not any(service.get(key) for key in ("network_mode", "pid", "ipc", "privileged", "devices",
                                                    "volumes_from", "container_name", "configs", "secrets",
                                                    "extra_hosts", "develop", "use_api_socket")), "sibling_host_resource_refused")
        require(len(service.get("ports", [])) <= (1 if name in ("orchestrator", "n8n") else 0), "sibling_port_not_stock")
        for port in service.get("ports", []):
            published = str(port.get("published", ""))
            require(port.get("host_ip") == "127.0.0.1" and published.isdigit() and
                    1024 <= int(published) <= 65535 and published not in final_ports and
                    port.get("protocol", "tcp") in ("tcp", "udp"), "sibling_port_not_isolated")
            binding = (int(published), port.get("protocol", "tcp"))
            require(binding not in sibling_ports, "sibling_port_collision")
            sibling_ports.add(binding)
            require(port.get("target") == (8080 if name == "orchestrator" else 5678) and
                    port.get("protocol", "tcp") == "tcp" and
                    published == str(host_port if name == "orchestrator" else n8n_host_port), "sibling_port_not_stock")
        for mount in service.get("volumes", []):
            source = str(mount.get("source", ""))
            pipe = source.replace("\\", "/").lower().startswith("//./pipe/")
            require(mount.get("type") != "npipe" and not pipe, "sibling_engine_socket_refused")
            if mount.get("type") == "bind":
                require(not any(paths_overlap(source, socket) for socket in engine_sockets) and
                        not (Path(source).exists() and stat.S_ISSOCK(Path(source).stat().st_mode)),
                        "sibling_engine_socket_refused")
            if mount.get("type") == "bind" and not mount.get("read_only"):
                unlinked(Path(mount["source"]))
                require(all(not paths_overlap(mount["source"], source) for source in [*final_binds, *protected_paths]),
                        "sibling_shared_write_mount")
    require(any(str(port.get("published")) == str(host_port) and port.get("target") == 8080 and
                port.get("protocol", "tcp") == "tcp"
                for port in sibling["services"]["orchestrator"].get("ports", [])), "sibling_host_port_mismatch")
    stock_mounts(sibling, registry_path=registry_path)


@contextmanager
def locked(path):
    unlinked(path)
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ReleaseError("another_release_writer") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def append_record(path, value):
    value = dict(value, record_sha256=sha(encoded(value)))
    unlinked(path)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "ab") as stream:
        stream.write(encoded(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def read_journal(path, descriptor_digest, owner_descriptor_digest, owner_launcher_digest):
    if not path.exists():
        return []
    raw = bounded_read(path)
    require(not raw or raw.endswith(b"\n"), "incomplete_journal_no_replay")
    result = [document(line) for line in raw.splitlines()]
    require(len(result) <= 2, "unexpected_journal_sequence")
    for number, entry in enumerate(result, 1):
        fields = {"event", "descriptor_sha256", "owner_descriptor_sha256", "owner_launcher_sha256",
                  "step", "sequence", "record_sha256", "output_sha256"}
        if number == 2:
            fields |= {"status"}
        require(isinstance(entry, dict) and set(entry) == fields, "invalid_journal_record")
        require(entry["sequence"] == number and entry["descriptor_sha256"] == descriptor_digest and
                entry["owner_descriptor_sha256"] == owner_descriptor_digest and
                entry["owner_launcher_sha256"] == owner_launcher_digest and
                entry["step"] == "render_compose" and entry["event"] == ("intent" if number == 1 else "receipt"),
                "journal_binding_mismatch")
        require(sha(encoded({k: v for k, v in entry.items() if k != "record_sha256"})) == entry["record_sha256"],
                "journal_digest_mismatch")
        require(isinstance(entry["output_sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", entry["output_sha256"]),
                "journal_output_digest_invalid")
        if number == 2:
            require(entry["status"] == "COMPLETE" and entry["output_sha256"] == result[0]["output_sha256"],
                    "journal_receipt_mismatch")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("doctor", "prepare", "resume", "rehearsal"))
    parser.add_argument("--descriptor", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        release = Release(args.descriptor)
        if args.action == "doctor":
            report = release.doctor()
            print(json.dumps(report, sort_keys=True))
            return 2  # Missing authority is never a PASS, even with every diagnostic green.
        if args.action == "rehearsal":
            print(json.dumps(release.rehearsal(), sort_keys=True))
            return 0
        print(json.dumps(release.prepare(resume=args.action == "resume"), sort_keys=True))
        return 0
    except Exception as error:
        print(json.dumps({"status": "BLOCKED", "reason": str(error) if isinstance(error, ReleaseError) else type(error).__name__,
                          "release_ready": False, "provider_calls": 0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
