#!/bin/bash
# orchestrator.sh — เปิด tmux session ทีม agent สำหรับ carr-intmbslc-ab64-ha
#
# แต่ละ pane รัน `claude` แยก process กันจริง (คนละ session ไม่ใช่ Task-tool subagent)
# แล้ว bootstrap ข้อความแรกให้แต่ละ pane อ่านไฟล์ role ของตัวเองใน .claude/agents/
# และรับบทบาทนั้นตลอด session — planner สั่งงาน pane อื่นผ่าน .claude/shared/dispatch.sh
#
# Usage:
#   ./orchestrator.sh                     เปิด session ใหม่ทั้งชุด (ถ้ามีอยู่แล้วจะไม่ทำอะไร)
#   ./orchestrator.sh --respawn-workers   ฆ่า+เกิดใหม่เฉพาะ 4 pane worker โดยไม่แตะ planner
#                                         (ใช้เมื่อ role ของ worker เพี้ยน หรืออยากได้ context สะอาด
#                                          โดยไม่เสียบทสนทนาของ planner ที่ถือแผนอยู่)
#
# ทำไมต้องมี --respawn-workers: planner รันอยู่ใน pane ของ session นี้เอง
# ถ้า kill-session ทั้งชุด planner จะตายไปด้วยและเสีย context แผนทั้งหมด

set -euo pipefail

SESSION="ab64-team"
WINDOW="team"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# role file ใน .claude/agents/, model ที่ใช้, และค่า @role ที่ dispatch.sh ใช้ค้นหา pane
# index 0 ของอาเรย์ = planner เสมอ, 1..4 = worker
ROLES=(planner integration-engineer qa-engineer docs-engineer reviewer)
MODELS=(opus sonnet sonnet sonnet opus)
TITLES=(planner integration qa docs reviewer)

# pane ที่ planner ควรอยู่: ใน layout tiled 5 ช่อง แถวล่างเป็น pane เดียวเต็มความกว้าง = index 4
# planner ต้องการพื้นที่มากสุดเพราะอ่าน/เขียนแผนยาวและเป็น pane ที่คนจริงคุยด้วย
PLANNER_PANE_INDEX=4

MODE="full"
if [[ "${1:-}" == "--respawn-workers" ]]; then
  MODE="workers"
