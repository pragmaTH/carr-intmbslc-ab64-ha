#!/bin/bash
# dispatch.sh — ส่งคำสั่งจาก pane หนึ่งไปอีก pane (ใช้ในเซสชัน ab64-team)
#
# Usage:
#   .claude/shared/dispatch.sh [--no-goal] <role> "<message>"
#
# role: planner | integration | qa | docs | reviewer
# message: ข้อความที่จะส่งไปเป็น user input ของ claude pane นั้น
#
# พฤติกรรมเริ่มต้น (goal mode):
#   เมื่อ dispatch ไปยัง agent อื่น (integration|qa|docs|reviewer) สคริปต์จะ "ห่อ" ข้อความด้วยคำสั่ง
#   /goal ให้อัตโนมัติ → agent ปลายทางจะเข้าโหมด goal ทำงานต่อเนื่องจนเสร็จ
#   โดยไม่หยุดถาม confirm ทีละ step (ยกเว้น live-write บน hardware จริง และ commit/push — ดู commands/goal.md)
#
#   จึงควรเขียน <message> ให้เป็น "เงื่อนไขความสำเร็จ" ที่ตรวจสอบได้ เช่น
#   "อ่าน tasks/integration-X.md ทำให้เสร็จ และเขียน done/integration-X.md ให้ครบ"
#
# ตัวเลือก:
#   --no-goal   ส่งข้อความธรรมดา ไม่ห่อด้วย /goal (เช่น ส่ง ping/แจ้งสถานะสั้น ๆ)
#   planner     ไม่เคยห่อด้วย /goal (กันตั้ง goal ทับ planner เอง)
#
# env:
#   DRY_RUN=1   พิมพ์คำสั่งที่จะส่งโดยไม่ส่งจริง (ใช้ทดสอบ)
#
# ตัวอย่าง:
#   .claude/shared/dispatch.sh integration "อ่าน .claude/shared/tasks/integration-x.md ทำให้เสร็จ เขียน done/integration-x.md"
#   .claude/shared/dispatch.sh reviewer    "review topic x: อ่าน done/*x* + plan-x.md เขียน review/x-review.md ให้เสร็จ"
#   .claude/shared/dispatch.sh --no-goal docs "ping: ตอนนี้ว่างไหม?"

set -euo pipefail

SESSION="ab64-team"
WINDOW="team"

USE_GOAL=1

# parse option flags (ต้องมาก่อน role)
while [[ $# -gt 0 && "${1:-}" == --* ]]; do
  case "$1" in
    --no-goal) USE_GOAL=0; shift ;;
    --goal)    USE_GOAL=1; shift ;;
    --) shift; break ;;
    *) echo "Unknown option: $1 (ใช้ --no-goal | --goal)" >&2; exit 1 ;;
  esac
done

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 [--no-goal] <planner|integration|qa|docs|reviewer> \"<message>\"" >&2
  exit 1
fi

ROLE="$1"
shift
MSG="$*"

case "$ROLE" in
  planner)     USE_GOAL=0 ;;   # ไม่ห่อ /goal ให้ตัว planner เอง
  integration|qa|docs|reviewer) ;;
  *) echo "Unknown role: $ROLE (ใช้ planner|integration|qa|docs|reviewer)" >&2; exit 1 ;;
esac

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "Session '$SESSION' ไม่มี — รัน ./orchestrator.sh ก่อน" >&2
  exit 1
fi

# หา pane จาก tmux user option "@role" ของ pane นั้น ไม่ใช่จาก pane index
#
# ทำไมไม่ใช้ index: tmux เรียง pane index ใหม่ตามตำแหน่งหลัง split-window + select-layout
# index จึงไม่การันตีว่าตรงกับลำดับที่ orchestrator.sh ตั้งใจ
# (เหตุการณ์จริง 2026-08-01: index เพี้ยนไป 1 ตำแหน่ง → dispatch ไปผิด role โดยไม่มี error ใด ๆ)
#
# ทำไมไม่ใช้ pane_title: Claude Code เขียนทับ pane title เองตามงานที่กำลังทำ
# (เห็นจริง 2026-08-01: title กลายเป็น "✳ เป็นวิศวกรรวมระบบ...") ใช้เป็น key ไม่ได้
#
# @role เป็น user option ของ tmux เอง ไม่มีใครไปยุ่ง → ตั้งครั้งเดียวใน orchestrator.sh
# หาไม่เจอ = ล้มเหลวเสียงดัง ดีกว่าส่งไปผิด pane เงียบ ๆ
PANE="$(tmux list-panes -t "$SESSION:$WINDOW" -F '#{pane_index} #{@role}' \
        | awk -v r="$ROLE" '$2 == r { print $1; exit }')"

if [[ -z "$PANE" ]]; then
  echo "หา pane ของ role '$ROLE' ไม่เจอ (@role ยังไม่ถูกตั้ง?)" >&2
  echo "ตั้งให้ครบก่อน เช่น: tmux set-option -p -t $SESSION:$WINDOW.<index> @role <role>" >&2
  echo "ตอนนี้มี:" >&2
  tmux list-panes -t "$SESSION:$WINDOW" -F '  index=#{pane_index} @role=#{@role} title=#{pane_title}' >&2
  exit 1
fi

# ห่อข้อความด้วย /goal เมื่ออยู่ใน goal mode → agent ปลายทางทำงานต่อเนื่องไม่หยุดถาม confirm
if [[ "$USE_GOAL" -eq 1 ]]; then
  SEND="/goal $MSG"
else
  SEND="$MSG"
fi

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  echo "[DRY_RUN] target=$SESSION:$WINDOW.$PANE ($ROLE) goal_mode=$USE_GOAL"
  echo "[DRY_RUN] would send: $SEND"
  exit 0
fi

TARGET="$SESSION:$WINDOW.$PANE"

# พิมพ์ข้อความเป็น literal (ปลอดภัยกับอักขระพิเศษ)
# กรณี /goal: ช่องว่างหลัง "/goal" ปิด slash-command palette ทำให้ args ตามหลังถูกส่งครบ
tmux send-keys -t "$TARGET" -l -- "$SEND"
# หน่วงก่อน Enter — กับข้อความยาว/bracketed-paste ถ้ายิง Enter เร็วเกิน
# มันจะหลุดเข้าไปใน paste buffer แล้วไม่ submit (อาการ "ลืมกด enter")
sleep 0.6
tmux send-keys -t "$TARGET" C-m
# กันพลาด: ถ้า Enter แรกไปตกใน paste ที่ยังไม่ปิด ส่งซ้ำอีกครั้งหลังหน่วงสั้น ๆ
sleep 0.3
tmux send-keys -t "$TARGET" C-m

if [[ "$USE_GOAL" -eq 1 ]]; then
  echo "→ ส่งให้ $ROLE (pane $PANE) แบบ /goal แล้ว: $MSG"
else
  echo "→ ส่งให้ $ROLE (pane $PANE) แล้ว: $MSG"
fi
