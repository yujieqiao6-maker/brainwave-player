Unicode True
!include "MUI2.nsh"
!include "x64.nsh"
!include "WinVer.nsh"
!ifndef PAYLOAD
  !error "PAYLOAD must point to the staged Windows application"
!endif
!ifndef OUTPUT
  !error "OUTPUT must specify the installer path"
!endif
Name "脑波控制视频播放器 8.1.0"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\BrainwavePlayer"
InstallDirRegKey HKCU "Software\BrainwavePlayer\Installer" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 32
ShowInstDetails show
ShowUninstDetails show
VIProductVersion "8.1.0.0"
VIAddVersionKey /LANG=2052 "ProductName" "BrainwavePlayer"
VIAddVersionKey /LANG=2052 "FileDescription" "脑波控制视频播放器安装程序"
VIAddVersionKey /LANG=2052 "FileVersion" "8.1.0"
VIAddVersionKey /LANG=2052 "LegalCopyright" "BrainwavePlayer contributors"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\runtime\pythonw.exe"
!define MUI_FINISHPAGE_RUN_PARAMETERS "$\"$INSTDIR\app\launcher.py$\""
!define MUI_FINISHPAGE_RUN_TEXT "启动脑波控制视频播放器"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "需要 Windows 10/11 64 位系统。"
    Abort
  ${EndIf}
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_ICONSTOP "需要 Windows 10 或更新版本。"
    Abort
  ${EndIf}
  SetRegView 64
  SetShellVarContext current
FunctionEnd

Section "安装播放器" Main
  SetOutPath "$INSTDIR"
  File /r "${PAYLOAD}/*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\BrainwavePlayer"
  CreateShortCut "$SMPROGRAMS\BrainwavePlayer\脑波控制视频播放器.lnk" "$INSTDIR\runtime\pythonw.exe" '"$INSTDIR\app\launcher.py"'
  CreateShortCut "$SMPROGRAMS\BrainwavePlayer\卸载.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortCut "$DESKTOP\脑波控制视频播放器.lnk" "$INSTDIR\runtime\pythonw.exe" '"$INSTDIR\app\launcher.py"'
  WriteRegStr HKCU "Software\BrainwavePlayer\Installer" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BrainwavePlayer" "DisplayName" "脑波控制视频播放器"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BrainwavePlayer" "DisplayVersion" "8.1.0"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BrainwavePlayer" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BrainwavePlayer" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BrainwavePlayer" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BrainwavePlayer" "NoRepair" 1
SectionEnd

Section "Uninstall"
  SetRegView 64
  SetShellVarContext current
  !include "${REMOVE_MANIFEST}"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$DESKTOP\脑波控制视频播放器.lnk"
  Delete "$SMPROGRAMS\BrainwavePlayer\脑波控制视频播放器.lnk"
  Delete "$SMPROGRAMS\BrainwavePlayer\卸载.lnk"
  RMDir "$SMPROGRAMS\BrainwavePlayer"
  DeleteRegKey HKCU "Software\BrainwavePlayer\Installer"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BrainwavePlayer"
SectionEnd
