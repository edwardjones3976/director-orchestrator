"""Construct target-free, construction-only KEP inputs. No model or held-out access."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "train"))
import prompt_template

REPOSITORY = "kubernetes/enhancements"
COMMIT = "f7be055669b365e9b4ab3905bda6ef385e0c84ca"
PINS = {
    "archive": "da037ba8c8e68aa7033d256beff5da7cf3605b83678d46b0f6c403947c574618",
    "license": "b40930bbcf80744c86c46a12bc9da056641d722716c378f5659b9e555ef833e1",
    "tree": "91f07eb949f3a66ea8dd48022179a60bf8df3fe7cc529ee0fa53a663b46d3f9b",
    "census": "df00d70bc30e77bc2b77c1c147e45f046aff3c9a1cc286dce16aa8acd1736bca",
}
SUPPORT_SECTIONS = ("Non-Goals", "Notes/Constraints/Caveats", "Alpha Graduation Criteria", "Test Plan")
CONTEXT_SECTIONS = ("Summary", "Motivation", "Goals") + SUPPORT_SECTIONS
ORDER = "sig-round-robin/numeric-id-path/v1"
MAX_ARTIFACT = 2 * 1024 * 1024
MAX_SOURCE = 1024 * 1024
MAX_SOURCE_TOTAL = 64 * 1024 * 1024
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_MEMBERS = 20000
SECRET_MARKERS = ("_KEY=", "_TOKEN=", "_SECRET=", "DSN=", "PASSWORD=", "xoxb-", "xapp-")
RECORD_KEYS = frozenset({
    "protocol", "disposition", "source_group", "kep_number", "selection_rank", "source_ref",
    "source_sha256", "source_git_blob", "policy_sha256", "qualified_selection_sha256",
    "source_captured_at", "generated_at", "input", "title_span", "objective_span", "context_spans",
})


class Refusal(ValueError):
    """Only a fixed reason code is exposed to the operator."""
    def __init__(self, reason, summary=None):
        super().__init__(reason)
        self.summary = summary


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise Refusal("DUPLICATE_JSON_MEMBER")
            result[key] = value
        return result
    try:
        def invalid_constant(_value):
            raise Refusal("INVALID_JSON")
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (UnicodeError, json.JSONDecodeError, TypeError):
        raise Refusal("INVALID_JSON") from None


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def utc(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value):
        raise Refusal("INVALID_TIMESTAMP")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise Refusal("INVALID_TIMESTAMP") from None


def next_clock(clock, after):
    value = clock()
    if not utc(after) <= utc(value) <= datetime.now(timezone.utc):
        raise Refusal("INVALID_CHRONOLOGY")
    return value


def guard_path(path, reason="PROTECTED_PATH"):
    """Refuse protected components and ambiguous Win32 spellings before access."""
    for part in Path(path).parts:
        if part.endswith((".", " ")) or part.casefold() == "heldout":
            raise Refusal(reason)


def pinned_file(path, pin, limit=MAX_ARTIFACT):
    path = Path(path).absolute()
    guard_path(path)
    if not isinstance(pin, str) or re.fullmatch(r"[0-9a-f]{64}", pin) is None:
        raise Refusal("INVALID_PIN")
    try:
        raw = _read_artifact(path, limit)
    except OSError:
        raise Refusal("UNREADABLE_ARTIFACT") from None
    if len(raw) > limit or digest(raw) != pin:
        raise Refusal("ARTIFACT_PIN_MISMATCH")
    return raw


def safe_archive_name(name):
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        return False
    return not name.startswith("/") and all(part not in ("", ".", "..") for part in name.rstrip("/").split("/"))


def visible_lines(text):
    """Mask comments at original offsets; fence literals cannot open comments."""
    offset, in_comment, fence = 0, False, None
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not marker[2].strip():
                fence = None
            yield offset, line, True
            offset += len(line)
            continue
        if not in_comment and marker and not (marker[1][0] == "`" and "`" in marker[2]):
            fence = (marker[1][0], len(marker[1]))
            yield offset, line, True
            offset += len(line)
            continue
        visible = list(line)
        index = 0
        while index < len(line):
            if not in_comment and line.startswith("<!--", index):
                in_comment = True
            if in_comment:
                if line.startswith("-->", index):
                    for pos in range(index, min(index + 3, len(line))):
                        visible[pos] = " "
                    index += 3
                    in_comment = False
                    continue
                if visible[index] not in "\r\n":
                    visible[index] = " "
            index += 1
        clean = "".join(visible).rstrip("\r\n")
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", clean)
        if marker and not (marker[1][0] == "`" and "`" in marker[2]):
            fence = (marker[1][0], len(marker[1]))
            yield offset, "".join(visible), True
        else:
            yield offset, "".join(visible), False
        offset += len(line)


def heading_name(name):
    return re.sub(r"\s+\(optional\)$", "", name.casefold())


def source_sections(text):
    """Original Unicode offsets; comments and fenced-code headings are ignored."""
    headings = []
    for offset, visible, fenced in visible_lines(text):
        if not fenced:
            clean = visible.rstrip("\r\n")
            match = re.match(r"^ {0,3}(#{1,6})[ \t]+(.*)$", clean)
            if match:
                name = re.sub(r"[ \t]+#+[ \t]*$", "", match[2]).strip(" \t")
                if not name:
                    continue
                start = match.start(2) + len(match[2]) - len(match[2].lstrip(" \t"))
                end = start + len(name)
                # A title containing removed comment syntax is ambiguous, not rewritten.
                headings.append((len(match[1]), name, offset, offset + len(visible),
                                 {"start": offset + start, "end": offset + end}))
    sections = {}
    title = None
    mapping = {name.casefold(): name for name in CONTEXT_SECTIONS}
    for index, (level, name, start, body, title_span) in enumerate(headings):
        if level == 1 and title is None:
            if text[title_span["start"]:title_span["end"]] != name:
                raise Refusal("AMBIGUOUS_INPUT_TITLE")
            title = title_span
            continue  # The document title is not also an input section.
        key_name = heading_name(name)
        key = mapping.get(key_name)
        if key_name == "alpha":
            parent = next((head for head in reversed(headings[:index]) if head[0] < level), None)
            if parent and heading_name(parent[1]) == "graduation criteria":
                # A single original span cannot include the parent role without also
                # including an earlier sibling. Refuse instead of citing Beta as Alpha.
                if any(head[2] > parent[2] for head in headings[:index]):
                    raise Refusal("ALPHA_PRECEDING_SIBLING")
                key = "Alpha Graduation Criteria"
        if key:
            if key in sections:
                raise Refusal("AMBIGUOUS_SECTION")
            end = next((head[2] for head in headings[index + 1:] if head[0] <= level), len(text))
            # Context preserves the source role, including Alpha's parent role.
            context_start = parent[2] if key == "Alpha Graduation Criteria" and key_name == "alpha" else start
            sections[key] = {"start": context_start, "body_start": body, "end": end}
    return title, sections


def visibly_nonempty(text):
    fence = None
    for _, visible, fenced in visible_lines(text):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", visible.rstrip("\r\n"))
        if fenced and marker:
            if fence is None:
                fence = (marker[1][0], len(marker[1]))
                continue  # The info string is a delimiter, not section content.
            if marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not marker[2].strip():
                fence = None
                continue
        if any(char.isalnum() for char in visible):
            return True
    return False


class _WindowsDirectories:
    """Retained no-delete-sharing handles anchor every Windows path component."""
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes = ctypes
        class FileId(ctypes.Structure):
            _fields_ = [("volume", ctypes.c_ulonglong), ("identifier", ctypes.c_ubyte * 16)]
        class Attributes(ctypes.Structure):
            _fields_ = [("attributes", wintypes.DWORD), ("reparse_tag", wintypes.DWORD)]
        class UnicodeString(ctypes.Structure):
            _fields_ = [("length", wintypes.USHORT), ("maximum_length", wintypes.USHORT),
                        ("buffer", wintypes.LPWSTR)]
        class ObjectAttributes(ctypes.Structure):
            _fields_ = [("length", wintypes.ULONG), ("root", wintypes.HANDLE),
                        ("name", ctypes.POINTER(UnicodeString)), ("attributes", wintypes.ULONG),
                        ("security_descriptor", ctypes.c_void_p), ("security_qos", ctypes.c_void_p)]
        class StatusUnion(ctypes.Union):
            _fields_ = [("status", wintypes.LONG), ("pointer", ctypes.c_void_p)]
        class IoStatus(ctypes.Structure):
            _fields_ = [("status", StatusUnion), ("information", ctypes.c_size_t)]
        self.FileId, self.Attributes = FileId, Attributes
        self.UnicodeString, self.ObjectAttributes, self.IoStatus = UnicodeString, ObjectAttributes, IoStatus
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.create = kernel.CreateFileW
        self.create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        self.create.restype = wintypes.HANDLE
        self.info = kernel.GetFileInformationByHandleEx
        self.info.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.info.restype = wintypes.BOOL
        self.final_name = kernel.GetFinalPathNameByHandleW
        self.final_name.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        self.final_name.restype = wintypes.DWORD
        self.close = kernel.CloseHandle
        self.close.argtypes, self.close.restype = [wintypes.HANDLE], wintypes.BOOL
        self.disposition = kernel.SetFileInformationByHandle
        self.disposition.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.disposition.restype = wintypes.BOOL
        native = ctypes.WinDLL("ntdll")
        self.native_create = native.NtCreateFile
        self.native_create.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG,
            ctypes.POINTER(ObjectAttributes), ctypes.POINTER(IoStatus), ctypes.c_void_p,
            wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG]
        self.native_create.restype = wintypes.LONG
        self.native_error = native.RtlNtStatusToDosError
        self.native_error.argtypes, self.native_error.restype = [wintypes.LONG], wintypes.ULONG
        self.Handle = wintypes.HANDLE

    def open(self, path, *, sharing=3, access=0x80000000, creation=3, flags=0x02200000):
        guard_path(path)
        handle = self.create(str(path), access, sharing, None, creation, flags, None)
        if handle == self.ctypes.c_void_p(-1).value:
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        try:
            self.guard_handle_path(handle)
            return handle
        except BaseException:
            self.close(handle)
            raise

    def guard_handle_path(self, handle):
        """Check the normalized name of THIS acquired object, before content access.

        FILE_NAME_NORMALIZED | VOLUME_NAME_DOS (flags 0) expands short names.
        Microsoft documents a too-small result as the required size including NUL.
        No pathname resolution or reopened object is used as a weaker fallback.
        """
        size = 1024
        while size <= 32768:
            buffer = self.ctypes.create_unicode_buffer(size)
            count = self.final_name(handle, buffer, size, 0)
            if count == 0:
                raise self.ctypes.WinError(self.ctypes.get_last_error())
            if count < size:
                guard_path(buffer.value)
                return
            size = count + 1
        raise Refusal("PROTECTED_PATH")

    def identity(self, handle):
        file_id, attributes = self.FileId(), self.Attributes()
        if not self.info(handle, 18, self.ctypes.byref(file_id), self.ctypes.sizeof(file_id)) or not self.info(
                handle, 9, self.ctypes.byref(attributes), self.ctypes.sizeof(attributes)):
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        if not attributes.attributes & 0x10 or attributes.attributes & 0x400:
            raise Refusal("PROTECTED_OUTPUT_PATH")
        return file_id.volume, bytes(file_id.identifier)

    def snapshot(self, path):
        handle = self.open(path, sharing=7)
        try:
            return self.identity(handle)
        finally:
            self.close(handle)

    def open_relative(self, parent, name, *, directory):
        """Open one child of an acquired object; never follow its reparse point."""
        if not name or name in (".", "..") or any(char in name for char in "/\\:\0"):
            raise Refusal("PROTECTED_PATH")
        guard_path(name)
        buffer = self.ctypes.create_unicode_buffer(name)
        length = len(name.encode("utf-16-le"))
        if length + 2 > 65535:
            raise Refusal("PROTECTED_PATH")
        string = self.UnicodeString(length, length + 2, self.ctypes.cast(buffer, self.ctypes.c_wchar_p))
        attributes = self.ObjectAttributes(self.ctypes.sizeof(self.ObjectAttributes), parent,
            self.ctypes.pointer(string), 0x40, None, None)
        handle, status = self.Handle(), self.IoStatus()
        access = 0x00100081 if directory else 0x80100080
        options = 0x00200021 if directory else 0x00200060
        code = self.native_create(self.ctypes.byref(handle), access, self.ctypes.byref(attributes),
            self.ctypes.byref(status), None, 0, 3 if directory else 1, 1, options, None, 0)
        if code != 0:
            if handle.value:
                self.close(handle.value)
            raise self.ctypes.WinError(self.native_error(code))
        try:
            info = self.Attributes()
            if not self.info(handle.value, 9, self.ctypes.byref(info), self.ctypes.sizeof(info)):
                raise self.ctypes.WinError(self.ctypes.get_last_error())
            if info.attributes & 0x400 or bool(info.attributes & 0x10) != directory:
                raise Refusal("ARTIFACT_REPARSE")
            self.guard_handle_path(handle.value)
            return handle.value
        except BaseException:
            self.close(handle.value)
            raise

    def delete_owned(self, handle):
        # FILE_DISPOSITION_INFO contains a one-byte BOOLEAN, not a Win32 BOOL.
        value = self.ctypes.c_ubyte(1)
        if not self.disposition(handle, 4, self.ctypes.byref(value), self.ctypes.sizeof(value)):
            raise self.ctypes.WinError(self.ctypes.get_last_error())

    def create_directory(self, parent, name):
        """FILE_CREATE returns the birth handle; never mkdir then authenticate a pathname."""
        if not parent or not name or name in (".", "..") or any(char in name for char in "\\/:\0"):
            raise Refusal("PROTECTED_OUTPUT_PATH")
        guard_path(name, "PROTECTED_OUTPUT_PATH")
        buffer = self.ctypes.create_unicode_buffer(name)
        length = len(name.encode("utf-16-le"))
        if length + 2 > 65535:
            raise Refusal("PROTECTED_OUTPUT_PATH")
        string = self.UnicodeString(length, length + 2, self.ctypes.cast(buffer, self.ctypes.c_wchar_p))
        attributes = self.ObjectAttributes(self.ctypes.sizeof(self.ObjectAttributes), parent,
            self.ctypes.pointer(string), 0x40, None, None)  # OBJ_CASE_INSENSITIVE, relative RootDirectory.
        handle, status = self.Handle(), self.IoStatus()
        # SYNCHRONIZE | FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES, share read/write only;
        # FILE_CREATE, DIRECTORY_FILE | SYNCHRONOUS_IO_NONALERT | OPEN_REPARSE_POINT.
        code = self.native_create(self.ctypes.byref(handle), 0x00100081, self.ctypes.byref(attributes),
            self.ctypes.byref(status), None, 0x10, 3, 2, 0x00200021, None, 0)
        if code != 0:
            if handle.value:
                self.close(handle.value)
            raise self.ctypes.WinError(self.native_error(code))
        if not handle.value or status.information != 2:  # FILE_CREATED, not FILE_OPENED.
            if handle.value:
                self.close(handle.value)
            raise Refusal("OUTPUT_BIRTH_NOT_CREATED")
        try:
            self.guard_handle_path(handle.value)
            return handle.value
        except BaseException:
            self.close(handle.value)
            raise


def _read_artifact(path, limit):
    """Read through held no-follow objects; hashes authenticate the resulting bytes."""
    if os.name != "nt":
        raise Refusal("ARTIFACT_PLATFORM_UNQUALIFIED")
    import msvcrt
    windows, directories, leaf, fd = _WindowsDirectories(), [], None, None
    try:
        parent = windows.open(Path(path.anchor))
        directories.append(parent)
        windows.identity(parent)
        for name in path.parts[1:-1]:
            parent = windows.open_relative(parent, name, directory=True)
            directories.append(parent)
        leaf = windows.open_relative(parent, path.name, directory=False)
        fd = msvcrt.open_osfhandle(leaf, os.O_RDONLY | os.O_BINARY)
        leaf = None  # fd owns this same held leaf object until after the read.
        chunks, remaining = [], limit + 1
        while remaining:
            chunk = os.read(fd, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    finally:
        if fd is not None:
            os.close(fd)
        if leaf is not None:
            windows.close(leaf)
        for handle in reversed(directories):
            windows.close(handle)


class _Publication:
    """Publish through acquired directory objects, never a mutable output pathname."""
    def __init__(self, out):
        self.out = out
        self.directories, self.files = [], []
        self.windows = _WindowsDirectories() if os.name == "nt" else None
        self.output_handle = None

    def _snapshot(self, path, parent=None):
        return self.windows.snapshot(path)

    def _take_directory(self, path, parent, expected):
        handle = None
        try:
            handle = self.windows.open(path)  # FILE_SHARE_DELETE deliberately absent.
            identity = self.windows.identity(handle)
            if identity != expected:
                raise Refusal("OUTPUT_PATH_RACE")
            self.directories.append(handle)
            return handle
        except BaseException:
            if handle is not None:
                self.windows.close(handle)
            raise

    def _create_directory(self, path, parent):
        handle = self.windows.create_directory(parent, path.name)
        try:
            self.windows.identity(handle)  # Validate the returned birth object, not an output-path snapshot.
            self.directories.append(handle)
            return handle
        except BaseException:
            self.windows.close(handle)
            raise

    def __enter__(self):
        try:
            if self.windows is None:
                raise Refusal("OUTPUT_PLATFORM_UNQUALIFIED")
            guard_path(self.out, "PROTECTED_OUTPUT_PATH")
            chain = list(reversed(self.out.parent.parents)) + [self.out.parent]
            snapshots = {}
            for path in chain:
                try:
                    snapshots[path] = self._snapshot(path)
                except FileNotFoundError:
                    snapshots[path] = None
            parent = None
            for path in chain:
                expected = snapshots[path]
                if expected is None:
                    try:
                        parent = self._create_directory(path, parent)
                    except FileExistsError:
                        raise Refusal("OUTPUT_PATH_RACE") from None
                else:
                    parent = self._take_directory(path, parent, expected)
            try:
                self.output_handle = self._create_directory(self.out, parent)
            except FileExistsError:
                raise Refusal("OUTPUT_EXISTS") from None
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _write_temp(self, raw):
        name = ".kep-" + os.urandom(16).hex()
        import msvcrt
        handle = self.windows.open(self.out / name, sharing=1, access=0x40010000, creation=1, flags=0x00200080)
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
        except BaseException:
            self.windows.close(handle)
            raise
        self.files.append((name, fd))
        remaining = memoryview(raw)
        while remaining:
            count = os.write(fd, remaining)
            if count <= 0:
                raise OSError("short publication write")
            remaining = remaining[count:]
        os.fsync(fd)
        return name

    def publish(self, data, manifest):
        for final, raw in (("inputs.jsonl", data), ("manifest.json", manifest)):
            staged = self._write_temp(raw)
            # All ancestor/destination and staging-file objects deny rename/delete.
            os.link(self.out / staged, self.out / final)
            # The complete manifest is linked last, never exposed while being written.

    def __exit__(self, *_exc):
        for name, fd in reversed(self.files):
            try:
                import msvcrt
                self.windows.delete_owned(msvcrt.get_osfhandle(fd))
            except OSError:
                pass  # Preserve the owned temporary; never delete by a mutable pathname.
            finally:
                os.close(fd)
        self.files.clear()
        for handle in reversed(self.directories):
            self.windows.close(handle)
        self.directories.clear()


def metadata_identity(raw):
    """Inspect YAML nodes only; no constructors, implicit dates or merge keys."""
    try:
        node = yaml.compose(raw.decode("utf-8"), Loader=yaml.BaseLoader)
        if not isinstance(node, yaml.MappingNode):
            raise Refusal("INVALID_METADATA")
        fields = {}
        for key, value in node.value:
            if not isinstance(key, yaml.ScalarNode) or key.value in fields or key.value == "<<":
                raise Refusal("INVALID_METADATA")
            fields[key.value] = value
        number, status = fields.get("kep-number"), fields.get("status")
        if any(not isinstance(value, yaml.ScalarNode) or value.style in ("|", ">") for value in (number, status)):
            raise Refusal("INVALID_METADATA")
        if re.fullmatch(r"[0-9]+", number.value) is None:
            raise Refusal("INVALID_METADATA")
        return int(number.value), status.value
    except (UnicodeError, yaml.YAMLError, RecursionError):
        raise Refusal("INVALID_METADATA") from None


def trim_span(text, span):
    raw = text[span["start"]:span["end"]]
    leading = len(raw) - len(raw.lstrip())
    trailing = len(raw) - len(raw.rstrip())
    return {"start": span["start"] + leading, "end": span["end"] - trailing}


def validate_record(record, text):
    if set(record) != RECORD_KEYS or record["protocol"] != "kep-input/v1" or record["disposition"] != "construction_only":
        raise Refusal("INVALID_INPUT_RECORD")
    if type(record["kep_number"]) is not int or record["kep_number"] < 1 or type(record["selection_rank"]) is not int or record["selection_rank"] < 0 or record["source_group"] != f"licensed/{REPOSITORY}/kep-{record['kep_number']}":
        raise Refusal("INVALID_INPUT_IDENTITY")
    for key in ("source_sha256", "policy_sha256", "qualified_selection_sha256"):
        if not isinstance(record[key], str) or re.fullmatch(r"[0-9a-f]{64}", record[key]) is None:
            raise Refusal("INVALID_INPUT_IDENTITY")
    raw = text.encode("utf-8")
    blob = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
    if not safe_archive_name(record["source_ref"]) or record["source_sha256"] != digest(raw) or record["source_git_blob"] != blob:
        raise Refusal("SOURCE_PIN_MISMATCH")
    item = record["input"]
    if not isinstance(item, dict) or set(item) != {"objective", "repository", "title", "context"}:
        raise Refusal("INVALID_INPUT_SHAPE")
    if prompt_template.input_contract(item) != prompt_template.INPUT_CONTRACT_CONTEXT or prompt_template.context_shape_errors(item["context"]):
        raise Refusal("INVALID_INPUT_SHAPE")
    if item["repository"] != REPOSITORY or any(not isinstance(item[key], str) or not item[key].strip() for key in ("objective", "title")):
        raise Refusal("INVALID_INPUT_SHAPE")
    def excerpt(span):
        if not isinstance(span, dict) or set(span) != {"start", "end"} or any(type(span[key]) is not int for key in span):
            raise Refusal("INVALID_SPAN")
        if not 0 <= span["start"] < span["end"] <= len(text):
            raise Refusal("INVALID_SPAN")
        return text[span["start"]:span["end"]]
    if item["title"] != excerpt(record["title_span"]) or item["objective"] != excerpt(record["objective_span"]):
        raise Refusal("INPUT_SPAN_MISMATCH")
    spans = record["context_spans"]
    if len(spans) != len(item["context"]):
        raise Refusal("CONTEXT_SPAN_MISMATCH")
    for block, span in zip(item["context"], spans):
        if block["kind"] != "assignment_packet" or block["source_ref"] != record["source_ref"] or block["source_sha256"] != record["source_sha256"] or block["text"] != excerpt(span):
            raise Refusal("CONTEXT_SPAN_MISMATCH")
    if not utc(record["source_captured_at"]) <= utc(record["generated_at"]) <= datetime.now(timezone.utc):
        raise Refusal("INVALID_CHRONOLOGY")
    if any(marker in json.dumps(record, ensure_ascii=False) for marker in SECRET_MARKERS):
        raise Refusal("SECRET_PATTERN")


def prepare(*, archive, tree, census, license_file, policy, policy_sha256, out, dry_run=False,
            clock=utc_now, _pins=None):
    """_pins injects only synthetic fixture identities; the CLI uses frozen PINS."""
    pins = PINS if _pins is None else _pins
    out = Path(out).absolute()
    guard_path(out, "PROTECTED_OUTPUT_PATH")
    if any(parent.is_symlink() or parent.is_junction() for parent in (out, *out.parents)):
        raise Refusal("PROTECTED_OUTPUT_PATH")
    if not dry_run and (out.exists() or out.is_symlink()):
        raise Refusal("OUTPUT_EXISTS")
    raw_archive = pinned_file(archive, pins["archive"], MAX_ARCHIVE)
    raw_tree = pinned_file(tree, pins["tree"])
    raw_census = pinned_file(census, pins["census"])
    pinned_file(license_file, pins["license"])
    raw_policy = pinned_file(policy, policy_sha256, 65536)
    rule = strict_json(raw_policy)
    policy_keys = {"protocol", "repository", "commit", "statuses", "creation_date_filter", "required_sections", "order", "frozen_at", "authority"}
    if not isinstance(rule, dict) or set(rule) != policy_keys or rule.get("authority") != "issue299/comment5973258630" or rule.get("protocol") != "kep-input-selection/v2" or rule.get("repository") != REPOSITORY or rule.get("commit") != COMMIT or rule.get("statuses") != ["implemented", "implementable"] or rule.get("creation_date_filter") is not None or rule.get("required_sections") != list(SUPPORT_SECTIONS) or rule.get("order") != ORDER:
        raise Refusal("INVALID_POLICY")
    frozen_at = rule.get("frozen_at")
    utc(frozen_at)
    started_at = next_clock(clock, frozen_at)
    if utc(started_at) <= utc(frozen_at):
        raise Refusal("INVALID_CHRONOLOGY")
    metadata, upstream = strict_json(raw_census), strict_json(raw_tree)
    if not isinstance(metadata, dict) or metadata.get("repo") != REPOSITORY or metadata.get("commit") != COMMIT or metadata.get("archive_sha256") != pins["archive"] or not isinstance(metadata.get("items"), list):
        raise Refusal("INVALID_CENSUS")
    if not isinstance(upstream, dict) or upstream.get("truncated") is not False or not isinstance(upstream.get("tree"), list):
        raise Refusal("INVALID_TREE")
    blobs = {}
    for entry in upstream["tree"]:
        if not isinstance(entry, dict) or not safe_archive_name(entry.get("path")):
            raise Refusal("INVALID_TREE")
        if entry.get("type") == "blob":
            if entry["path"] in blobs or re.fullmatch(r"[0-9a-f]{40}", str(entry.get("sha"))) is None:
                raise Refusal("INVALID_TREE")
            blobs[entry["path"]] = entry["sha"]
    decisions, eligible, seen_paths, seen_ids = [], [], set(), set()
    for entry in metadata["items"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("status"), str):
            raise Refusal("INVALID_CENSUS")
        path, sig = entry.get("path"), entry.get("sig")
        # The legacy census field "sig" stores the original namespace, including
        # historical provider-* groups; retain it as the round-robin bucket.
        if not isinstance(path, str) or not safe_archive_name(path) or re.fullmatch(r"keps/(?:sig|provider)-[a-z0-9-]+/[A-Za-z0-9._-]+/kep\.yaml", path) is None or sig != path.split("/")[1] or path in seen_paths:
            raise Refusal("INVALID_CENSUS")
        seen_paths.add(path)
        decision = {"path": path, "sig": sig, "status": entry.get("status"), "kep_number": None,
                    "selection_rank": None, "section_qualified": False, "disposition": "refused", "reason": None}
        decisions.append(decision)
        number = entry.get("kep_id")
        if type(number) is int:
            number = str(number)
        directory_number = re.match(r"([0-9]+)(?:-|$)", path.split("/")[2])
        if not isinstance(number, str) or not number.isascii() or not number.isdigit():
            raise Refusal("CENSUS_IDENTITY_MISMATCH")
        number = int(number)
        decision["kep_number"] = number  # Retained for every authenticated census entry.
        if re.fullmatch(r"[0-9a-f]{64}", str(entry.get("metadata_sha256"))) is None:
            raise Refusal("INVALID_CENSUS")
        # Census/YAML identity is authoritative. Directory disagreements are
        # counted refusals, not identities to repair or a batch-wide veto.
        if entry.get("status") in ("implemented", "implementable") and number > 0:
            if number in seen_ids:
                raise Refusal("DUPLICATE_KEP_ID")
            seen_ids.add(number)
        if directory_number is None or int(directory_number[1]) != number:
            decision["kep_number"] = number
            decision["reason"] = "DIRECTORY_ID_MISMATCH"
            continue
        if entry.get("status") not in ("implemented", "implementable"):
            decision["reason"] = "INELIGIBLE_STATUS"
            continue
        if number < 1:
            decision["reason"] = "NONPOSITIVE_ID"
            continue
        decision["kep_number"] = number
        eligible.append((entry, decision))
    buckets = defaultdict(list)
    for pair in eligible:
        buckets[pair[0]["sig"]].append(pair)
    for bucket in buckets.values():
        bucket.sort(key=lambda pair: (pair[1]["kep_number"], pair[0]["path"]))
    ordered = [buckets[sig][index] for index in range(max((len(v) for v in buckets.values()), default=0))
               for sig in sorted(buckets) if index < len(buckets[sig])]
    for rank, (_, decision) in enumerate(ordered):
        decision["selection_rank"] = rank
    order_hash = digest(json_bytes([{key: decision[key] for key in ("path", "sig", "kep_number", "selection_rank")} for _, decision in ordered]))
    prepared, source_total = [], 0
    prefix = "enhancements-" + COMMIT + "/"
    last_time = started_at
    with tarfile.open(fileobj=io.BytesIO(raw_archive), mode="r:gz") as tar:
        members = {}
        for member in tar:
            if len(members) >= MAX_MEMBERS or not safe_archive_name(member.name) or not (member.name == prefix.rstrip("/") or member.name.startswith(prefix)) or member.name in members or member.issym() or member.islnk():
                raise Refusal("INVALID_ARCHIVE_MEMBER")
            members[member.name] = member
        def read_member(path):
            member = members.get(prefix + path)
            if member is None or not member.isfile() or not 0 <= member.size <= MAX_SOURCE:
                raise Refusal("NO_BOUNDED_REGULAR_SOURCE")
            handle = tar.extractfile(member)
            if handle is None:
                raise Refusal("NO_BOUNDED_REGULAR_SOURCE")
            with handle:
                raw = handle.read(MAX_SOURCE + 1)
            if len(raw) != member.size or len(raw) > MAX_SOURCE:
                raise Refusal("NO_BOUNDED_REGULAR_SOURCE")
            blob = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
            if blobs.get(path) != blob:
                raise Refusal("SOURCE_GIT_BLOB_MISMATCH")
            return raw, blob
        archive_license, _ = read_member("LICENSE")
        if digest(archive_license) != pins["license"]:
            raise Refusal("LICENSE_MISMATCH")
        # Authenticate census semantics for every entry before reading bodies;
        # a false ineligible status must not silently remove a pinned source.
        for entry in metadata["items"]:
            metadata_raw, _ = read_member(entry["path"])
            if digest(metadata_raw) != entry["metadata_sha256"]:
                raise Refusal("METADATA_PIN_MISMATCH")
            number, status = metadata_identity(metadata_raw)
            if number != int(entry["kep_id"]) or status != entry["status"]:
                raise Refusal("CENSUS_METADATA_MISMATCH")
        for entry, decision in ordered:
            try:
                path = str(PurePosixPath(entry["path"]).parent / "README.md")
                raw, blob = read_member(path)
                source_total += len(raw)
                if source_total > MAX_SOURCE_TOTAL:
                    raise Refusal("CORPUS_SIZE_BOUND")
                text = raw.decode("utf-8")
                captured = next_clock(clock, last_time)
                last_time = captured
                title_span, sections = source_sections(text)
                if any(name not in sections or not visibly_nonempty(text[sections[name]["body_start"]:sections[name]["end"]]) for name in SUPPORT_SECTIONS):
                    raise Refusal("MISSING_OR_EMPTY_SUPPORT_SECTION")
                decision["section_qualified"] = True
                if title_span is None:
                    raise Refusal("INPUT_TITLE_MISSING")
                if "Goals" not in sections or not visibly_nonempty(text[sections["Goals"]["body_start"]:sections["Goals"]["end"]]):
                    raise Refusal("INPUT_GOALS_MISSING")
                objective_span = trim_span(text, {"start": sections["Goals"]["body_start"], "end": sections["Goals"]["end"]})
                spans = sorted(({"start": sections[name]["start"], "end": sections[name]["end"]} for name in CONTEXT_SECTIONS if name in sections and visibly_nonempty(text[sections[name]["body_start"]:sections[name]["end"]])), key=lambda span: span["start"])
                source_sha = digest(raw)
                item = {"objective": text[objective_span["start"]:objective_span["end"]], "title": text[title_span["start"]:title_span["end"]], "repository": REPOSITORY,
                        "context": [{"kind": "assignment_packet", "source_ref": path, "source_sha256": source_sha, "text": text[span["start"]:span["end"]]} for span in spans]}
                if any(marker in json.dumps(item, ensure_ascii=False) for marker in SECRET_MARKERS):
                    raise Refusal("SECRET_PATTERN")
                prepared.append({"decision": decision, "text": text, "item": item, "spans": spans, "title_span": title_span, "objective_span": objective_span, "captured": captured, "source_ref": path, "source_sha": source_sha, "blob": blob})
                decision["disposition"], decision["reason"] = "construction_only", None
            except UnicodeError:
                decision["reason"] = "INVALID_UTF8"
            except Refusal as exc:
                if str(exc) in {"SOURCE_GIT_BLOB_MISMATCH", "METADATA_PIN_MISMATCH", "CORPUS_SIZE_BOUND", "INVALID_CHRONOLOGY"}:
                    raise
                decision["reason"] = str(exc)
    decisions.sort(key=lambda item: item["path"])
    qualification = {"protocol": "kep-input-qualification/v2", "policy_sha256": policy_sha256, "order_sha256": order_hash,
                     "qualified_at": next_clock(clock, last_time), "census": decisions}
    qualification_sha = digest(json_bytes(qualification))
    last_time = qualification["qualified_at"]
    records = []
    for source in prepared:
        decision = source["decision"]
        generated = next_clock(clock, last_time)
        last_time = generated
        record = {"protocol": "kep-input/v1", "disposition": "construction_only", "source_group": f"licensed/{REPOSITORY}/kep-{decision['kep_number']}",
                  "kep_number": decision["kep_number"], "selection_rank": decision["selection_rank"], "source_ref": source["source_ref"],
                  "source_sha256": source["source_sha"], "source_git_blob": source["blob"], "policy_sha256": policy_sha256,
                  "qualified_selection_sha256": qualification_sha, "source_captured_at": source["captured"], "generated_at": generated,
                  "input": source["item"], "title_span": source["title_span"], "objective_span": source["objective_span"], "context_spans": source["spans"]}
        validate_record(record, source["text"])
        records.append(record)
    summary = {"metadata_candidates": len(decisions), "status_candidates": sum(item["status"] in ("implemented", "implementable") for item in decisions),
               "positive_unique_ids": len(ordered), "section_qualified": sum(item["section_qualified"] for item in decisions),
               "usable_inputs": len(records), "refused": len(decisions) - len(records), "refused_by_reason": dict(sorted(Counter(item["reason"] for item in decisions if item["reason"]).items()))}
    if not records:
        raise Refusal("NO_USABLE_INPUTS", summary)
    payloads = [json_bytes(record) + b"\n" for record in records]
    data = b"".join(payloads)
    manifest = {"protocol": "kep-input-manifest/v1", "disposition": "construction_only", "repository": REPOSITORY, "commit": COMMIT,
                "pins": dict(pins), "policy_sha256": policy_sha256, "policy": rule, "policy_frozen_at": frozen_at,
                "construction_started_at": started_at, "recorded_at": next_clock(clock, last_time), "order_sha256": order_hash,
                "qualified_selection_sha256": qualification_sha, "qualification": qualification, "summary": summary,
                "inputs_sha256": digest(data), "records": [{"source_group": record["source_group"], "selection_rank": record["selection_rank"], "sha256": digest(payload)} for record, payload in zip(records, payloads)]}
    if dry_run:
        return {"completion_status": "not_completed", "dry_run": True, **summary}
    manifest_data = json_bytes(manifest) + b"\n"
    with _Publication(out) as publication:
        publication.publish(data, manifest_data)
    # These hashes bind the bytes supplied to successful publication. Authenticity
    # comes from the external host receipt, never this caller-visible dictionary.
    return {"protocol": "kep-input-completion/v1", "completion_status": "completed",
            "dry_run": False, "inputs_sha256": digest(data),
            "manifest_sha256": digest(manifest_data), "policy_sha256": policy_sha256, **summary}


def main(argv=None):
    class Parser(argparse.ArgumentParser):
        def error(self, _message):
            self.exit(2, '{"refused": "USAGE_ERROR"}\n')
    parser = Parser(description=__doc__)
    for name in ("archive", "tree", "census", "license", "policy", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--policy-sha256", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = vars(parser.parse_args(argv))
    args["license_file"] = args.pop("license")
    try:
        result = prepare(**args)
    except Refusal as exc:
        print(json.dumps({"refused": str(exc), **({"summary": exc.summary} if exc.summary is not None else {})}), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, AttributeError, tarfile.TarError):
        print(json.dumps({"refused": "INVALID_CONSTRUCTION_EVIDENCE"}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
