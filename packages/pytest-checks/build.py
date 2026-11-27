"""Build the optional pytest adapter as a deterministic wheel using the public capture implementation."""
import base64
import csv
import hashlib
import io
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

here = Path(__file__).resolve().parent
version = "0.1.1"
distribution = f"tokensand_pytest_checks-{version}"
dist = distribution + ".dist-info"
capture = here.parents[1] / "plugins/tokensand-agent-pack/skills/tokensand-agent-pack/scripts/capture_pytest.py"
files = {f"tokensand_pytest/{path.name}": path.read_bytes() for path in (here / "tokensand_pytest").glob("*.py")}
files["tokensand_pytest/capture.py"] = capture.read_bytes()
files[f"{dist}/METADATA"] = (
    f"Metadata-Version: 2.4\nName: tokensand-pytest-checks\nVersion: {version}\n"
    "Summary: Report private counts for one explicitly connected pytest workflow file.\nRequires-Python: >=3.10\nLicense-Expression: MIT\nLicense-File: LICENSE\n\n"
).encode()
files[f"{dist}/licenses/LICENSE"] = (here.parents[1] / "LICENSE").read_bytes()
files[f"{dist}/WHEEL"] = b"Wheel-Version: 1.0\nGenerator: tokensand\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
files[f"{dist}/entry_points.txt"] = b"[pytest11]\ntokensand_repository_checks = tokensand_pytest.plugin\n"
record = io.StringIO(newline="")
rows = csv.writer(record, lineterminator="\n")
for name, data in sorted(files.items()):
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    rows.writerow([name, "sha256=" + digest, len(data)])
rows.writerow([f"{dist}/RECORD", "", ""])
files[f"{dist}/RECORD"] = record.getvalue().encode()
destination = here.parent / (distribution + "-py3-none-any.whl")
with ZipFile(destination, "w") as archive:
    for name, data in sorted(files.items()):
        entry = ZipInfo(name, (2020, 1, 1, 0, 0, 0))
        entry.compress_type = ZIP_DEFLATED
        entry.external_attr = 0o100644 << 16
        archive.writestr(entry, data)
print(destination)
