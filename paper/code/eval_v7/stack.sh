#!/usr/bin/env bash
# Launch / stop / check an isolated API stack for one worker.
#
#   stack.sh up    <worker> [domain]   # domain: bio (default) | retail
#   stack.sh down  <worker>
#   stack.sh check <worker> [domain]
#
# Ports are 8000 + worker*10 + 1..N; DBs live under /tmp/aob_w<worker>. The agent always
# addresses canonical 8001-800N whatever the worker, so prompts and traces are identical.
# Convention: workers 0-3 = bio (8 APIs), workers 5-8 = retail (4 APIs).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"          # .../code
PY="${AOB_PYTHON:-$(cd "$ROOT/.." && pwd)/env/bin/python}"

cmd="${1:?usage: stack.sh up|down|check <worker> [domain]}"
w="${2:?worker number}"
domain="${3:-}"
base=$((8000 + w * 10))
dbdir="/tmp/aob_w${w}"
sess="aob_stack_w${w}"
[ -z "$domain" ] && { [ "$w" -ge 5 ] && domain=retail || domain=bio; }

if [ "$domain" = "retail" ]; then
  names=(orders inventory customers tickets)
  app="$ROOT/agentorchbench/apis/domain2_retail/app.py"
else
  names=(transfer registry catalog task pipeline publish meta galaxy)
  dirs=(transfer_api registry_api catalog_api task_api pipeline_api publish_api meta_api galaxy_api)
fi

case "$cmd" in
  up)
    tmux kill-session -t "$sess" 2>/dev/null
    rm -rf "$dbdir"; mkdir -p "$dbdir"
    for i in "${!names[@]}"; do
      n="${names[$i]}"; p=$((base + i + 1))
      if [ "$domain" = "retail" ]; then
        run="cd $(dirname "$app") && ROLE=$n PORT=$p DB_PATH=$dbdir/${n}.db $PY app.py > $dbdir/${n}.log 2>&1"
      else
        run="cd $ROOT/agentorchbench/apis/${dirs[$i]} && PORT=$p DB_PATH=$dbdir/${n}.db $PY app.py > $dbdir/${n}.log 2>&1"
      fi
      if [ "$i" = 0 ]; then tmux new-session -d -s "$sess" -n "$n" "$run"
      else tmux new-window -t "$sess" -n "$n" "$run"; fi
    done
    sleep 6
    "$0" check "$w" "$domain"
    ;;
  down)
    tmux kill-session -t "$sess" 2>/dev/null && echo "stopped $sess" || echo "$sess not running"
    ;;
  check)
    bad=0
    for i in "${!names[@]}"; do
      p=$((base + i + 1))
      if ! curl -sf -m 3 "http://127.0.0.1:$p/health" >/dev/null; then
        echo "FAIL :$p (${names[$i]}) — $(tail -1 "$dbdir/${names[$i]}.log" 2>/dev/null | head -c 140)"
        bad=$((bad + 1))
      fi
    done
    if [ "$bad" = 0 ]; then echo "worker $w ($domain) OK: ports $((base+1))-$((base+${#names[@]}))"
    else echo "worker $w ($domain): $bad API(s) down"; exit 1; fi
    ;;
  *) echo "unknown command $cmd" >&2; exit 2 ;;
esac