elif [[ $# -gt 0 ]]; then
  echo "Unknown option: $1 (ใช้ --respawn-workers)" >&2
  exit 1
fi

# ตั้ง @role + ยิงคำสั่ง bootstrap ให้ pane หนึ่ง
# $1 = pane target (pane id หรือ session:window.index), $2 = index ในอาเรย์ ROLES
bootstrap_pane() {
  local pane="$1" i="$2" role model bootstrap
  role="${ROLES[$i]}"
  model="${MODELS[$i]}"
  tmux set-option -p -t "$pane" @role "${TITLES[$i]}"
  bootstrap="อ่าน .claude/agents/${role}.md ทั้งไฟล์ แล้วรับบทบาทเป็น ${role} ของทีมนี้ตั้งแต่นี้เป็นต้นไปตลอด session ทำตาม workflow และข้อจำกัดในไฟล์นั้นทุกประการ"
  tmux send-keys -t "$pane" -l -- "claude --model $model \"$bootstrap\""
  sleep 0.3
  tmux send-keys -t "$pane" C-m
}

# แบ่งหน้าต่างให้ครบ 5 ช่องแบบ tiled (เรียกตอนในหน้าต่างเหลือ pane เดียว)
split_to_five() {
  local i
  for ((i = 1; i < ${#ROLES[@]}; i++)); do
    tmux split-window -t "$SESSION:$WINDOW" -c "$REPO_ROOT"
    tmux select-layout -t "$SESSION:$WINDOW" tiled
  done
}

# โชว์ role บนขอบ pane ให้คนดูด้วยตาได้ว่าใครเป็นใคร
setup_borders() {
  tmux set-option -t "$SESSION:$WINDOW" pane-border-status top
  tmux set-option -t "$SESSION:$WINDOW" pane-border-format ' #{pane_index}: #{@role} '
}

# ⚠️ tmux เรียง pane index ใหม่ตามตำแหน่งหลัง split-window + select-layout
# ห้ามสมมติว่า pane index 0..4 ตรงกับลำดับใน ROLES (เหตุการณ์จริง 2026-08-01: เพี้ยนไป 1 ตำแหน่ง
# ทำให้ dispatch.sh ส่งงานไปผิด role โดยไม่มี error ใด ๆ)
# วิธีแก้: ผูก role กับ tmux user option @role ตอน bootstrap แล้วให้ dispatch.sh
# ค้นหาจาก @role ไม่ใช่ index → index จะเรียงยังไงก็ไม่หลุด
#
# ใช้ @role ไม่ใช่ pane title เพราะ Claude Code เขียนทับ pane title เองตามงานที่กำลังทำ (เห็นจริง 2026-08-01)

if [[ "$MODE" == "workers" ]]; then
  if ! tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Session '$SESSION' ไม่มี — รัน ./orchestrator.sh (ไม่ใส่ flag) แทน" >&2
    exit 1
  fi

  # หา pane ของ planner: ถ้ารันจาก planner เองใช้ $TMUX_PANE, ไม่งั้นหาจาก @role
  PLANNER_ID="${TMUX_PANE:-}"
  if [[ -z "$PLANNER_ID" ]]; then
    PLANNER_ID="$(tmux list-panes -t "$SESSION:$WINDOW" -F '#{pane_id} #{@role}' \
                  | awk '$2 == "planner" { print $1; exit }')"
  fi
  if [[ -z "$PLANNER_ID" ]]; then
    echo "หา pane ของ planner ไม่เจอ (ไม่มี @role=planner และไม่ได้รันจากใน tmux)" >&2
    exit 1
  fi

  echo "planner = $PLANNER_ID (จะไม่ถูกแตะ) — ปิด worker pane ที่เหลือ"
  while read -r id; do
    [[ "$id" == "$PLANNER_ID" ]] && continue
    tmux kill-pane -t "$id"
  done < <(tmux list-panes -t "$SESSION:$WINDOW" -F '#{pane_id}')

  split_to_five
  setup_borders

  # ย้าย planner ไปอยู่ pane ใหญ่สุด ถ้าหลังแบ่งใหม่แล้วมันไม่ได้อยู่ตรงนั้น
  cur_idx="$(tmux display -p -t "$PLANNER_ID" '#{pane_index}')"
  if [[ "$cur_idx" != "$PLANNER_PANE_INDEX" ]]; then
    tmux swap-pane -s "$PLANNER_ID" -t "$SESSION:$WINDOW.$PLANNER_PANE_INDEX"
  fi
  tmux set-option -p -t "$PLANNER_ID" @role "${TITLES[0]}"

  # bootstrap เฉพาะ pane ที่ไม่ใช่ planner เรียงตาม index
  role_i=1
  while read -r _idx id; do
    [[ "$id" == "$PLANNER_ID" ]] && continue
    bootstrap_pane "$id" "$role_i"
    role_i=$((role_i + 1))
  done < <(tmux list-panes -t "$SESSION:$WINDOW" -F '#{pane_index} #{pane_id}' | sort -n)

  echo "เกิด worker ใหม่ครบแล้ว (planner ไม่ถูกแตะ):"
  tmux list-panes -t "$SESSION:$WINDOW" -F '  pane #{pane_index} = #{@role}'
  exit 0
fi

# ---------- MODE=full: เปิด session ใหม่ทั้งชุด ----------

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "Session '$SESSION' มีอยู่แล้ว — attach ด้วย: tmux attach -t $SESSION"
  echo "อยากได้ worker context สะอาดโดยไม่เสีย planner: ./orchestrator.sh --respawn-workers"
  exit 0
fi

tmux new-session -d -s "$SESSION" -n "$WINDOW" -c "$REPO_ROOT"
split_to_five
setup_borders

# planner ต้องได้ pane index 4 (ใหญ่สุด) — แจก role ให้ index อื่นเป็น worker ตามลำดับ
mapfile -t PANE_IDX < <(tmux list-panes -t "$SESSION:$WINDOW" -F '#{pane_index}' | sort -n)

role_i=1
for idx in "${PANE_IDX[@]}"; do
  if [[ "$idx" == "$PLANNER_PANE_INDEX" ]]; then
    bootstrap_pane "$SESSION:$WINDOW.$idx" 0
  else
    bootstrap_pane "$SESSION:$WINDOW.$idx" "$role_i"
    role_i=$((role_i + 1))
  fi
done

echo "เปิดทีมแล้ว: tmux attach -t $SESSION"
tmux list-panes -t "$SESSION:$WINDOW" -F '  pane #{pane_index} = #{@role}'
echo "สั่งงานจาก pane ที่ @role=planner ด้วย: .claude/shared/dispatch.sh <role> \"<เงื่อนไขความสำเร็จ>\""
