"""Desktop entrypoint for PyInstaller and normal Python (no visible terminal on Windows).

All startup output and uncaught errors are retained in YYYY-MM-DD_HH-MM-SS-ffffff.txt
beside the executable, or in LOCALAPPDATA if that directory is read-only.
"""
from __future__ import annotations
import datetime as dt
import faulthandler
import logging
import os
from pathlib import Path
import socket
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser


def executable_directory():
    return Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent


def log_file():
    name=dt.datetime.now().strftime('%Y-%m-%d_%H-%M-%S-%f')+'.txt'
    preferred=executable_directory()/name
    try:
        with preferred.open('x',encoding='utf-8'):
            pass
        return preferred
    except OSError:
        base=Path(os.environ.get('LOCALAPPDATA') or Path.home())/'CADCostAnalyzer2D'/'logs'
        base.mkdir(parents=True,exist_ok=True)
        return base/name


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0))
        return s.getsockname()[1]


def browser_when_ready(port):
    url=f'http://127.0.0.1:{port}/'
    for _ in range(180):
        try:
            with urllib.request.urlopen(url,timeout=1.) as r:
                if r.status<500:
                    webbrowser.open(url)
                    return
        except Exception:
            pass
        time.sleep(1.)
    logging.error('Browser was not opened: local server did not become ready: %s',url)


def main():
    logfile=Path(os.environ.get('CAD2D_LOG_PATH') or log_file())
    logfile.parent.mkdir(parents=True,exist_ok=True)
    with logfile.open('a',encoding='utf-8',buffering=1) as stream:
        oldout,olderr=sys.stdout,sys.stderr
        sys.stdout=stream
        sys.stderr=stream
        logging.basicConfig(stream=stream,level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s: %(message)s',force=True)
        try:
            faulthandler.enable(file=stream)
        except OSError:
            pass
        print('\n=== CAD Cost Analyzer 2D desktop ===',flush=True)
        print('Started:',dt.datetime.now().isoformat(),flush=True)
        print('Executable:',sys.executable,flush=True)
        print('Python:',sys.version,flush=True)
        print('Log:',logfile,flush=True)
        try:
            if getattr(sys,'frozen',False):
                app_dir=Path(sys._MEIPASS)/'app'
            else:
                app_dir=Path(__file__).resolve().parent
            script=app_dir/'app.py'
            if not script.exists():
                raise FileNotFoundError(f'Application source not found: {script}')
            os.chdir(app_dir)
            sys.path.insert(0,str(app_dir))
            os.environ.setdefault('STREAMLIT_BROWSER_GATHER_USAGE_STATS','false')
            os.environ.setdefault('STREAMLIT_SERVER_HEADLESS','true')
            port=free_port()
            print('Launching:',script,'port:',port,flush=True)
            threading.Thread(target=browser_when_ready,args=(port,),daemon=True).start()
            from streamlit.web import bootstrap
            bootstrap.run(str(script),False,[],{
                'server.address':'127.0.0.1',
                'server.port':port,
                'server.headless':True,
                'server.fileWatcherType':'none',
                'browser.gatherUsageStats':False,
                'global.developmentMode':False,
            })
        except BaseException:
            logging.critical('Startup terminated with exception:')
            traceback.print_exc(file=stream)
            if os.name=='nt':
                try:
                    import ctypes
                    ctypes.windll.user32.MessageBoxW(None,
                        'CAD Cost Analyzer could not start. Details in:\n'+str(logfile),
                        'CAD Cost Analyzer — startup error',0x10)
                except Exception:
                    pass
            raise
        finally:
            print('Stopped:',dt.datetime.now().isoformat(),flush=True)
            sys.stdout=oldout
            sys.stderr=olderr


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--v31-balanced':
        if getattr(sys,'frozen',False):
            sys.path.insert(0,str(Path(sys._MEIPASS)/'app'))
        from v31_balanced_worker import worker as balanced_worker
        if len(sys.argv)!=4:raise SystemExit(2)
        balanced_worker(sys.argv[2],sys.argv[3])
    elif len(sys.argv)>1 and sys.argv[1]=='--v3-worker':
        if getattr(sys,'frozen',False):
            sys.path.insert(0,str(Path(sys._MEIPASS)/'app'))
        from v3_bounded import worker
        if len(sys.argv)!=4: raise SystemExit(2)
        worker(sys.argv[2],sys.argv[3])
    elif len(sys.argv)>1 and sys.argv[1]=='--cad-search-worker':
        # PyInstaller child process executes ONLY the optimizer; it must not
        # reopen the browser or start Streamlit.
        if getattr(sys,'frozen',False):
            sys.path.insert(0,str(Path(sys._MEIPASS)/'app'))
        from bounded_search import worker_cli
        worker_cli()
    else:
        main()
