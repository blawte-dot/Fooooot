#!/bin/sh
# تشغيل التطبيق على الهاتف (Termux) بأمر واحد. يعمل فقط ما دمت فاتحه: أغلق Termux أو اضغط Ctrl+C فيتوقف.
cd "$(dirname "$0")" || exit 1
[ -f "$HOME/.keys" ] && . "$HOME/.keys"            # ملف مفاتيحك الخاص: export PITCHAPI_KEY=... و GITHUB_DATA_TOKEN=... (لا ترفعه لأي مكان)
command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock
trap 'command -v termux-wake-unlock >/dev/null 2>&1 && termux-wake-unlock' EXIT
( sleep 4; command -v termux-open-url >/dev/null 2>&1 && termux-open-url "http://127.0.0.1:8000" ) &
exec gunicorn app:app --bind 127.0.0.1:8000 --workers 1 --threads 4 --timeout 180
