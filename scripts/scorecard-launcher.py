# [[OpenAI Codex | GPT-6 | 2026-09-27 | 14:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 14:10 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 02:11 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 19:58 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 19:06 UTC | BEGIN]]
"""Owner-installed launcher for the twenty-check scorecard (review 4112412081).

This repository copy is only the reviewed source. The owner copies it from the reviewed evaluator commit to
``~/.director-scorecard/launcher.py`` and runs THAT copy, isolated from the checkout:

    python -I ~/.director-scorecard/launcher.py <repository> --candidate <sha> --output-dir <folder> [flags]

It reads ``evaluator_sha`` from ``~/.director-scorecard/release-descriptor.json``, requires that commit on
github/main in <repository>, extracts exactly that commit's tree with ``git archive`` into a fresh temporary
directory, and runs the archived ``scripts/director-scorecard.py`` in the same isolated interpreter,
adding only the archived ``src`` to its installed standard-library and site-package paths.
This launch executes no working-checkout code. Candidates can execute their own copies;
only reports verified against the owner-local receipt count. Git itself is trusted. Standard library only.
"""
import argparse
import hashlib
import importlib.util
import io
import json
import os
import secrets
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

DESCRIPTOR_VERSION = "director-cli-release-descriptor/v1"
OWNER_HOME_ENV = "DIRECTOR_SCORECARD_OWNER_HOME"  # the account home this launcher resolved, handed to its evaluator
DESCRIPTOR_ENV = "DIRECTOR_SCORECARD_DESCRIPTOR_SHA256"  # digest of the exact descriptor bytes this launch pinned
TREE_ENV = "DIRECTOR_SCORECARD_TREE"  # the archived evaluator tree the scorecard must be running from
REPO_ENV = "DIRECTOR_SCORECARD_REPO"  # the repository git reads candidate and evaluator commits from
SHA_ENV = "DIRECTOR_SCORECARD_EVALUATOR_SHA"  # the exact pin the tree was extracted from; the child must still see it
REFUSAL = "scorecard not run from the pinned evaluator checkout"
UNATTESTED = "UNATTESTED: not produced by the owner-installed launcher; no check may be relied on"
NONCE_ENV = "DIRECTOR_SCORECARD_NONCE"


def account_home() -> Path:
    """The account's profile directory from the operating system, never HOME or USERPROFILE (#287 item 34):
    a caller-controlled environment must not be able to relocate the owner trust root."""
    if os.name == "nt":
        import ctypes
        import uuid
        folder = (ctypes.c_byte * 16).from_buffer_copy(
            uuid.UUID("5E6C858F-0E22-4760-9AFE-EA3317B67173").bytes_le)  # FOLDERID_Profile
        path = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(folder), 0, None, ctypes.byref(path)) != 0:
            raise OSError("the account profile folder could not be resolved")
        try:
            return Path(path.value)
        finally:
            ctypes.windll.ole32.CoTaskMemFree(path)
    import pwd
    try:
        home = pwd.getpwuid(os.geteuid()).pw_dir
    except KeyError as error:  # an effective UID with no passwd/NSS entry (arbitrary-UID containers)
        raise OSError("the account has no passwd entry") from error
    if not home or not os.path.isabs(home):
        raise OSError("the account home is not an absolute path")
    return Path(home)


def owner_root() -> Path:
    return account_home() / ".director-scorecard"


def release_descriptor() -> Path:
    return owner_root() / "release-descriptor.json"


def read_descriptor(descriptor: Path) -> tuple[str, dict] | None:
    """(sha256 of the exact bytes, parsed document), or None: absent, a link, oversized, malformed or another
    version. The digest binds a launch to the whole descriptor, not only its evaluator pin (#287 item 33)."""
    try:
        if descriptor.is_symlink() or not descriptor.is_file() or descriptor.stat().st_size > 1_048_576:
            return None
        raw = descriptor.read_bytes()
        value = json.loads(raw)
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("version") != DESCRIPTOR_VERSION:
        return None
    return hashlib.sha256(raw).hexdigest(), value


def _pin(value: dict) -> str | None:
    pin = value.get("evaluator_sha")
    return pin if isinstance(pin, str) and len(pin) == 40 and all(c in "0123456789abcdef" for c in pin) else None


