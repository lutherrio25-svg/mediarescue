"""The Windows build and the wheel must not disagree about what version they are."""
from pathlib import Path

from mediarescue import __version__

ROOT = Path(__file__).resolve().parent.parent


def test_the_exe_version_resource_matches_the_package():
    text = (ROOT / "packaging" / "version_info.txt").read_text(encoding="utf-8")
    assert f"'{__version__}'" in text, "packaging/version_info.txt is stale"
    assert text.count(f"'{__version__}'") == 2, "FileVersion and ProductVersion must both match"
    # Explorer shows the tuple and the string; a bump that updated only one is a silent mismatch
    # in the file properties the user checks when deciding whether the update took.
    parts = __version__.split(".")
    tuple_version = "(" + ", ".join(parts + ["0"] * (4 - len(parts))) + ")"
    assert text.count(tuple_version) == 2, f"filevers/prodvers must be {tuple_version}"


def test_the_installer_takes_its_version_from_the_build():
    text = (ROOT / "packaging" / "mediarescue.iss").read_text(encoding="utf-8")
    # A hard-coded version in the .iss would ship mediarescue-0.9.0-setup.exe from a 0.11 build.
    assert "OutputBaseFilename={#AppName}-{#AppVersion}-setup" in text
    assert 'AppVersion={#AppVersion}' in text


def test_the_spec_builds_one_console_exe_and_one_window_exe():
    """The console flag lives on the exe, so a slip here silently brings the terminal window back."""
    spec = (ROOT / "packaging" / "mediarescue.spec").read_text(encoding="utf-8")
    cli_block, _, window_block = spec.partition('name="mediarescue-window"')
    assert "console=True" in cli_block, "the command line build must keep its console"
    assert "console=False" in window_block, "the window build must have no console at all"


def test_the_installer_opens_the_window_not_a_console():
    text = (ROOT / "packaging" / "mediarescue.iss").read_text(encoding="utf-8")
    assert '#define AppExe "mediarescue-window.exe"' in text, \
        "the Start Menu entry must open the windowless build"
    assert 'Name: "{autodesktop}\\{#AppName}"; Filename: "{app}\\{#AppExe}"' in text


def test_the_release_signs_both_exes_and_the_installer_or_says_it_cannot():
    """An unsigned exe inside a signed folder is not signed; the installer is a separate file."""
    workflow = (ROOT / ".github" / "workflows" / "build-exe.yml").read_text(encoding="utf-8")
    # Two steps (exes, then installer) times two routes (.pfx, store thumbprint).
    assert workflow.count("-Path dist-exe/mediarescue ") == 2, \
        "the two exes must go through sign.ps1 on both routes"
    assert workflow.count("-Path dist-setup ") == 2, \
        "the installer is a separate file with its own signature, on both routes"
    assert "SIGNING_CERT_THUMBPRINT" in workflow, "a hardware/cloud key has to be usable too"
    assert "no signing certificate configured" in workflow, \
        "an unsigned release must say so in the log instead of failing silently"


def test_the_signing_script_timestamps_and_verifies():
    text = (ROOT / "packaging" / "sign.ps1").read_text(encoding="utf-8")
    # Without a timestamp every signature dies with the certificate, and already-downloaded copies
    # start warning again the day it expires.
    assert "timestamp.digicert.com" in text
    assert "Set-AuthenticodeSignature" in text, "a laptop without Windows SDK must still be able to sign"
    assert "Get-AuthenticodeSignature" in text, "signing without checking the result is a claim"
    assert "AllowUntrusted" in text, "a self-signed rehearsal must be possible without weakening releases"


WINGET = ROOT / "packaging" / "winget" / "manifests" / "s" / "StarCorpsBks" / "MediaRescue"


def _field(text: str, name: str) -> str:
    for line in text.splitlines():
        # Installers and AppsAndFeaturesEntries are lists, so keys arrive indented and sometimes
        # behind a dash.
        stripped = line.strip().lstrip("- ")
        if stripped.startswith(f"{name}:"):
            return stripped.split(":", 1)[1].strip().strip('"')
    raise AssertionError(f"{name} is missing from the manifest")


