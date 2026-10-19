@echo off
cd /d "%~dp0"
where pyw >/dev/null 2>/dev/null && (start "" pyw lotto_app.py) || (start "" pythonw lotto_app.py)
