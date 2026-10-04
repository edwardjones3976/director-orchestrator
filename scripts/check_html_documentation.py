"""Validate canonical HTML documentation, local links and the narrow Markdown exceptions."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit


COMPATIBILITY_NOTICE = (
    "This file is a link-only compatibility entry point for existing references "
    "from the separately managed Homemade LLM project; do not add substantive documentation here."
)
README_POINTER = """# Director Orchestrator

The canonical project overview is [README.html](README.html).
Browse the [HTML documentation library](docs/index.html), [provider profiles](docs/provider-profiles/README.html), and [HTML documentation policy](docs/html-documentation-policy.html).

This small file is retained only for GitHub discovery. Open the HTML files in a browser; GitHub source views do not render complete HTML pages.
"""

CONSUMER_SUFFIXES = frozenset({
    '.py', '.ps1', '.sh', '.md', '.mdc', '.yaml', '.yml', '.toml', '.json',
    '.txt', '.ini', '.cfg', '.conf', '.xml', '.rst', '.js', '.css',
})
DOCUMENT_REFERENCE = re.compile(
    r'(?<![A-Za-z0-9_./\\])(?:\.\.?[/\\])*(?P<path>docs[/\\][A-Za-z0-9_./\\-]+\.md)'
    r'(?=$|[^A-Za-z0-9_.-])'
)


def _url_parts(value: str):
    if any(ord(char) < 32 or ord(char) == 127 for char in unquote(value)) or re.search(r'%(?![0-9a-fA-F]{2})', value):
        raise ValueError('control character or invalid percent escape')
    parts = urlsplit(value)
    if parts.scheme.lower() not in {'', 'https', 'http', 'mailto', 'codex'}:
        raise ValueError('unsupported or executable URL scheme')
    if parts.scheme.lower() in {'https', 'http'}:
        if not parts.hostname:
            raise ValueError('missing URL host')
        parts.port  # Validate invalid/non-numeric ports too.
    return parts


def _manifest_path(root: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if not name or relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name or any(ord(c) < 32 for c in name):
        raise ValueError('manifest paths must be repository-relative')
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError('unsafe manifest path')
    return path


def _compatibility_errors(root: Path, old: str, new: str) -> list[str]:
    """Only the established pointer notice and valid legacy anchors are allowed.

    Listing a file in the manifest is not an exemption for arbitrary Markdown.
    The canonical target must be the mapped HTML document, never another pointer.
    """
    source = _manifest_path(root, old)
    target = _manifest_path(root, new)
    if not source.is_file() or not target.is_file():
        return ['missing compatibility pointer or canonical target: ' + old]
    if source.suffix != '.md' or target.suffix != '.html' or source.parent != target.parent:
        return ['invalid compatibility pointer mapping: ' + old]
    text = source.read_text(encoding='utf-8')
    anchors = re.findall(r'^<a id="([A-Za-z0-9_-]+)"></a>\s*$', text, re.MULTILINE)
    without_anchors = re.sub(r'^<a id="[A-Za-z0-9_-]+"></a>\s*$', '', text, flags=re.MULTILINE)
    without_anchors = re.sub(r'\n\s*\n', '\n\n', without_anchors)
    expected = f'# Document moved to HTML\n\nThe maintained document is [{target.name}]({target.name}). {COMPATIBILITY_NOTICE}'
    errors = []
    if without_anchors.strip() != expected or len(text.encode('utf-8')) > 2048:
        errors.append('compatibility Markdown must remain link-only: ' + old)
    target_ids = Page(target.read_text(encoding='utf-8')).ids
    if len(anchors) != len(set(anchors)) or any(anchor not in target_ids for anchor in anchors):
        errors.append('compatibility pointer has an invalid legacy anchor: ' + old)
    return errors


def _provenance_errors(root: Path, manifest: dict) -> list[str]:
    """Verify retained source digests against Git bytes, independent of CRLF checkout rules."""
    base = manifest.get('migration_base')
    if base is None and 'documents' not in manifest:
        return []  # A standalone HTML fixture/library need not describe a migration.
    if not isinstance(base, str) or not re.fullmatch(r'[0-9a-f]{40}', base):
        return ['invalid migration base']
    records = manifest.get('documents', [])
    sources = []
    try:
        for record in records:
            source = record['source']
            _manifest_path(root, source)
            if manifest['mapping'].get(source) != record['target'] or not re.fullmatch(r'[0-9a-f]{64}', record['source_sha256']):
                return ['invalid migration source record: ' + source]
            sources.append(source)
        if len(sources) != len(set(sources)) or set(sources) != set(manifest['mapping']):
            return ['migration source records do not match the document mapping']
        ancestor = subprocess.run(['git', 'merge-base', '--is-ancestor', base, 'HEAD'], cwd=root,
                                  capture_output=True, timeout=20)
        if ancestor.returncode:
            return ['migration base is unavailable or not an ancestor of HEAD']
        result = subprocess.run(['git', 'cat-file', '--batch'], cwd=root,
                                input=''.join(f'{base}:{source}\n' for source in sources).encode('utf-8'),
                                capture_output=True, timeout=20, check=True)
        data, offset, errors = result.stdout, 0, []
        for record in records:
            end = data.index(b'\n', offset)
            fields = data[offset:end].split()
            if len(fields) != 3 or fields[1] != b'blob':
                return ['missing migration source blob: ' + record['source']]
            size = int(fields[2])
            blob = data[end + 1:end + 1 + size]
            offset = end + 2 + size
            if len(blob) != size or hashlib.sha256(blob).hexdigest() != record['source_sha256']:
                errors.append('migration source digest mismatch: ' + record['source'])
        return errors
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError):
        return ['migration source provenance could not be verified']


def _consumer_reference_errors(root: Path, manifest: dict) -> list[str]:
    """Check tracked active source/config pointers, without rewriting evidence.

    Tests deliberately carry old-path fixtures; migration records and archives
    retain the original names as evidence. Homemade LLM is separately managed.
    Remote historical URLs are not local documentation pointers.
    """
    try:
        removed = {
            old: new for old, new in manifest['mapping'].items()
            if not _manifest_path(root, old).is_file()
        }
        tracked = subprocess.run(['git', 'ls-files', '-z'], cwd=root,
                                 capture_output=True, timeout=20)
    except (OSError, ValueError, subprocess.SubprocessError):
        return ['active documentation consumer inventory could not be verified']
    if tracked.returncode:
        if 'migration_base' in manifest:
            return ['active documentation consumer inventory could not be verified']
        return []  # A standalone, non-Git HTML library has no tracked source consumers.
    errors = []
    for raw in tracked.stdout.split(b'\0'):
        if not raw:
            continue
        name = raw.decode('utf-8')
        if name.startswith(('tests/', 'docs/archive/', 'projects/homemade-llm/')) or name == 'docs/html-migration-map.json':
            continue
        relative = PurePosixPath(name)
        if relative.suffix.lower() not in CONSUMER_SUFFIXES and not relative.name.startswith('.env') and relative.name not in {'Dockerfile', 'Makefile'}:
            continue
        try:
            path = _manifest_path(root, name)
            if not path.is_file():
                continue
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError, ValueError):
            errors.append('active documentation consumer is unreadable or unsafe: ' + name)
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for match in DOCUMENT_REFERENCE.finditer(line):
                old = match['path'].replace('\\', '/')
                if old in removed:
                    errors.append(f'{name}:{number}: removed Markdown reference {old}; use {removed[old]}')
    return errors


class Page(HTMLParser):
    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.links: list[str] = []
        self.errors: list[str] = []
        self.has_main = self.has_h1 = self.has_title = self.has_language = False
        self.main_count = 0
        self.feed(source)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        self.has_main |= tag == 'main'
        self.main_count += tag == 'main'
        self.has_h1 |= tag == 'h1'
        self.has_title |= tag == 'title'
        self.has_language |= tag == 'html' and bool(values.get('lang'))
        identity = values.get('id')
        if identity:
            if identity in self.ids:
                self.errors.append('duplicate id: ' + identity)
            self.ids.add(identity)
        if tag == 'a' and values.get('name'):
            self.ids.add(values['name'])
        if tag in {'iframe', 'object', 'embed', 'base', 'form', 'svg', 'math'}:
            self.errors.append('unsupported active element: ' + tag)
        if tag == 'meta' and (values.get('http-equiv') or '').lower() == 'refresh':
            self.errors.append('automatic document redirects are not permitted')
        if len(attrs) != len(values):
            self.errors.append('duplicate HTML attribute')
        for key, value in attrs:
            if key.lower().startswith('on'):
                self.errors.append('event handler is not permitted: ' + key)
            if key in {'href', 'src'} and value:
                try:
                    _url_parts(value)
                except ValueError:
                    self.errors.append('malformed or executable URL is not permitted')
                else:
                    self.links.append(value)
        if tag == 'script' and values.get('src') not in {'assets/documentation.js', '../assets/documentation.js'}:
            self.errors.append('only the local documentation navigation script is permitted')


def check(root: Path) -> list[str]:
    root = root.resolve()
    manifest = json.loads((root/'docs/html-migration-map.json').read_text(encoding='utf-8'))
    paths = sorted({root/'README.html', *(path for path in (root/'docs').rglob('*.html')
                                       if path.relative_to(root).parts[:2] != ('docs', 'archive'))})
    pages: dict[Path, Page] = {}
    errors: list[str] = _provenance_errors(root, manifest)
    errors.extend(_consumer_reference_errors(root, manifest))
    for path in paths:
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            errors.append(f'{path}: unsafe or missing document')
            continue
        page = Page(path.read_text(encoding='utf-8'))
        pages[path.resolve()] = page
        for label in ('main', 'h1', 'title', 'language'):
            if not getattr(page, 'has_'+label):
                page.errors.append('missing ' + label)
        if page.main_count > 1:
            page.errors.append('multiple main elements')
        errors.extend(f'{path.relative_to(root)}: {error}' for error in page.errors)
    for old, new in manifest['mapping'].items():
        try:
            old_path, new_path = _manifest_path(root, old), _manifest_path(root, new)
        except ValueError as error:
            errors.append('invalid migration mapping: ' + str(error))
            continue
        if new_path.suffix != '.html' or not new_path.is_file():
            errors.append('missing canonical HTML: ' + new)
        if old != 'README.md' and old_path.is_file() and old not in manifest.get('compatibility_pointers', []):
            errors.append('competing Markdown source: ' + old)
    readme_pointer = root/'README.md'
    if readme_pointer.is_file() and readme_pointer.read_text(encoding='utf-8').strip() != README_POINTER.strip():
        errors.append('README.md must remain the thin GitHub discovery pointer')
    for old in manifest.get('compatibility_pointers', []):
        if old not in manifest['mapping']:
            errors.append('compatibility pointer has no canonical mapping: ' + old)
            continue
        try:
            errors.extend(_compatibility_errors(root, old, manifest['mapping'][old]))
        except (OSError, UnicodeDecodeError, ValueError) as error:
            errors.append(f'invalid compatibility pointer {old}: {type(error).__name__}')
    for path in (root/'docs').rglob('*.md'):
        relative = path.relative_to(root).as_posix()
        if relative.startswith('docs/archive/') or relative == 'docs/providers/zai-welcome-aboard.md' or relative in manifest.get('compatibility_pointers', []):
            continue
        errors.append('new human-facing Markdown is not permitted: ' + relative)
    for path, page in pages.items():
        for link in page.links:
            parts = _url_parts(link)
            if parts.scheme or parts.netloc or parts.path.startswith('/'):
                continue
            target = (path.parent/unquote(parts.path)).resolve() if parts.path else path
            if not target.is_relative_to(root):
                continue  # Explicit external local artifact; not served by this library.
            if not target.exists():
                errors.append(f'{path.relative_to(root)}: missing local link {link}')
            elif parts.fragment and target in pages and unquote(parts.fragment) not in pages[target].ids:
                errors.append(f'{path.relative_to(root)}: missing anchor {link}')
    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    errors = check(args.root)
    for error in errors:
        print(error)
    print('HTML documentation valid' if not errors else f'{len(errors)} documentation errors')
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
