"""安装包与打包脚本的一致性（WP9）。

这类错只有在干净的 Windows 上装一遍才会暴露：服务注册失败、托盘读不到配置、装完打不开。
所以在这里把三处路径钉住——WinSW 定义的文件名与可执行文件、快捷方式/启动项的安装目录、
打包产物与安装包拷贝的目录。
"""

import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # packages/agent/tests → 仓库根
ISS = ROOT / "packaging" / "windows" / "everrec.iss"
BUILD = ROOT / "packaging" / "windows" / "build.ps1"
POSTINSTALL = ROOT / "deploy" / "windows" / "setup-postinstall.ps1"
SERVICE_XML = ROOT / "deploy" / "windows" / "everrec-agent-service.xml"


def test_chinese_scripts_have_utf8_bom():
    """带中文的 .ps1 / .iss 必须是 UTF-8 with BOM。

    Windows PowerShell 5.1 和 Inno Setup 6 没有 BOM 就按 ANSI 读：中文变乱码，build.ps1 直接解析失败
    （第一次打 tag 出安装包时就是这么挂的）。仓库其他文件（.py/.md/.yaml）不需要，也不要加。
    """
    for path in (BUILD, ISS, POSTINSTALL):
        data = path.read_bytes()
        assert data.startswith(b"\xef\xbb\xbf"), f"{path.name} 少了 UTF-8 BOM，5.1 / Inno 会读成乱码"


def test_installer_output_dir_matches_ci_artifact_path():
    """CI 最后一步上传 `Output/*.exe`，安装包必须真的输出到仓库根的 Output\\。

    Inno Setup 默认输出到脚本旁边的 `packaging\\windows\\Output\\`，和 CI 的路径对不上就会
    "No files were found with the provided path"（第二次打 tag 时挂在这）。
    """
    iss = ISS.read_text(encoding="utf-8-sig")
    assert "OutputDir=..\\..\\Output" in iss, "iss 没把安装包输出到仓库根的 Output\\"
    workflow = (ROOT / ".github" / "workflows" / "windows-build.yml").read_text(encoding="utf-8")
    assert "path: Output/*.exe" in workflow


def test_service_xml_name_matches_service_exe():
    """WinSW 只读跟可执行文件同名的配置：everrec-agent-service.exe ↔ everrec-agent-service.xml。"""
    iss = ISS.read_text(encoding="utf-8-sig")
    assert SERVICE_XML.exists(), "服务定义文件不在 deploy/windows/"
    assert "winsw\\everrec-agent-service.exe" in iss, "安装包里服务程序不叫 everrec-agent-service.exe"
    assert "deploy\\windows\\everrec-agent-service.xml" in iss, "安装包里没带上同名的服务定义"


def test_service_points_at_agent_inside_its_folder():
    """%BASE% 是安装目录，Agent 本体在 {app}\\everrec-agent\\ 里，不能写成 %BASE%\\everrec-agent.exe。"""
    root = ET.parse(SERVICE_XML).getroot()
    exe = root.findtext("executable", "").lower()
    assert exe == r"%base%\everrec-agent\everrec-agent.exe".lower()
    assert r"%BASE%\config\everrec.yaml" in root.findtext("arguments", "")


def test_launched_programs_get_install_dir():
    """快捷方式和启动项里的客户端/录屏进程必须带 --install-dir {app}，否则找不到 config\\。"""
    for line in ISS.read_text(encoding="utf-8-sig").splitlines():
        text = line.strip()
        if not text or text.startswith(";"):
            continue
        if "everrec-desktop.exe" in text or "everrec-screen.exe" in text:
            assert "--install-dir" in text, f"这条启动项没带安装目录：{text}"


def test_build_outputs_match_what_installer_copies():
    """build.ps1 打出来的每个程序，安装包都要拷过去（多一个少一个都会让安装后缺文件）。"""
    build = BUILD.read_text(encoding="utf-8-sig")
    iss = ISS.read_text(encoding="utf-8-sig")
    for name in ("everrec-agent", "everrec-desktop", "everrec-screen"):
        assert f'Name = "{name}"' in build, f"build.ps1 没有打包 {name}"
        assert "{#DistDir}\\" + name + "\\*" in iss, f"安装包没有拷贝 {name}"


def test_build_uses_project_venv_python():
    """CI 里 pyinstaller 装在 .venv，直接叫 python 会用到 runner 的系统解释器。"""
    build = BUILD.read_text(encoding="utf-8-sig")
    assert r".venv\Scripts\python.exe" in build
    assert "@pyArgs" in build, "PyInstaller 调用没走参数数组（参数里有逗号/空格会散架）"


def test_only_desktop_bundles_qt():
    """Agent 和录屏进程都不 import PySide6，不该把 Qt 收进包里（每个包白多一百多 MB）。"""
    build = BUILD.read_text(encoding="utf-8-sig")
    entries = [line for line in build.splitlines() if line.strip().startswith("@{ Name =")]
    assert len(entries) == 3, "程序清单应该是三个"
    for line in entries:
        expected = "Qt = $true" if "everrec-desktop" in line else "Qt = $false"
        assert expected in line, f"Qt 收集标志不对：{line.strip()}"
