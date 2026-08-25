#!/usr/bin/env bash
# Executor resiliente da quantizacao no WSL (F2).
# Lancado via `setsid` para sobreviver ao teardown do wrapper/sessao; usa
# arquivos-marcadores (RUNNING/DONE/FAILED) em vez de pgrep para status
# inequivoco. Log completo em quantize2.log.
set -u
WORK=$HOME/llm_local_batman
LOG="$WORK/quantize2.log"
MARK="$WORK/status"

mkdir -p "$MARK"
rm -f "$MARK"/DONE "$MARK"/FAILED "$MARK"/RUNNING
echo "$(date +%T)" > "$MARK/RUNNING"
echo "$$" > "$MARK/pid"

cd $HOME/llm_poc || { echo FAILED_CD > "$MARK/FAILED"; exit 1; }
# shellcheck disable=SC1091
source .venv/bin/activate

rm -rf "$WORK/gguf"
{
  echo "=== INICIO $(date +%T) ==="
  python "/mnt/c/Users/Rodrigo Vieira/Projeto 500/batman-os/scripts/llm_local/quantize.py" \
    --adapter "$WORK/checkpoints/final" \
    --out "$WORK/gguf" \
    --max-mem 0.55
  rc=$?
  echo "=== FIM $(date +%T) rc=$rc ==="
  if [ "$rc" -eq 0 ] && ls "$WORK"/gguf/*.gguf >/dev/null 2>&1; then
    ls -lh "$WORK"/gguf/*.gguf
    mv "$MARK/RUNNING" "$MARK/DONE"
  else
    echo "rc=$rc" > "$MARK/FAILED"
    rm -f "$MARK/RUNNING"
  fi
} >> "$LOG" 2>&1
