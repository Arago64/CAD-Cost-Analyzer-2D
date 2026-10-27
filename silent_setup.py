"""Optional Python-based console-free first-run installer for Windows .vbs launcher.

This requires a working Python installation. The PyInstaller EXE does NOT.
"""
from __future__ import annotations
import datetime as dt
import os
from pathlib import Path
import subprocess
import sys
import traceback
import venv

ROOT=Path(__file__).resolve().parent
LOG=Path(os.environ.get('CAD2D_LOG_PATH') or ROOT/(dt.datetime.now().strftime('%Y-%m-%d_%H-%M-%S-%f')+'.txt'))
HIDE=getattr(subprocess,'CREATE_NO_WINDOW',0)


def run(args,log,env=None):
    log.write('COMMAND: '+ ' '.join(map(str,args))+'\n')
    log.flush()
    done=subprocess.run(list(map(str,args)),cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,
                        creationflags=HIDE,env=env,check=False)
    if done.returncode:
        raise RuntimeError(f'Command failed (exit {done.returncode}): {args}')


def main():
    with LOG.open('a',encoding='utf-8',buffering=1) as log:
        try:
            log.write('CAD2D silent start: '+dt.datetime.now().isoformat()+'\n')
            venv_python=ROOT/'.venv'/'Scripts'/'python.exe'
            venv_pythonw=ROOT/'.venv'/'Scripts'/'pythonw.exe'
            if not venv_python.exists():
                log.write('Creating Python virtual environment\n')
                venv.EnvBuilder(with_pip=True).create(ROOT/'.venv')
            stamp=ROOT/'.venv'/'cad2d_requirements_ok.txt'
            requirements=ROOT/'requirements.txt'
            current=requirements.read_text(encoding='utf-8')
            if not stamp.exists() or stamp.read_text(encoding='utf-8')!=current:
                run([venv_python,'-m','pip','install','-r',requirements],log)
                stamp.write_text(current,encoding='utf-8')
            env=os.environ.copy()
            env['CAD2D_LOG_PATH']=str(LOG)
            command=[venv_pythonw if venv_pythonw.exists() else venv_python,
                     ROOT/'desktop_launcher.py']
            subprocess.Popen(list(map(str,command)),cwd=ROOT,
                             env=env,creationflags=HIDE,stdin=subprocess.DEVNULL,
                             stdout=log,stderr=log,close_fds=True)
            log.write('Desktop application process launched\n')
        except Exception:
            traceback.print_exc(file=log)
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(None,'Startup failed. Log:\n'+str(LOG),
                    'CAD Cost Analyzer',0x10)
            except Exception:
                pass


if __name__=='__main__':
    main()
