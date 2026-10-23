#!/bin/sh
# تشغيل التطبيق على الهاتف (Termux). يعمل فقط ما دمت فاتحه: أغلق Termux أو اضغط Ctrl+C فيتوقف. لا شيء يعمل في الخلفية.
cd "$(dirname "$0")" || exit 1
[ -f "$HOME/.keys" ] && . "$HOME/.keys"      # ملف مفاتيحك الخاص (لا يُرفع لأي مكان): PITCHAPI_KEY و GEMINI_API_KEY و GITHUB_DATA_TOKEN
[ -z "$PITCHAPI_KEY" ] && echo "تنبيه: PITCHAPI_KEY غير موجود في ~/.keys"
export DATA_RUNTIME_DIR="${DATA_RUNTIME_DIR:-$HOME/.matchpredict-data}"   # مجلد دائم لبيانات التحديث، فلا تُحمَّل من جديد كل مرة
mkdir -p "$DATA_RUNTIME_DIR"
command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock
trap 'command -v termux-wake-unlock >/dev/null 2>&1 && termux-wake-unlock' EXIT
( sleep 4; command -v termux-open-url >/dev/null 2>&1 && termux-open-url "http://127.0.0.1:8000" ) &
if command -v gunicorn >/dev/null 2>&1; then
  exec gunicorn app:app --bind 127.0.0.1:8000 --workers 1 --threads 4 --timeout 180
else
  exec python -c "from app import app; app.run(host='127.0.0.1', port=8000, threaded=True)"
fi
