@echo off
rem ============================================================
rem NoMY 主程序（客户端）打包脚本（Nuitka standalone 目录模式）
rem
rem 用法：双击运行，或在项目根目录执行 build_app.bat
rem 产物：dist\NoMY\NoMY.exe（主程序 + 同目录依赖库/资源文件夹）
rem
rem 说明：
rem   - 优先使用项目 .venv 虚拟环境打包（依赖齐全）；
rem   - Config\（含版本/网络/界面配置）与 plugins\（外置插件）作为数据目录打进包；
rem     注意：打包前如需分发给别人，请清理 Config\config.json 中的个人令牌；
rem   - 首次打包 Nuitka 会自动下载 C 编译器（MinGW64），PySide6 体量大，
rem     编译可能需要 10-30 分钟。
rem ============================================================
setlocal
cd /d "%~dp0"

rem 优先使用项目虚拟环境（依赖与 requirements.txt 一致）
if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=%cd%\.venv\Scripts\python.exe"
    echo [环境] 使用项目虚拟环境: %cd%\.venv
) else (
    set "PYTHON_EXE=python"
    echo [环境] 未找到 .venv，使用系统 Python
)

%PYTHON_EXE% -c "import PySide6, qfluentwidgets, markdown, numpy, PIL, psutil, openai, cryptography" >nul 2>nul
if errorlevel 1 (
    echo [错误] 当前 Python 缺少必要依赖，请先执行: pip install -r requirements.txt
    pause & exit /b 1
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

echo.
echo [Nuitka] 开始打包主程序 fluwidget.py（PySide6 体量大，可能需要 10-30 分钟）...
%PYTHON_EXE% -m nuitka ^
    --standalone ^
    --assume-yes-for-downloads ^
    --enable-plugin=pyside6 ^
    --include-package-data=qfluentwidgets ^
    --include-package-data=certifi ^
    --include-qt-plugins=sensible,multimedia ^
    --windows-console-mode=disable ^
    --output-dir=dist ^
    --output-filename=NoMY.exe ^
    --company-name=NoMY ^
    --product-name=NoMY ^
    --file-version=1.0.0.0 ^
    --product-version=1.0.0.0 ^
    --windows-icon-from-ico=Config\image\menu.ico ^
    --include-data-dir=Config=Config ^
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
    fluwidget.py
if errorlevel 1 (
    echo [错误] 打包失败，请查看上方 Nuitka 输出
    pause & exit /b 1
)

if exist dist\NoMY rmdir /s /q dist\NoMY
ren dist\fluwidget.dist NoMY
rem 外置插件目录（含 .py，Nuitka 数据目录会跳过，构建后复制）
xcopy plugins dist\NoMY\plugins\ /e /i /y >nul
rem 复制 QtMultimedia 运行时动态加载的 ffmpeg 解码器 DLL
for /f "delims=" %%i in ('%PYTHON_EXE% -c "import PySide6, os; print(os.path.dirname(PySide6.__file__))"') do set "PYSIDE_DIR=%%i"
copy /y "%PYSIDE_DIR%\avcodec-*.dll" dist\NoMY\ >nul
copy /y "%PYSIDE_DIR%\avutil-*.dll" dist\NoMY\ >nul

echo.
echo ============================================================
echo [完成] 产物: %cd%\dist\NoMY\NoMY.exe
echo 提示: 整个 NoMY 文件夹（主程序 + 库文件 + Config + plugins）一起拷贝使用。
echo ============================================================
pause
