#!/usr/bin/env python
"""
Nuitka 打包脚本

用法：
    # 先激活 venv
    # 然后：
    python build.py

功能：
    - 从 msx/grid.py 读取 VERSION 作为产品和文件版本
    - 自动识别平台 (Windows / macOS / 其它)
    - 调用 Nuitka 生成单文件可执行程序
"""

import os
import re
import sys
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ENTRY = ROOT / "app.py"
GRID_FILE = ROOT / "msx" / "grid.py"
PRODUCT_NAME = "MSX Grid"
COMPANY_NAME = "Dominic"
DESCRIPTION = "MSX.com RWA 网格交易控制台"


def read_version() -> str:
    """
    从 msx/grid.py 中读取 VERSION = "x.y.z"
    """
    text = GRID_FILE.read_text(encoding="utf-8")
    m = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if not m:
        raise RuntimeError("在 msx/grid.py 中未找到 VERSION 常量")
    return m.group(1)


def build():
    if not ENTRY.exists():
        raise SystemExit(f"入口文件不存在: {ENTRY}")

    version = read_version()
    print(f"[*] 检测到版本号 VERSION = {version}")

    # 基础 Nuitka 命令
    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--onefile",
        "--follow-imports",
        "--show-progress",
        "--show-memory",
        # 打包静态资源和配置目录，保证 onefile 解压后的临时目录中也有这些文件
        "--include-data-dir=static=static",
        "--include-data-dir=config=config",
        str(ENTRY),
    ]

    # 平台特定配置
    if sys.platform.startswith("win"):
        print("[*] 检测到平台: Windows")
        cmd += [
            f'--product-name={PRODUCT_NAME}',
            f'--file-version={version}',
            f'--product-version={version}',
            f'--company-name={COMPANY_NAME}',
            f'--file-description={DESCRIPTION}',
            # 如果有图标，可以取消注释并修改路径
            # r'--windows-icon-from-ico=static\icon.ico',
        ]
    elif sys.platform == "darwin":
        print("[*] 检测到平台: macOS")
        cmd += [
            f'--product-name={PRODUCT_NAME}',
            f'--file-version={version}',
            f'--product-version={version}',
            f'--company-name={COMPANY_NAME}',
            f'--file-description={DESCRIPTION}',
            # 如果要生成 .app，可考虑加：
            # '--macos-create-app-bundle',
            # '--macos-app-name=MSX Grid',
            # '--macos-app-icon=static/icon.icns',
        ]
    else:
        print(f"[*] 检测到平台: {sys.platform}（使用通用配置）")
        cmd += [
            f'--product-name={PRODUCT_NAME}',
            f'--file-version={version}',
            f'--product-version={version}',
        ]

    print("[*] 即将执行 Nuitka 命令：")
    print(" ".join(cmd))

    # 实际执行
    subprocess.run(cmd, check=True)
    print("[*] 打包完成")


if __name__ == "__main__":
    try:
        build()
    except subprocess.CalledProcessError as e:
        print(f"[!] Nuitka 打包失败，退出码：{e.returncode}")
        sys.exit(e.returncode)
    except Exception as e:
        print(f"[!] 打包脚本出错：{e}")
        sys.exit(1)