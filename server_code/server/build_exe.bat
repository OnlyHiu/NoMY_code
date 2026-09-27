@echo off
rem ============================================================
rem NoMY 服务管理后台打包脚本（Nuitka standalone 目录模式）
rem
rem 用法：
rem   build_exe.bat          打包命令行版服务端 -> dist\NoMY-server\
rem   build_exe.bat gui      打包图形管理后台   -> dist\NoMY-ServerGUI\
rem
rem 环境：
rem   优先使用项目根目录的 .venv 虚拟环境（依赖与开发环境一致），
rem   找不到 .venv 时才退回系统 PATH 里的 python。
rem
rem 说明：
rem   - 服务端为纯标准库程序；GUI 版基于 PySide6 + qfluentwidgets；
rem   - 首次打包 Nuitka 会自动下载 C 编译器（MinGW64），耗时数分钟；
rem   - GUI 版数据（账号/会话/聊天）保存在 exe 旁的 server_data\ 目录。
rem ============================================================
setlocal
cd /d "%~dp0"

set MODE=%1
if "%MODE%"=="" set MODE=cli

rem 优先使用项目根目录的 .venv（依赖与开发一致），否则用系统 python
if exist "..\.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%cd%\..\.venv\Scripts\python.exe"
    echo [环境] 使用项目虚拟环境: %cd%\..\.venv
) else (
    set "PYTHON_EXE=python"
    echo [环境] 未找到 ..\.venv，使用系统 Python
)

%PYTHON_EXE% -c "import sys; print(sys.version)"

rem GUI 版依赖检查：PySide6 + qfluentwidgets 必须可导入
if /i "%MODE%"=="gui" (
    %PYTHON_EXE% -c "import PySide6, qfluentwidgets" >nul 2>nul
    if errorlevel 1 (
        echo [错误] 当前 Python 缺少 PySide6 / qfluentwidgets，
        echo        请先执行: "%PYTHON_EXE%" -m pip install PySide6 PySide6-Fluent-Widgets
        pause & exit /b 1
    )
)

%PYTHON_EXE% -m pip show nuitka >nul 2>nul
if errorlevel 1 (
    echo [Nuitka] 未安装，正在安装...
    %PYTHON_EXE% -m pip install nuitka
    if errorlevel 1 (
        echo [错误] Nuitka 安装失败，请检查网络或手动执行: pip install nuitka
        pause & exit /b 1
    )
)

if /i "%MODE%"=="gui" goto build_gui

:build_cli
echo.
echo [Nuitka] 开始打包命令行版服务端 distribute_server.py（首次编译需数分钟）...
%PYTHON_EXE% -m nuitka ^
    --standalone ^
    --assume-yes-for-downloads ^
    --output-dir=dist ^
    --output-filename=NoMY-server.exe ^
    --company-name=NoMY ^
    --product-name=NoMY-server ^
    --file-version=1.0.0.0 ^
    --product-version=1.0.0.0 ^
    --windows-icon-from-ico=..\..\client_code\Config\image\menu.ico ^
    --include-data-dir=web=web ^
    --noinclude-dlls=Qt63D* ^
    --noinclude-dlls=Qt6Quick3D* ^
    --noinclude-dlls=Qt6WebEngine* ^
    --noinclude-dlls=Qt6Designer* ^
    --noinclude-dlls=Qt6Charts* ^
    --noinclude-dlls=Qt6DataVis* ^
    --noinclude-dlls=Qt6VirtualKeyboard* ^
    --noinclude-dlls=Qt6Pdf* ^
    --noinclude-dlls=Qt6Test* ^
    --noinclude-dlls=Qt6Sensors* ^
    --noinclude-dlls=Qt6SerialPort* ^
    --noinclude-dlls=Qt6WebSockets* ^
    --noinclude-dlls=Qt6WebChannel* ^
    --noinclude-dlls=Qt6RemoteObjects* ^
    --noinclude-dlls=Qt6Scxml* ^
    --noinclude-dlls=Qt6Positioning* ^
    --noinclude-dlls=Qt6Location* ^
    --noinclude-dlls=Qt6Nfc* ^
    --noinclude-dlls=Qt6Bluetooth* ^
    --noinclude-dlls=opengl32sw.dll ^
    --remove-output ^
    distribute_server.py
