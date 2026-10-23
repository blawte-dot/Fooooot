#!/bin/sh
# تحديث كود التطبيق على الهاتف من GitHub بأمر واحد (يحتاج GITHUB_DATA_TOKEN في ~/.keys بصلاحية Contents: Read فقط).
[ -f "$HOME/.keys" ] && . "$HOME/.keys"
[ -z "$GITHUB_DATA_TOKEN" ] && { echo "ضع GITHUB_DATA_TOKEN في ~/.keys أولًا"; exit 1; }
REPO="${GITHUB_REPO:-blawte-dot/Fooooot}"; cd "$(dirname "$0")" || exit 1
T="$(mktemp -d)"; curl -fsSL -H "Authorization: Bearer $GITHUB_DATA_TOKEN" "https://api.github.com/repos/$REPO/zipball/main" -o "$T/c.zip" || { echo "فشل التنزيل"; exit 1; }
unzip -q -o "$T/c.zip" -d "$T/x" && cp -r "$T"/x/*/. . && rm -rf "$T" && echo "تم التحديث. أعد تشغيل: sh start.sh"
