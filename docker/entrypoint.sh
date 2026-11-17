#!/bin/sh
# 컨테이너 시작 시 root로 실행되어, 볼륨·바인드 마운트(/data, /app/results)의 소유자를 temi로 맞춘 뒤
# temi(uid 10001) 권한으로 내려가 서버를 실행한다. (호스트에서 results 폴더 chown을 따로 할 필요 없음)
set -eu
if [ "$(id -u)" = "0" ]; then
  mkdir -p /data /app/results
  chown -R temi:temi /data /app/results 2>/dev/null || echo "[entrypoint] 경고: /data 또는 /app/results 소유자 변경 실패"
  exec setpriv --reuid=temi --regid=temi --init-groups "$@"
fi
exec "$@"
