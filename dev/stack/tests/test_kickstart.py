import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "kickstart.sh"
SUM = "ab" * 32
RELEASES = """[
  {"tag_name": "runner-9.9.9"},
  {"tag_name": "image-2.0.0"},
  {"tag_name": "image-1.0.0"}
]"""
CURL = r"""#!/bin/bash
url=""; posted=""
while [ $# -gt 0 ]; do
    case "$1" in
    -d) posted=yes; shift ;;
    -o | -H | -w | -X) shift ;;
    http*) url="$1" ;;
    esac
    shift
done
case "$url" in
*/releases\?*) cat "$FAKE/releases" ;;
*/image-2.0.0/SHA256SUMS) cat "$FAKE/sums" ;;
*/api/v1/images) if [ -n "$posted" ]; then cat >"$FAKE/posted"; else cat "$FAKE/images"; fi ;;
*) echo "unexpected $url" >&2; exit 22 ;;
esac
"""
GIT = "#!/bin/bash\necho git@github.com:alpha/beta.git\n"


@pytest.fixture
def stand(tmp_path: Path) -> Path:
    fake = tmp_path / "fake"
    for folder in (fake, tmp_path / "bin", tmp_path / "root/docker", tmp_path / "root/.venv/bin"):
        folder.mkdir(parents=True)
    (tmp_path / "local").mkdir()
    (tmp_path / "local/operator").write_text("operator-token")
    (tmp_path / "root/docker/.env").write_text("NAOS_BIND=127.0.0.1\nNAOS_API_PORT=8080\n")
    (tmp_path / "root/.venv/bin/python").symlink_to(sys.executable)
    for name, body in (("curl", CURL), ("git", GIT)):
        (tmp_path / "bin" / name).write_text(body)
        (tmp_path / "bin" / name).chmod(0o755)
    (fake / "releases").write_text(RELEASES)
    (fake / "sums").write_text(f"{SUM}  naos-agents-2.0.0.qcow2\n{'cd' * 32}  other.qcow2\n")
    (fake / "images").write_text("[]")
    return tmp_path


def _register(stand: Path) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": f"{stand / 'bin'}:{os.environ['PATH']}",
        "ROOT": str(stand / "root"),
        "LOCAL": str(stand / "local"),
        "FAKE": str(stand / "fake"),
    }
    return subprocess.run(  # noqa: S603
        ["/bin/bash", str(SCRIPT), "image"], env=env, capture_output=True, text=True, check=False
    )


def test_a_stand_registers_the_newest_release_through_the_api(stand: Path) -> None:
    done = _register(stand)

    assert done.returncode == 0, done.stderr
    release = "https://github.com/alpha/beta/releases/download/image-2.0.0"
    assert json.loads((stand / "fake/posted").read_text()) == {
        "id": "naos-agents-2.0.0",
        "version": "2.0.0",
        "digest": f"sha256:{SUM}",
        "url": f"{release}/naos-agents-2.0.0.qcow2",
    }


def test_a_catalog_with_other_images_still_gets_the_newest(stand: Path) -> None:
    other = "cd" * 32
    (stand / "fake/images").write_text(f'[{{"id": "image_alpha", "digest": "sha256:{other}"}}]')

    assert _register(stand).returncode == 0
    assert json.loads((stand / "fake/posted").read_text())["id"] == "naos-agents-2.0.0"


def test_the_newest_release_already_in_the_catalog_is_not_posted_again(stand: Path) -> None:
    (stand / "fake/images").write_text(f'[{{"id": "image_alpha", "digest": "sha256:{SUM}"}}]')

    assert _register(stand).returncode == 0
    assert not (stand / "fake/posted").exists()


def test_a_release_without_its_checksum_stops_the_start(stand: Path) -> None:
    (stand / "fake/sums").write_text(f"{SUM}  other.qcow2\n")

    done = _register(stand)

    assert done.returncode != 0
    assert "is not listed in" in done.stderr
    assert not (stand / "fake/posted").exists()