def report_digest(report: dict) -> str:
    return hashlib.sha256(json.dumps(report, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _receipt_path(nonce: str, root: Path | None = None) -> Path:
    if not isinstance(nonce, str) or len(nonce) != 64 or any(c not in "0123456789abcdef" for c in nonce):
        raise ValueError("invalid attestation nonce")
    return (root or owner_root()) / "attestations" / (nonce + ".json")


def _evaluator_root() -> Path:
    """Inside the evaluator a launch started: always the OS-resolved owner root (#287 item 34), the identical
    lookup the installed launcher itself performs. A forwarded DIRECTOR_SCORECARD_OWNER_HOME is never a source of
    this root -- a caller-controlled environment must not be able to relocate the owner trust root merely by also
    presenting a descriptor digest. A run without the launcher's descriptor digest is unattested in any case."""
    return owner_root()


def read_attestation(nonce: str, root: Path | None = None) -> dict | None:
    """Owner-local receipt under the OS-resolved owner root (or the root a launch handed its evaluator), never a
    path supplied by the candidate. The receipt must name the digest of the descriptor installed now.

    The owner installation and receipt directory are the trust boundary. As with the
    descriptor, an actor able to rewrite those owner files is outside this boundary.
    """
    try:
        path = _receipt_path(nonce, root)
        installed = path.parent.parent / "launcher.py"
        if any(p.is_symlink() for p in (path, path.parent, path.parent.parent, installed)):
            return None
        if path.stat().st_size > 1_048_576:
            return None
        receipt = json.loads(path.read_bytes())
        fields = receipt["attestation"]
        read = read_descriptor(installed.parent / "release-descriptor.json")
        pin = _pin(read[1]) if read else None
        if (pin is None or fields["nonce"] != nonce or fields["evaluator_sha"] != pin or
                fields["descriptor_sha256"] != read[0] or
                fields["launcher_sha256"] != hashlib.sha256(installed.read_bytes()).hexdigest() or
                not isinstance(fields["tree_id"], str) or len(fields["tree_id"]) != 40 or
                any(c not in "0123456789abcdef" for c in fields["tree_id"])):
            return None
        return receipt
    except (OSError, ValueError, KeyError, TypeError):
        return None


def run_attestation(root: Path) -> dict | None:
    try:
        receipt = read_attestation(os.environ.get(NONCE_ENV, ""), _evaluator_root())
    except OSError:
        return None
    if receipt is None or receipt.get("report_sha256") is not None:
        return None
    try:
        if (Path(receipt["tree"]).resolve() != root.resolve() or
                receipt["attestation"]["evaluator_sha"] != os.environ.get(SHA_ENV) or
                receipt["attestation"]["descriptor_sha256"] != os.environ.get(DESCRIPTOR_ENV)):
            return None
    except (KeyError, TypeError, OSError):
        return None
    return receipt["attestation"]


def report_is_attested(report: dict) -> bool:
    """All saved-report consumers must verify the completed receipt, not a JSON boolean."""
    try:
        if report.get("attested") is not True:
            return False
        receipt = read_attestation(report["attestation"]["nonce"])
        return bool(receipt and receipt["attestation"] == report["attestation"] and
                    receipt.get("report_sha256") == report_digest(report))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def pinned_evaluator(descriptor: Path | None = None) -> str | None:
    """The descriptor's evaluator_sha (40 lowercase hex), or None: absent, a link, oversized, malformed, another
    version or no pin."""
    read = read_descriptor(descriptor or release_descriptor())
    return _pin(read[1]) if read else None


def _git_environment() -> dict[str, str]:
    # Standalone bootstrap: never import a helper from the not-yet-trusted checkout.
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR"}}
    env.update(GIT_NO_REPLACE_OBJECTS="1", GIT_GRAFT_FILE=os.devnull,
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return env


def _git(repo: Path, *arguments: str) -> bytes:
    return subprocess.run(["git", "--no-replace-objects", "-C", str(repo), *arguments],
                          env=_git_environment(), capture_output=True, timeout=120, check=True,
                          stdin=subprocess.DEVNULL).stdout


def _reject_git_metadata(repo: Path) -> None:
    if _git(repo, "for-each-ref", "--format=%(refname)", "refs/replace/").strip():
        raise ValueError("replace refs are not permitted")
    common = os.fsdecode(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")).strip()
    if not common or not Path(common).is_absolute():
        raise ValueError("Git common directory could not be verified")
    try:
        (Path(common) / "info" / "grafts").lstat()
    except FileNotFoundError:
        return
    raise ValueError("grafts metadata is not permitted")


def _publish_html(tree: Path, report: dict, arguments: list[str]) -> None:
    """Publish the human view from the archived renderer after JSON sealing.

    The output selector is the user's CLI argument, never a path in the report.
    JSON/receipt verification stays unchanged; HTML is not an evidence format.
    Legacy evaluators without an HTML renderer remain runnable at their exact
    owner pin and are not modified by a newer installed launcher.
    """
    renderer = tree / "src" / "director_orchestrator" / "scorecard_html.py"
    if not renderer.is_file():
        return
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--output-dir", type=Path)
    output, _ = parser.parse_known_args(arguments)
    if output.output_dir is None:
        return
    json_path = output.output_dir / "completion-scorecard.json"
    saved = json.loads(json_path.read_bytes())
    if not report_is_attested(saved) or report_digest(saved) != report_digest(report):
        raise ValueError("the saved JSON does not match the sealed report")
    spec = importlib.util.spec_from_file_location("scorecard_html", renderer)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    document = module.render_html(saved)
    if not report_is_attested(saved):
        raise ValueError("the completed JSON receipt no longer verifies")
    destination = output.output_dir / "completion-scorecard.html"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=output.output_dir, prefix=".scorecard-", suffix=".html", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(document)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def launch(repo: Path, arguments: list[str], descriptor: Path | None = None) -> int:
    descriptor = descriptor or release_descriptor()
    read = read_descriptor(descriptor)
    pin = _pin(read[1]) if read else None
    if pin is None:
        return _refuse("evaluator revision not pinned")
    descriptor_sha256 = read[0]
    try:
        _reject_git_metadata(repo)
    except ValueError as error:
        return _refuse(str(error))
    except (OSError, subprocess.SubprocessError):
        return _refuse("Git replace/graft metadata could not be verified")
    try:
        _git(repo, "merge-base", "--is-ancestor", pin, "github/main")
    except (OSError, subprocess.SubprocessError):
        return _refuse("the pinned evaluator revision is not on github/main")
    here = Path.cwd().resolve()
    if here == repo or repo in here.parents:
        return _refuse("run the launcher from a working directory outside the checkout")
    with tempfile.TemporaryDirectory(prefix="director-scorecard-") as folder:
        tree = Path(folder) / "tree"
        try:
            tree_id = _git(repo, "rev-parse", "--verify", f"{pin}^{{tree}}").decode("ascii").strip()
            if len(tree_id) != 40 or any(c not in "0123456789abcdef" for c in tree_id):
                raise ValueError("invalid evaluator tree id")
            archive = _git(repo, "archive", "--format=tar", tree_id)
            with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
                bundle.extractall(tree, filter="data")
        except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError):
            return _refuse("the pinned evaluator tree could not be extracted")
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith("PYTHON")}
        env.update({TREE_ENV: str(tree), REPO_ENV: str(repo), SHA_ENV: pin,
                    OWNER_HOME_ENV: str(owner_root().parent), DESCRIPTOR_ENV: descriptor_sha256})
        nonce = secrets.token_hex(32)
        receipt_path = _receipt_path(nonce)
        installed = receipt_path.parent.parent / "launcher.py"
        if Path(__file__).resolve() != installed.resolve() or installed.is_symlink():
            return _refuse("use the owner-installed launcher")
        receipt_path.parent.mkdir(exist_ok=True)
        if receipt_path.parent.is_symlink() or installed.parent.is_symlink():
            return _refuse("owner attestation directory must not be a link")
        attestation = {"evaluator_sha": pin, "descriptor_sha256": descriptor_sha256, "tree_id": tree_id,
                       "launcher_sha256": hashlib.sha256(installed.read_bytes()).hexdigest(), "nonce": nonce}
        receipt = {"attestation": attestation, "tree": str(tree), "report_sha256": None}
        with receipt_path.open("x", encoding="utf-8") as stream:
            json.dump(receipt, stream)
        env[NONCE_ENV] = nonce
        # The child deposits its exact report outside the archived tree. Only this
        # launcher seals it after a successful exit and a final descriptor check.
        result_path = Path(folder) / "report.json"
        env["DIRECTOR_SCORECARD_RESULT"] = str(result_path)
        # -I retains this interpreter's installed dependencies, excluding user site,
        # caller PYTHONPATH and cwd. Preserve its native-loader environment too.
        bootstrap = ("import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); "
                     "script = sys.argv.pop(1); sys.argv[0] = script; "
                     "runpy.run_path(script, run_name='__main__')")
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", bootstrap, str(tree / "src"),
             str(tree / "scripts" / "director-scorecard.py"), *arguments],
            env=env, cwd=here).returncode
        receipt["report_sha256"] = "INVALID"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        if result:
            return result
        try:
            report = json.loads(result_path.read_bytes())
            # The whole descriptor, byte for byte, must be the one this launch pinned (#287 item 33):
            # rotating any field mid-run (security base, workflow pin, releases) voids the report.
            reread = read_descriptor(descriptor)
            if (report.get("attested") is not True or report.get("attestation") != attestation or
                    reread is None or reread[0] != descriptor_sha256):
                return _refuse(UNATTESTED)
            receipt["report_sha256"] = report_digest(report)
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            _publish_html(tree, report, arguments)
        except (OSError, ValueError, TypeError):
            receipt["report_sha256"] = "INVALID"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            return _refuse(UNATTESTED)
        return 0


def _refuse(reason: str) -> int:
    print(f"{REFUSAL}: {reason}", file=sys.stderr)
    return 2


def main(argv: list[str]) -> int:
    if not sys.flags.isolated:
        return _refuse("run the launcher with python -I so nothing on PYTHONPATH or in the checkout is imported")
    try:
        installed = owner_root() / "launcher.py"
    except OSError:
        return _refuse("the owner account home could not be resolved")
    if Path(__file__).resolve() != installed.resolve() or installed.is_symlink():
        return _refuse("use the owner-installed launcher")
    if argv[:1] == ["--verify-report"]:
        try:
            valid = len(argv) == 2 and report_is_attested(json.loads(Path(argv[1]).read_bytes()))
        except (OSError, ValueError):
            valid = False
        print("ATTESTED: owner-installed launcher report" if valid else UNATTESTED)
        return 0 if valid else 2
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    return launch(Path(argv[0]).resolve(), argv[1:])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 19:06 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 19:58 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 02:11 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 14:10 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 14:55 UTC | END]]
