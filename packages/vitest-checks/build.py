"""Create the standalone npm prototype with the exact existing Python receipt client."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

source = Path(__file__).resolve().parent
out = Path(sys.argv[1]).resolve(strict=True)
with tempfile.TemporaryDirectory() as temp:
    stage = Path(temp)
    for name in ["package.json", "reporter.mjs", "cli.mjs", "bridge.py", "README.md"]:
        shutil.copy2(source / name, stage / name)
    canonical = (source.parent / "pytest-checks/tokensand_pytest/reporter.py").read_text()
    # Keep the wheel's existing transport unmodified. The private npm consumer has its
    # own outbox, takes its HTTP key from bridge memory, and limits the Git child env.
    changes = {
        'Path(git.stdout.strip()) / "tokensand-private"': 'Path(git.stdout.strip()) / "tokensand-private-vitest"',
        'key = os.environ.get("TOKENS_OBSERVE_API_KEY")': 'key = _vitest_key',
        'capture_output=True, text=True, cwd=checkout)': 'capture_output=True, text=True, cwd=checkout, env=_vitest_git_env)',
    }
    for old, new in changes.items():
        if canonical.count(old) != 1:
            raise RuntimeError("Review the repository transport before packaging Vitest")
        canonical = canonical.replace(old, new)
    (stage / "repository_transport.py").write_text(canonical)
    shutil.copy2(source.parents[1] / "LICENSE", stage / "LICENSE")
    subprocess.run(["npm", "pack", "--ignore-scripts", "--pack-destination", str(out)], cwd=stage, check=True)
