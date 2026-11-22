"""The Windows build and the wheel must not disagree about what version they are."""
from pathlib import Path

from mediarescue import __version__

ROOT = Path(__file__).resolve().parent.parent


def test_the_exe_version_resource_matches_the_package():
    text = (ROOT / "packaging" / "version_info.txt").read_text(encoding="utf-8")
    assert f"'{__version__}'" in text, "packaging/version_info.txt is stale"
    assert text.count(f"'{__version__}'") == 2, "FileVersion and ProductVersion must both match"


def test_the_installer_takes_its_version_from_the_build():
    text = (ROOT / "packaging" / "mediarescue.iss").read_text(encoding="utf-8")
    # A hard-coded version in the .iss would ship mediarescue-0.9.0-setup.exe from a 0.11 build.
    assert "OutputBaseFilename={#AppName}-{#AppVersion}-setup" in text
    assert 'AppVersion={#AppVersion}' in text


def test_the_frozen_launcher_keeps_the_exit_code_and_the_utf8_streams():
    text = (ROOT / "packaging" / "entry.py").read_text(encoding="utf-8")
    assert "raise SystemExit(main())" in text, "the CLI's verdict is its exit code"
    assert 'encoding="utf-8"' in text, "a frozen build must print the same bytes as the wheel"
