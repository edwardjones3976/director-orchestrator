"""Add the fixture workflow verifier to a REA-exported AgentEnv bundle."""

import argparse
import json
import shutil
from pathlib import Path


def prepare(bundle):
    environment = bundle / "envs" / "release-folders"
    shutil.copyfile(Path(__file__).with_name("apply_rea_precision.py"), environment / "apply_rea_precision.py")
    dockerfile = environment / "Dockerfile"
    text = dockerfile.read_text()
    install = "RUN npm install -g --ignore-scripts --no-fund --no-audit rea-agents@6.9.0\n"
    if text.count(install) != 1:
        raise ValueError("Generated AgentEnv REA install changed")
    text = text.replace(install, install + (
        "COPY apply_rea_precision.py /opt/rea-lab-patch/apply_rea_precision.py\n"
        "RUN python3 /opt/rea-lab-patch/apply_rea_precision.py /usr/local/lib/node_modules/rea-agents\n"
    ))
    dockerfile.write_text(text)
    artifact = bundle / "artifacts" / "check_release_workflow"
    artifact.mkdir(exist_ok=False)
    shutil.copyfile(Path(__file__).with_name("agentenv_verify.py"), artifact / "verify.py")
    task = [
        {"id": "env", "type": "deploy_env", "env_id": "release-folders", "sandbox_type": "local"},
        {
            "id": "workflow", "type": "env_outcome_verifier",
            "env_id": "release-folders", "file_artifact_id": "check_release_workflow",
            "verifier_id": "workflow",
        },
    ]
    (bundle / "tasks" / "workflow.json").write_text(
        json.dumps(task, indent=2) + "\n", encoding="utf-8",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    prepare(parser.parse_args().bundle.resolve())