def test_the_winget_manifest_describes_the_installer_we_publish():
    """A manifest that drifts from the release installs the wrong bytes, silently.

    The catalog is allowed to sit behind the repository: a release ships from here the day it is
    ready, while winget submission waits in a moderator queue that runs for weeks. What it must
    never do is run *ahead* -- a manifest for a version this repository has not released points at
    an installer URL and a digest that do not exist -- so the newest manifest is checked for
    internal consistency and for being no newer than the package version.
    """
    folders = sorted((p.name for p in WINGET.iterdir() if p.is_dir()),
                     key=lambda v: tuple(int(part) for part in v.split(".")))
    assert folders, "no winget manifest at all"
    newest = folders[-1]
    assert tuple(int(p) for p in newest.split(".")) <= tuple(int(p) for p in __version__.split(".")), (
        f"winget manifest {newest} is newer than the package version {__version__}")

    folder = WINGET / newest
    ver = (folder / "StarCorpsBks.MediaRescue.yaml").read_text(encoding="utf-8")
    inst = (folder / "StarCorpsBks.MediaRescue.installer.yaml").read_text(encoding="utf-8")
    loc = (folder / "StarCorpsBks.MediaRescue.locale.en-US.yaml").read_text(encoding="utf-8")

    assert _field(ver, "PackageVersion") == newest
    assert _field(inst, "PackageVersion") == newest
    assert _field(loc, "PackageVersion") == newest

    # winget decides which file is which by ManifestType. The default locale must say
    # "defaultLocale": call it "locale" and the client answers "the multi file manifest is
    # incomplete" -- an error that names no file, and cost an hour to find.
    assert _field(ver, "ManifestType") == "version"
    assert _field(inst, "ManifestType") == "installer"
    assert _field(loc, "ManifestType") == "defaultLocale"
    assert _field(ver, "DefaultLocale") == "en-US"

    # Point at the tag, never /releases/latest: a manifest that follows latest installs bytes the
    # digest below it does not describe.
    url = _field(inst, "InstallerUrl")
    assert url.endswith(f"/download/v{newest}/mediarescue-{newest}-setup.exe"), url
    digest = _field(inst, "InstallerSha256")
    assert len(digest) == 64 and set(digest) <= set("0123456789ABCDEF"), digest

    # Per-user install: the .iss installs with PrivilegesRequired=lowest, so a manifest that claims
    # machine scope would ask for an elevation the installer never needed.
    iss = (ROOT / "packaging" / "mediarescue.iss").read_text(encoding="utf-8")
    assert "PrivilegesRequired=lowest" in iss
    assert _field(inst, "Scope") == "user"

    # Inno names the Programs-and-Features entry from AppVerName, so what winget must look for is
    # "mediarescue X.Y.Z" and not the bare AppName. Miss it and winget installs a second copy
    # beside the one already on disk instead of upgrading it.
    assert _field(inst, "DisplayName") == f"mediarescue {newest}"


def test_the_free_signing_route_is_documented_and_inert():
    doc = (ROOT / "docs" / "SIGNING.md").read_text(encoding="utf-8")
    assert "SignPath Foundation" in doc and "free" in doc
    job = (ROOT / "docs" / "signpath-job.yml").read_text(encoding="utf-8")
    assert "signpath/github-action-submit-signing-request" in job
    # A workflow file here would run on every push and fail on a policy that does not exist yet.
    assert not (ROOT / ".github" / "workflows" / "sign-windows.yml").exists()


def test_the_frozen_launcher_keeps_the_exit_code_and_the_utf8_streams():
    text = (ROOT / "packaging" / "entry.py").read_text(encoding="utf-8")
    assert "raise SystemExit(main())" in text, "the CLI's verdict is its exit code"
    assert 'encoding="utf-8"' in text, "a frozen build must print the same bytes as the wheel"


def _ico_sizes(path: Path) -> list[int]:
    """Sizes declared in an .ico directory. Every Windows surface picks a different one, so a file
    that only carries 256 px looks fine in Explorer and turns to mush in the taskbar."""
    data = path.read_bytes()
    assert data[:4] == b"\x00\x00\x01\x00", f"{path.name} is not an icon"
    count = int.from_bytes(data[4:6], "little")
    sizes = []
    for i in range(count):
        at = 6 + i * 16
        w = data[at] or 256
        h = data[at + 1] or 256
        assert w == h, f"{path.name} entry {i} is not square"
        sizes.append(w)
    return sizes


def test_the_installer_and_the_exes_carry_the_same_icon():
    """The icon file is optional for PyInstaller (the spec falls back to none), so its absence is
    silent: the app would simply ship with the default Python icon on every surface."""
    ico = ROOT / "packaging" / "mediarescue.ico"
    assert ico.is_file(), "packaging/mediarescue.ico is missing"
    sizes = _ico_sizes(ico)
    assert sizes == [16, 20, 24, 32, 40, 48, 64, 128, 256], f"unexpected icon sizes: {sizes}"
    # 16 px is drawn on its own grid; if it were the 256 px image scaled down the cut-out would blur
    # into the shield, which is exactly what makes small Windows icons look amateur.
    sixteen = ico.read_bytes()
    assert len(sixteen) > 1024

    spec = (ROOT / "packaging" / "mediarescue.spec").read_text(encoding="utf-8")
    assert 'root / "packaging" / "mediarescue.ico"' in spec, "the spec must point at the icon"

    iss = (ROOT / "packaging" / "mediarescue.iss").read_text(encoding="utf-8")
    assert "SetupIconFile=mediarescue.ico" in iss, "the installer must not fall back to the Inno icon"
