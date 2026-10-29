"""美股 K 线复盘训练 PyWebView 桌面版入口。"""

import argparse
import logging
import os
import sys
import threading
import time
import socket
import tempfile
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch


APP_VERSION = "v3.3.0-us.3"


def get_project_root():
    """Locate portable data folders for source, onedir, and onefile launches."""
    if getattr(sys, 'frozen', False):
        executable_dir = Path(sys.executable).resolve().parent
        if executable_dir.name.lower() == 'bin':
            return executable_dir.parent
        if executable_dir.parent.name.lower() == 'bin':
            return executable_dir.parent.parent
        return executable_dir
    return Path(__file__).resolve().parent.parent


project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from backend.startup_diagnostics import configure_startup_diagnostics, report_startup_failure

configure_startup_diagnostics('KLineTrainer_US', APP_VERSION, get_project_root())

try:
    import webview
except Exception as startup_error:
    report_startup_failure(
        f'无法加载桌面窗口组件：{type(startup_error).__name__}: {startup_error}',
        '美股 K 线复盘训练器启动失败',
        startup_error,
    )
    raise

# 导入后端应用
try:
    from backend import app_enhanced as backend_app
except Exception as startup_error:
    report_startup_failure(
        f'无法加载训练服务：{type(startup_error).__name__}: {startup_error}',
        '美股 K 线复盘训练器启动失败',
        startup_error,
    )
    raise

app = backend_app.app
app_project_root = backend_app.project_root

