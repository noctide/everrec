; EverRec 安装包脚本（tasks.md WP9、设计文档 14.1）
; 用 Inno Setup 6 编译：iscc packaging\windows\everrec.iss
;
; 选 Inno Setup 的理由（PR 里也写）：静默安装参数、升级/卸载保留数据、D→C 盘回退
; 这类需求在 Inno 的 [Code] 段里脚本更清晰，生态成熟、文档全，单个 .iss 搞定。
;
; 行为要点：
;   - 默认装 D:\EverRec；没有 D 盘退 C:\EverRec 并把本地配额调小（20GB→5GB）。
;   - 升级/卸载保留 recordings\、config\、logs\、state\（Inno 只删它装过的文件）。
;   - 静默参数：/SERVER=https://x /LOCATION=012，安装后调 everrec-agent set-config 写配置。
;   - 装完注册 WinSW 服务（开机自启）、跑安装期 PowerShell（相机权限/电源/USB/ACL/自启）。

#define MyAppName "EverRec 恒录"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "EverRec"
#define DistDir "..\..\dist"

[Setup]
AppId={{8F3C2A19-5B7E-4D6C-9E1A-7C4D8B2F6A01}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName=D:\EverRec
DisableProgramGroupPage=yes
OutputBaseFilename=EverRecSetup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\everrec-agent\everrec-agent.exe

[Languages]
Name: "chs"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicons"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："
Name: "startup"; Description: "开机自动运行托盘（所有用户启动项）"; GroupDescription: "开机自启："

[Files]
; 四个 onedir 程序整体拷进 {app}\下同名子目录
Source: "{#DistDir}\everrec-agent\*";  DestDir: "{app}\everrec-agent";  Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#DistDir}\everrec-client\*"; DestDir: "{app}\everrec-client"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#DistDir}\everrec-tray\*";   DestDir: "{app}\everrec-tray";   Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#DistDir}\everrec-repair\*"; DestDir: "{app}\everrec-repair"; Flags: ignoreversion recursesubdirs createallsubdirs
; WinSW 服务定义 + 服务二进制（WinSW-x64.exe 需提前改名放到 packaging\windows\winsw\）
Source: "..\..\deploy\windows\everrec-agent.xml"; DestDir: "{app}"; Flags: ignoreversion
Source: "winsw\everrec-agent-service.exe";        DestDir: "{app}"; Flags: ignoreversion
; 安装期脚本
Source: "..\..\deploy\windows\setup-postinstall.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}\录像客户端"; Filename: "{app}\everrec-client\everrec-client.exe"
Name: "{autoprograms}\{#MyAppName}\修复工具";   Filename: "{app}\everrec-repair\everrec-repair.exe"
Name: "{autodesktop}\{#MyAppName}";            Filename: "{app}\everrec-client\everrec-client.exe"; Tasks: desktopicons

[Registry]
; 托盘加入所有用户启动项（HKLM ...\Run = 所有用户）
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "EverRecTray"; ValueData: """{app}\everrec-tray\everrec-tray.exe"""; \
  Flags: uninsdeletevalue; Tasks: startup

[Run]
; 1) 注册并启动 WinSW 服务（开机自启、不依赖登录、异常自动拉起）
Filename: "{app}\everrec-agent-service.exe"; Parameters: "install"; \
  StatusMsg: "正在注册录制服务…"; Flags: runhidden waituntilterminated
Filename: "{app}\everrec-agent-service.exe"; Parameters: "start"; \
  StatusMsg: "正在启动录制服务…"; Flags: runhidden waituntilterminated
; 2) 安装期 PowerShell：相机权限、电源不睡眠、关 USB 选择性暂停、配置 ACL
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\setup-postinstall.ps1"" -InstallDir ""{app}"""; \
  StatusMsg: "正在配置系统（相机/电源/权限）…"; Flags: runhidden waituntilterminated

[UninstallRun]
; 卸载前停服务、注销服务
Filename: "{app}\everrec-agent-service.exe"; Parameters: "stop";     Flags: runhidden waituntilterminated
Filename: "{app}\everrec-agent-service.exe"; Parameters: "uninstall"; Flags: runhidden waituntilterminated

[Code]
{ ---- 从命令行解析静默参数 /SERVER= 与 /LOCATION= ---- }

// 取形如 /SERVER=https://x 的参数值；没有返回空串
function ParamValue(const name: String): String;
var
  i: Integer;
  arg: String;
  prefix: String;
begin
  Result := '';
  prefix := '/' + name + '=';
  for i := 1 to ParamCount do
  begin
    arg := ParamStr(i);
    if CompareText(Copy(arg, 1, Length(prefix)), prefix) = 0 then
    begin
      Result := Copy(arg, Length(prefix) + 1, Length(arg));
      exit;
    end;
  end;
end;

// 用 everrec-agent set-config 写一个配置项（键=值）
procedure SetConfig(const key, value: String);
var
  rc: Integer;
begin
  if value = '' then exit;
  Exec(ExpandConstant('{app}\everrec-agent\everrec-agent.exe'),
       'set-config -c "' + ExpandConstant('{app}\config\everrec.yaml') + '" ' + key + '=' + value,
       '', SW_HIDE, ewWaitUntilTerminated, rc);
end;

procedure InitializeWizard();
begin
  // 没有 D 盘：退到 C:\EverRec（配额在 CurStepChanged 里一并调小）
  if not DirExists('D:\') then
    WizardForm.DirEdit.Text := 'C:\EverRec';
end;

function ShouldAdjustQuota(): Boolean;
begin
  // 装到 C 盘说明没有 D 盘，空间紧张，本地配额调小
  Result := Pos('C:\', ExpandConstant('{app}')) = 1;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  rc: Integer;
  cfg: String;
begin
  cfg := ExpandConstant('{app}\config\everrec.yaml');
  if CurStep = ssPostInstall then
  begin
    // 首次安装生成默认配置；升级（已有配置）绝不动
    if not FileExists(cfg) then
      Exec(ExpandConstant('{app}\everrec-agent\everrec-agent.exe'),
           'init "' + ExpandConstant('{app}') + '"', '', SW_HIDE, ewWaitUntilTerminated, rc);
    // 静默参数写入服务器地址与位置编号
    SetConfig('server', ParamValue('SERVER'));
    SetConfig('location.number', ParamValue('LOCATION'));
    // 无 D 盘：本地配额调小到 5GB
    if ShouldAdjustQuota() then
      SetConfig('recording.local_quota_gb', '5');
  end;
end;
