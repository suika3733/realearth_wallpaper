; RealEarth 真实地球壁纸 - NSIS 安装脚本
; 编译: makensis /DMY_OUTPUT=RealEarth安装程序-v3.1.0.exe /DSRC_DIR=dist-dir/RealEarth RealEarthSetup.nsi

; 必须指定 Unicode 以获得中文支持
Unicode true

;----------- 版本信息 ----------
!define APP_NAME "RealEarth"
!define APP_DISPLAY_NAME "RealEarth 真实地球壁纸"
!define APP_VERSION "3.1.0"
!define APP_PUBLISHER "RealEarth"
!define APP_WEB "https://github.com/suika3733/realearth_wallpaper"

; 允许通过命令行传入
!ifndef MY_OUTPUT
  !define MY_OUTPUT "RealEarthSetup-v3.1.0.exe"
!endif
!ifndef SRC_DIR
  !define SRC_DIR "installer\source\RealEarth"
!endif

;----------- 基础设置 ----------
Name "${APP_DISPLAY_NAME}"
OutFile "${MY_OUTPUT}"
InstallDir "$PROGRAMFILES64\${APP_NAME}"
InstallDirRegKey HKLM "Software\${APP_NAME}" "InstallLocation"
RequestExecutionLevel admin
SetCompressor /SOLID lzma
SetCompressorDictSize 32

;----------- 图标 ----------
!define APP_ICON "RealEarth.ico"
Icon "${APP_ICON}"
UninstallIcon "${APP_ICON}"
VIProductVersion "3.1.0.0"
VIAddVersionKey "ProductName" "${APP_DISPLAY_NAME}"
VIAddVersionKey "ProductVersion" "${APP_VERSION}"
VIAddVersionKey "FileVersion" "${APP_VERSION}"
VIAddVersionKey "FileDescription" "${APP_DISPLAY_NAME}"
VIAddVersionKey "LegalCopyright" "Copyright (c) 2026 RealEarth"
VIAddVersionKey "CompanyName" "${APP_PUBLISHER}"
VIAddVersionKey "Comments" "实时地球壁纸"

;----------- 现代用户界面 (MUI2) ----------
!include "MUI2.nsh"

; 界面设置
!define MUI_ABORTWARNING
!define MUI_ICON "${APP_ICON}"
!define MUI_UNICON "${APP_ICON}"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_BITMAP_NOSTRETCH

; 欢迎页
!define MUI_WELCOMEPAGE_TITLE "欢迎安装 ${APP_DISPLAY_NAME} v${APP_VERSION}"
!define MUI_WELCOMEPAGE_TEXT "本向导将引导你完成 ${APP_DISPLAY_NAME} 的安装。$\r$\n$\r$\n该软件可以自动获取 NASA 每日天文图片、卫星影像和 SDO 太阳观测图像，并设置为你的桌面壁纸。$\r$\n$\r$\n点击「下一步」继续。"
!insertmacro MUI_PAGE_WELCOME

; 许可协议页
!insertmacro MUI_PAGE_LICENSE "${SRC_DIR}\..\license.txt"

; 安装目录页
!insertmacro MUI_PAGE_DIRECTORY

; 组件选择页（隐藏，使用默认组件）
!insertmacro MUI_PAGE_COMPONENTS

; 安装过程页
!insertmacro MUI_PAGE_INSTFILES

; 完成页
!define MUI_FINISHPAGE_RUN "$INSTDIR\RealEarth.exe"
!define MUI_FINISHPAGE_RUN_TEXT "立即启动 ${APP_NAME}"
!define MUI_FINISHPAGE_SHOWREADME "$INSTDIR\使用说明.txt"
!define MUI_FINISHPAGE_SHOWREADME_TEXT "查看使用说明"
!define MUI_FINISHPAGE_SHOWREADME_NOTCHECKED
!insertmacro MUI_PAGE_FINISH

; 卸载页
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

;----------- 语言 ----------
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

; 页面文字（简体中文为主）
LangString DESC_SecApp ${LANG_SIMPCHINESE} "安装 RealEarth 主程序"
LangString DESC_SecDesktop ${LANG_SIMPCHINESE} "在桌面创建快捷方式"
LangString DESC_SecStartMenu ${LANG_SIMPCHINESE} "在开始菜单创建快捷方式"

;----------- 默认安装节 ----------
Section "RealEarth 主程序" SecApp
  SectionIn RO
  SetOutPath "$INSTDIR"
  ; 复制 onedir 打包的所有文件
  File /r "${SRC_DIR}\*.*"
  ; 复制使用说明
  File "${SRC_DIR}\..\使用说明.txt"
  ; 写入卸载信息
  WriteUninstaller "$INSTDIR\uninstall.exe"
  ; 注册表 - 安装信息
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "DisplayName" "${APP_DISPLAY_NAME}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "Publisher" "${APP_PUBLISHER}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "DisplayIcon" "$INSTDIR\RealEarth.exe"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "UninstallString" "$INSTDIR\uninstall.exe"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "URLInfoAbout" "${APP_WEB}"
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "NoModify" 1
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "NoRepair" 1
  ; 应用自身的安装路径注册
  WriteRegStr HKLM "Software\${APP_NAME}" "InstallLocation" "$INSTDIR"
SectionEnd

; 桌面快捷方式（可选组件）
Section /o "桌面快捷方式" SecDesktop
  CreateShortCut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\RealEarth.exe" "" "$INSTDIR\RealEarth.exe" 0 SW_SHOWNORMAL "" "${APP_DISPLAY_NAME}"
SectionEnd

; 开始菜单快捷方式
Section "开始菜单快捷方式" SecStartMenu
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\RealEarth.exe" "" "$INSTDIR\RealEarth.exe" 0 SW_SHOWNORMAL "" "${APP_DISPLAY_NAME}"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\卸载${APP_NAME}.lnk" "$INSTDIR\uninstall.exe"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\使用说明.lnk" "$INSTDIR\使用说明.txt"
SectionEnd

;----------- 卸载节 ----------
Section "Uninstall"
  ; 停止可能运行的程序
  nsExec::Exec "taskkill /F /IM RealEarth.exe"
  ; 删除文件
  RMDir /r "$INSTDIR"
  ; 删除快捷方式
  Delete "$DESKTOP\${APP_NAME}.lnk"
  RMDir /r "$SMPROGRAMS\${APP_NAME}"
  ; 删除注册表
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"
  DeleteRegKey HKLM "Software\${APP_NAME}"
  DeleteRegKey HKCU "Software\${APP_NAME}"
SectionEnd

; 组件描述
!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecApp} $(DESC_SecApp)
  !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} $(DESC_SecDesktop)
  !insertmacro MUI_DESCRIPTION_TEXT ${SecStartMenu} $(DESC_SecStartMenu)
!insertmacro MUI_FUNCTION_DESCRIPTION_END