class KLineTrainerApp:
    def __init__(self):
        self.flask_app = app
        self.server = None
        self.server_error = None
        self.port = self.find_free_port()
        
    def find_free_port(self):
        """查找可用端口"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('127.0.0.1', 0))
            return s.getsockname()[1]
        
    def start_flask_server(self):
        """启动Flask服务器"""
        try:
            # 确保数据目录存在
            base_dir = Path(app_project_root)
            data_dir = base_dir / 'data'
            users_dir = base_dir / 'users_us'
            data_dir.mkdir(exist_ok=True)
            users_dir.mkdir(exist_ok=True)
            
            # 启动Flask应用
            self.flask_app.run(host='127.0.0.1', port=self.port, debug=False, use_reloader=False)
        except Exception as e:
            self.server_error = e
            logging.exception("Flask server failed to start")
    
    def get_frontend_url(self):
        """获取前端URL"""
        return f"http://127.0.0.1:{self.port}"
    
    def check_dependencies(self):
        """检查依赖包"""
        required_packages = [
            'flask',
            'flask-cors',
            'pandas',
            'numpy',
            'yfinance',
            'pywebview'
        ]
        
        missing_packages = []
        
        for package in required_packages:
            try:
                if package == 'pywebview':
                    import webview
                else:
                    __import__(package.replace('-', '_'))
            except ImportError:
                missing_packages.append(package)
        
        if missing_packages:
            raise RuntimeError(f"缺少依赖包: {', '.join(missing_packages)}")
        
        return True

def get_resource_path(relative_path):
    """ 获取资源的绝对路径，兼容开发环境和 PyInstaller 打包环境 """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # 打包后环境
        base_path = Path(sys._MEIPASS)
    else:
        # 开发环境
        base_path = Path(__file__).parent.parent # 从 main_pywebview.py 向上两级到项目根目录

    return base_path / relative_path


def run_packaged_smoke_test():
    """Run an offline end-to-end Flask check without creating a GUI window."""
    from backend.data_manager import DataManager
    from backend.user_manager_enhanced import UserManagerEnhanced

    smoke_log = Path(app_project_root) / 'data' / 'package-smoke-main.log'
    smoke_log.parent.mkdir(parents=True, exist_ok=True)
    checks = []
    try:
        smoke_temp_root = Path(app_project_root) / 'data'
        smoke_temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='kline-us-smoke-', dir=str(smoke_temp_root)) as temp_root:
            manager = DataManager(temp_root)
            Path(manager.offline_dir, 'AAPL.csv').write_text(
                'date,open,high,low,close,volume\n'
                '2024-01-02,99,101,98,100,1000\n'
                '2024-01-03,100,102,99,101,1100\n'
                '2024-01-04,101,103,100,102,1200\n',
                encoding='utf-8',
            )
            users_dir = str(Path(temp_root) / 'users_us')
            users = UserManagerEnhanced(users_dir=users_dir)
            users.create_user('package-smoke')

            with patch.object(backend_app, 'data_manager', manager), \
                    patch.object(backend_app, 'user_manager', users), \
                    patch.object(backend_app, 'users_dir_path', users_dir), \
                    patch.object(backend_app, 'active_trainings', {}), \
                    patch.object(backend_app, '_update_api_info'):
                client = app.test_client()
                home = client.get('/')
                assert home.status_code == 200 and '美股 K 线复盘训练'.encode() in home.data
                checks.append('static frontend')

                sources = client.get('/api/data/sources')
                assert sources.status_code == 200
                assert any(item['value'] == 'yfinance' for item in sources.get_json()['sources'])
                checks.append('yfinance module and source API')

                started = client.post('/api/training/start', json={
                    'user': 'package-smoke',
                    'mode': 'specified',
                    'stock_code': 'AAPL',
                    'start_date': '2024-01-02',
                    'data_source': 'offline',
                    'initial_capital': 1000,
                })
                assert started.status_code == 200, started.get_json()
                training_id = started.get_json()['id']
                training_data = client.get(f'/api/training/{training_id}/data')
                assert training_data.status_code == 200 and training_data.get_json()['kline_data']
                checks.append('offline training start and data API')

                bought = client.post(f'/api/training/{training_id}/trade', json={
                    'action': 'buy', 'quantity': 1,
                })
                assert bought.status_code == 200, bought.get_json()
                sold = client.post(f'/api/training/{training_id}/trade', json={
                    'action': 'sell', 'quantity': 1,
                })
                assert sold.status_code == 200, sold.get_json()
                ended = client.post(f'/api/training/{training_id}/end')
                assert ended.status_code == 200, ended.get_json()
                checks.append('whole-share same-day buy/sell and report API')

                # Verify the packaged timezone data and hourly replay path with
                # controlled cached bars; packaging checks never require Yahoo.
                import pandas as pd
                smoke_day = pd.Timestamp.now(tz='America/New_York').tz_localize(None).normalize() - pd.Timedelta(days=14)
                while smoke_day.weekday() >= 5:
                    smoke_day -= pd.Timedelta(days=1)
                hourly_rows = pd.DataFrame({
                    'date': [smoke_day + pd.Timedelta(hours=9, minutes=30),
                             smoke_day + pd.Timedelta(hours=10, minutes=30),
                             smoke_day + pd.Timedelta(hours=11, minutes=30)],
                    'open': [100, 101, 102], 'high': [102, 103, 104],
                    'low': [99, 100, 101], 'close': [101, 102, 103],
                    'volume': [1000, 1100, 1200],
                })
                manager._save_hourly_kline('AAPL', hourly_rows)
                manager._refreshed_hourly_symbols.add('AAPL')
                started = client.post('/api/training/start', json={
                    'user': 'package-smoke', 'mode': 'specified', 'stock_code': 'AAPL',
                    'start_date': smoke_day.strftime('%Y-%m-%d'), 'period': 'hourly',
                    'data_source': 'yfinance', 'initial_capital': 1000,
                })
                assert started.status_code == 200, started.get_json()
                hourly_id = started.get_json()['id']
                initial = client.get(f'/api/training/{hourly_id}/data?view_period=hourly').get_json()
                assert len(initial['kline_data']) == 1, initial
                advanced = client.post(f'/api/training/{hourly_id}/next')
                assert advanced.status_code == 200, advanced.get_json()
                visible = client.get(f'/api/training/{hourly_id}/data?view_period=hourly').get_json()
                assert len(visible['kline_data']) == 2, visible
                assert visible['kline_data'][1]['time'] - visible['kline_data'][0]['time'] == 3600
                daily_view = client.get(f'/api/training/{hourly_id}/data?view_period=daily').get_json()
                assert len(daily_view['kline_data']) == 1
                assert daily_view['kline_data'][0]['close'] == 102
                checks.append('hourly cached replay, timezone and visible-only daily aggregation')

        message = 'PASS: ' + '; '.join(checks)
        smoke_log.write_text(message + '\n', encoding='utf-8')
        if sys.stdout:
            print(message)
    except Exception as exc:
        message = f'FAIL after {", ".join(checks) or "startup"}: {type(exc).__name__}: {exc}'
        smoke_log.write_text(message + '\n', encoding='utf-8')
        if sys.stderr:
            print(message, file=sys.stderr)
        raise

def main():
    """主函数"""
    print()
    print("=" * 60)
    print(f"美股K线复盘训练 - 桌面版 {APP_VERSION}")
    print("=" * 60)
    print()
    
    app_instance = KLineTrainerApp()
    
    # 检查依赖
    print("检查依赖包...")
    if not app_instance.check_dependencies():
        sys.exit(1)
    print("依赖检查完成")
    print()
    
    # 启动Flask服务器（在后台线程中）
    print("启动后端服务...")
    flask_thread = threading.Thread(target=app_instance.start_flask_server, daemon=True)
    flask_thread.start()
    
    # 等待服务启动
    print("等待服务启动...")
    max_retries = 30
    is_ready = False
    health_url = f"http://127.0.0.1:{app_instance.port}/api/health"
    
    for _ in range(max_retries):
        if app_instance.server_error is not None:
            raise RuntimeError(f"后端服务启动失败：{type(app_instance.server_error).__name__}: {app_instance.server_error}") from app_instance.server_error
        try:
            response = urllib.request.urlopen(health_url, timeout=1)
            if response.status == 200:
                is_ready = True
                break
        except (urllib.error.URLError, socket.timeout):
            pass
        time.sleep(0.5)
        
    if not is_ready:
        raise RuntimeError(f"后端服务启动超时，无法在端口 {app_instance.port} 访问。")
        
    print("服务已就绪！")
    
    # 创建PyWebView窗口
    print("启动桌面应用...")
    
    try:
        # 创建窗口
        window = webview.create_window(
            title=f'美股K线复盘训练 - 桌面版 {APP_VERSION}',
            url=f"http://127.0.0.1:{app_instance.port}/",
            width=1400,
            height=900,
            # min_size=(1200, 800),
            # maximized=True,
            # resizable=False,
            # on_top=False,
        )
        
        print()
        print("=" * 60)
        print("桌面应用已启动！")
        print(f"后端API: http://127.0.0.1:{app_instance.port}/api")
        print("关闭窗口即可退出程序")
        print("=" * 60)
        
        # 启动PyWebView
        # webview.start(debug=True)
        webview.start(debug=False)
        
    except Exception as e:
        logging.exception("PyWebView window startup failed")
        raise RuntimeError(f"桌面窗口启动失败：{type(e).__name__}: {e}") from e
    
    print("\n程序已退出")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='美股 K 线复盘训练器')
    parser.add_argument('--smoke', action='store_true', help='无界面运行离线启动和 Flask API 检查')
    options, _ = parser.parse_known_args()
    try:
        if options.smoke:
            run_packaged_smoke_test()
        else:
            main()
    except SystemExit as startup_error:
        if startup_error.code not in (None, 0):
            report_startup_failure(
                f'程序异常退出（代码 {startup_error.code}）。',
                '美股 K 线复盘训练器启动失败',
            )
        raise
    except Exception as startup_error:
        report_startup_failure(
            f'{type(startup_error).__name__}: {startup_error}',
            '美股 K 线复盘训练器启动失败',
            startup_error,
        )
        raise