if errorlevel 1 (
    echo [错误] 打包失败，请查看上方 Nuitka 输出
    pause & exit /b 1
)
if exist dist\NoMY-server rmdir /s /q dist\NoMY-server
ren dist\distribute_server.dist NoMY-server
goto copy_config

:build_gui
echo.
echo [Nuitka] 开始打包图形管理后台 distribute_server_gui.py（PySide6 体量大，耗时较久）...
%PYTHON_EXE% -m nuitka ^
    --standalone ^
    --assume-yes-for-downloads ^
    --enable-plugin=pyside6 ^
    --include-package-data=qfluentwidgets ^
    --windows-console-mode=disable ^
    --output-dir=dist ^
    --output-filename=NoMY-ServerGUI.exe ^
    --company-name=NoMY ^
    --product-name=NoMY-ServerGUI ^
    --file-version=1.0.0.0 ^
    --product-version=1.0.0.0 ^
    --windows-icon-from-ico=..\..\client_code\Config\image\menu.ico ^
    --include-data-file=..\..\client_code\Config\image\menu.ico=Config\image\menu.ico ^
    --noinclude-dlls=Qt63D* ^
    --noinclude-dlls=Qt6Quick3D* ^
    --noinclude-dlls=Qt6WebEngine* ^
    --noinclude-dlls=Qt6Designer* ^
    --noinclude-dlls=Qt6Charts* ^
    --noinclude-dlls=Qt6DataVis* ^
    --noinclude-dlls=Qt6VirtualKeyboard* ^
    --noinclude-dlls=Qt6Pdf* ^
    --noinclude-dlls=Qt6Test* ^
    --noinclude-dlls=Qt6Sensors* ^
    --noinclude-dlls=Qt6SerialPort* ^
    --noinclude-dlls=Qt6WebSockets* ^
    --noinclude-dlls=Qt6WebChannel* ^
    --noinclude-dlls=Qt6RemoteObjects* ^
    --noinclude-dlls=Qt6Scxml* ^
    --noinclude-dlls=Qt6Positioning* ^
    --noinclude-dlls=Qt6Location* ^
    --noinclude-dlls=Qt6Nfc* ^
    --noinclude-dlls=Qt6Bluetooth* ^
    --noinclude-dlls=opengl32sw.dll ^
    --remove-output ^
    distribute_server_gui.py
if errorlevel 1 (
    echo [错误] 打包失败，请查看上方 Nuitka 输出
    pause & exit /b 1
)
if exist dist\NoMY-ServerGUI rmdir /s /q dist\NoMY-ServerGUI
ren dist\distribute_server_gui.dist NoMY-ServerGUI
goto copy_config

:copy_config
rem 附带版本信息文件（exe 会在所在目录自动查找 Config\Version.ini）
if not exist "..\..\client_code\Config\Version.ini" goto done
if /i "%MODE%"=="gui" (
    if not exist "dist\NoMY-ServerGUI\Config" mkdir "dist\NoMY-ServerGUI\Config"
    copy /y "..\..\client_code\Config\Version.ini" "dist\NoMY-ServerGUI\Config\Version.ini" >nul
) else (
    if not exist "dist\NoMY-server\Config" mkdir "dist\NoMY-server\Config"
    copy /y "..\..\client_code\Config\Version.ini" "dist\NoMY-server\Config\Version.ini" >nul
)
echo [完成] 已附带版本信息文件

:done
echo.
echo ============================================================
echo [完成] 产物位于: %cd%\dist\
if /i "%MODE%"=="gui" (
    echo   NoMY-ServerGUI\NoMY-ServerGUI.exe  - 图形管理后台
) else (
    echo   NoMY-server\NoMY-server.exe        - 命令行版服务端
)
echo 提示: 整个产物文件夹（主程序 + 库文件）一起拷贝到目标机器使用。
echo ============================================================
pause
