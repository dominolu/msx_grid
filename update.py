#!/usr/bin/env python3
"""
MSX Grid 自动更新脚本
从 GitHub 仓库下载最新代码并更新依赖
使用 ZIP 下载方式
"""

import os
import sys
import shutil
import tempfile
import zipfile
from pathlib import Path
from datetime import datetime
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
import json
import subprocess

# 仓库配置
REPO_OWNER = "dominolu"
REPO_NAME = "msx_grid"
BRANCH = "main"
GITHUB_API_BASE = "https://api.github.com"
GITHUB_ZIP_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/archive/refs/heads/{BRANCH}.zip"

# 颜色输出
class Colors:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

def print_info(msg):
    print(f"{Colors.BLUE}[INFO]{Colors.RESET} {msg}")

def print_success(msg):
    print(f"{Colors.GREEN}[SUCCESS]{Colors.RESET} {msg}")

def print_warning(msg):
    print(f"{Colors.YELLOW}[WARNING]{Colors.RESET} {msg}")

def print_error(msg):
    print(f"{Colors.RED}[ERROR]{Colors.RESET} {msg}")

def run_command(cmd, check=True):
    """执行 shell 命令"""
    try:
        result = subprocess.run(cmd, shell=True, check=check, capture_output=True, text=True)
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except subprocess.CalledProcessError as e:
        return None, str(e), e.returncode

