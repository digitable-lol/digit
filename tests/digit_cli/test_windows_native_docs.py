from pathlib import Path


def test_windows_native_install_path_docs_match_installer() -> None:
    doc = Path("website/docs/user-guide/windows-native.md").read_text()
    install = Path("scripts/install.ps1").read_text()

    assert "%LOCALAPPDATA%\\digit\\digit\\venv\\Scripts" in doc
    assert "Get-Command digit        # should print C:\\Users\\<you>\\AppData\\Local\\digit\\digit\\venv\\Scripts\\digit.exe" in doc
    assert '$digitBin = "$InstallDir\\venv\\Scripts"' in install
