#!/bin/sh
# OLLAMA_PULL_MODELS(쉼표 구분)의 모델을 받는다. ollama-init 컨테이너에서 실행.
set -eu
: "${OLLAMA_PULL_MODELS:?OLLAMA_PULL_MODELS가 비었습니다}"
echo "[pull] 대상: ${OLLAMA_PULL_MODELS}"
old_ifs=$IFS
IFS=','
for m in $OLLAMA_PULL_MODELS; do
  IFS=$old_ifs
  m=$(echo "$m" | tr -d '[:space:]')
  [ -z "$m" ] && continue
  echo "[pull] $m"
  ollama pull "$m"
done
echo "[pull] 완료"
ollama list