def backup_config():
    """备份配置文件"""
    config_path = Path("config/config.yaml")
    if config_path.exists():
        backup_path = Path(f"config/config.yaml.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        shutil.copy2(config_path, backup_path)
        print_info(f"配置文件已备份到: {backup_path}")
        return backup_path
    return None

def restore_config(backup_path):
    """恢复配置文件"""
    if backup_path and backup_path.exists():
        config_path = Path("config/config.yaml")
        shutil.copy2(backup_path, config_path)
        print_info("配置文件已恢复")

def get_latest_commit_info():
    """获取最新 commit 信息（通过 API）"""
    api_url = f"{GITHUB_API_BASE}/repos/{REPO_OWNER}/{REPO_NAME}/commits/{BRANCH}"
    try:
        req = Request(api_url)
        req.add_header('User-Agent', 'MSX-Grid-Updater/1.0')
        req.add_header('Accept', 'application/vnd.github.v3+json')
        
        with urlopen(req) as response:
            data = json.loads(response.read().decode('utf-8'))
            return {
                'sha': data.get('sha', '')[:8],
                'message': data.get('commit', {}).get('message', '').split('\n')[0],
                'date': data.get('commit', {}).get('author', {}).get('date', '')
            }
    except Exception as e:
        print_warning(f"获取 commit 信息失败: {e}")
        return None

def download_file(url, dest_path, chunk_size=8192):
    """下载文件"""
    try:
        req = Request(url)
        req.add_header('User-Agent', 'MSX-Grid-Updater/1.0')
        
        with urlopen(req) as response:
            total_size = int(response.headers.get('Content-Length', 0))
            downloaded = 0
            
            with open(dest_path, 'wb') as f:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    
                    if total_size > 0:
                        percent = (downloaded / total_size) * 100
                        print(f"\r下载进度: {percent:.1f}% ({downloaded}/{total_size} bytes)", end='', flush=True)
            
            print()  # 换行
            return True
    except (URLError, HTTPError) as e:
        print_error(f"下载失败: {e}")
        return False
    except Exception as e:
        print_error(f"下载过程中出错: {e}")
        return False

def check_requirements_changed(old_requirements_path, new_requirements_path):
    """检查 requirements.txt 是否发生变化"""
    if not old_requirements_path.exists() or not new_requirements_path.exists():
        return True  # 如果文件不存在，认为有变化
    
    try:
        with open(old_requirements_path, 'r', encoding='utf-8') as f:
            old_content = f.read()
        with open(new_requirements_path, 'r', encoding='utf-8') as f:
            new_content = f.read()
        return old_content != new_content
    except Exception:
        return True  # 出错时认为有变化

def update_dependencies():
    """更新 Python 依赖"""
    print_info("检查依赖更新...")
    
    if not Path("requirements.txt").exists():
        print_warning("requirements.txt 不存在，跳过依赖更新")
        return
    
    print_info("正在更新 Python 依赖...")
    try:
        run_command(f"{sys.executable} -m pip install -r requirements.txt --upgrade")
        print_success("依赖更新完成")
    except Exception as e:
        print_error(f"依赖更新失败: {e}")

def update_via_zip():
    """通过下载 ZIP 文件更新"""
    print_info("使用 ZIP 下载方式更新...")
    
    # 获取最新版本信息
    commit_info = get_latest_commit_info()
    if commit_info:
        print_info(f"最新 commit: {commit_info['sha']} - {commit_info['message']}")
    
    # 创建临时目录
    temp_dir = Path(tempfile.mkdtemp(prefix="msx_grid_update_"))
    zip_path = temp_dir / "update.zip"
    
    try:
        # 下载 ZIP
        print_info("正在下载最新代码...")
        if not download_file(GITHUB_ZIP_URL, zip_path):
            return False
        
        # 解压 ZIP
        print_info("正在解压文件...")
        extract_dir = temp_dir / "extracted"
        extract_dir.mkdir()
        
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
        
        # 找到解压后的目录（通常是 repo_name-branch）
        extracted_dirs = list(extract_dir.iterdir())
        if not extracted_dirs:
            print_error("解压后未找到文件")
            return False
        
        source_dir = extracted_dirs[0]
        
        # 需要保留的文件和目录
        preserve_items = {
            'config/config.yaml',
            'logs',
            '__pycache__',
            '.git',
            'app.bin',
            'app.build',
            'app.dist',
            'app.onefile-build',
        }
        
        # 备份需要保留的文件
        preserve_backup = {}
        for item in preserve_items:
            item_path = Path(item)
            if item_path.exists():
                if item_path.is_file():
                    preserve_backup[item] = item_path.read_bytes()
                elif item_path.is_dir():
                    backup_dir = temp_dir / f"backup_{item.replace('/', '_')}"
                    shutil.copytree(item_path, backup_dir)
                    preserve_backup[item] = backup_dir
        
        # 检查 requirements.txt 是否有变化
        old_requirements = Path("requirements.txt")
        new_requirements = source_dir / "requirements.txt"
        requirements_changed = False
        if old_requirements.exists() and new_requirements.exists():
            requirements_changed = check_requirements_changed(old_requirements, new_requirements)
        
        # 复制新文件（排除需要保留的）
        print_info("正在更新文件...")
        files_updated = 0
        for root, dirs, files in os.walk(source_dir):
            # 跳过 .git 目录
            if '.git' in dirs:
                dirs.remove('.git')
            
            rel_root = Path(root).relative_to(source_dir)
            target_root = Path(rel_root)
            
            for file in files:
                source_file = Path(root) / file
                target_file = target_root / file
                
                # 跳过需要保留的文件
                skip = False
                for preserve in preserve_items:
                    if str(target_file).startswith(preserve):
                        skip = True
                        break
                
                if skip:
                    continue
                
                # 确保目标目录存在
                target_file.parent.mkdir(parents=True, exist_ok=True)
                
                # 复制文件
                shutil.copy2(source_file, target_file)
                files_updated += 1
        
        print_success(f"已更新 {files_updated} 个文件")
        
        # 恢复保留的文件
        for item, backup in preserve_backup.items():
            item_path = Path(item)
            if isinstance(backup, bytes):
                item_path.parent.mkdir(parents=True, exist_ok=True)
                item_path.write_bytes(backup)
            elif isinstance(backup, Path):
                if item_path.exists():
                    if item_path.is_file():
                        item_path.unlink()
                    else:
                        shutil.rmtree(item_path)
                shutil.move(str(backup), str(item_path))
        
        return True, requirements_changed
        
    except Exception as e:
        print_error(f"ZIP 更新过程中出错: {e}")
        return False, False
    finally:
        # 清理临时文件
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass

def main():
    """主函数"""
    print(f"{Colors.BOLD}=== MSX Grid 自动更新脚本 ==={Colors.RESET}\n")
    
    # 备份配置
    backup_path = backup_config()
    
    # 确认更新
    commit_info = get_latest_commit_info()
    if commit_info:
        print_info(f"最新版本: {commit_info['sha']} - {commit_info['message']}")
    
    response = input("\n是否继续更新? (y/n): ").strip().lower()
    if response != 'y':
        print_info("更新已取消")
        return
    
    # 执行更新
    success, requirements_changed = update_via_zip()
    
    # 处理依赖更新
    if success:
        if requirements_changed:
            print_info("检测到 requirements.txt 有变化")
            response = input("是否更新 Python 依赖? (y/n): ").strip().lower()
            if response == 'y':
                update_dependencies()
        
        print_success("\n更新完成！")
        print_info("建议:")
        print_info("1. 检查配置文件 config/config.yaml 是否需要调整")
        print_info("2. 如有备份文件，可手动对比配置差异")
        print_info("3. 重启应用服务以应用更新")
    else:
        # 恢复配置
        if backup_path:
            restore_config(backup_path)
        print_error("更新失败，配置文件已恢复")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n更新已中断")
        sys.exit(1)
    except Exception as e:
        print_error(f"更新过程中发生错误: {e}")
        sys.exit(1)
