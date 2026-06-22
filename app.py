#!/usr/bin/env python3
"""血管外科備考互動網頁系統 — 雲端版"""
from flask import Flask, jsonify, request, render_template_string
import json, random, datetime, re, sqlite3, os
from pathlib import Path
from collections import defaultdict

app = Flask(__name__)

# ── Data paths ──────────────────────────────────────────────
BASE = Path(__file__).parent
QBANK_PATH = BASE / "vascular-question-bank.json"
DB_PATH = Path(os.environ.get("DATA_DIR", "/tmp")) / "vascular_progress.db"
EXAM_DATE = datetime.date(2026, 8, 23)

# ── Question bank (loaded once) ──────────────────────────────
with open(QBANK_PATH, encoding="utf-8") as f:
    RAW_QUESTIONS = json.load(f)

TOPIC_LABELS = {
    "Aorta": "主動脈", "Basic": "基礎科學", "Peripheral": "週邊血管",
    "Venous": "靜脈疾病", "Access": "透析通路", "Carotid": "頸動脈", "Visceral": "內臟血管",
}
TOPIC_ICONS = {
    "Aorta": "🫀", "Basic": "🔬", "Peripheral": "🦵",
    "Venous": "🩸", "Access": "💉", "Carotid": "🧠", "Visceral": "🫁",
}

def normalize_topic(raw):
    raw = str(raw)
    for key in TOPIC_LABELS:
        if key.lower() in raw.lower(): return key
    if any(k in raw.lower() for k in ["access","hemodialysis","透析"]): return "Access"
    if any(k in raw.lower() for k in ["aorta","主動脈"]): return "Aorta"
    return "Basic"

QUESTIONS = []
for q in RAW_QUESTIONS:
    qq = dict(q)
    qq["topic_norm"] = normalize_topic(q.get("topic", "Basic"))
    qq["topic_label"] = TOPIC_LABELS.get(qq["topic_norm"], qq["topic_norm"])
    QUESTIONS.append(qq)

# ── SQLite progress DB ────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS progress (
                qid INTEGER PRIMARY KEY,
                attempts INTEGER DEFAULT 0,
                correct INTEGER DEFAULT 0,
                last_answer TEXT,
                last_date TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                qid INTEGER,
                answer TEXT,
                correct INTEGER,
                date TEXT
            )
        """)
        conn.commit()

init_db()

def normalize_answer(ans):
    ans = str(ans).strip()
    for fw, hw in zip("ＡＢＣＤＥ", "ABCDE"):
        ans = ans.replace(fw, hw)
    m = re.search(r'[A-E]', ans)
    return m.group(0) if m else ""

# ── HTML (inlined from index.html) ───────────────────────────
HTML = open(BASE / "index.html", encoding="utf-8").read()

# ── Routes ────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/api/topics")
def get_topics():
    with get_db() as conn:
        prog = {row["qid"]: row for row in conn.execute("SELECT * FROM progress")}
    topics = defaultdict(lambda: {"total":0,"practiced":0,"correct":0})
    for q in QUESTIONS:
        t = q["topic_norm"]
        topics[t]["total"] += 1
        p = prog.get(q["id"])
        if p and p["attempts"] > 0:
            topics[t]["practiced"] += 1
            if p["correct"]: topics[t]["correct"] += 1
    result = []
    for key, d in sorted(topics.items()):
        result.append({
            "key": key, "label": TOPIC_LABELS.get(key, key),
            "icon": TOPIC_ICONS.get(key, "📚"),
            **d,
            "accuracy": round(d["correct"]/d["practiced"]*100) if d["practiced"] else 0
        })
    return jsonify(result)

@app.route("/api/questions")
def get_questions():
    topic = request.args.get("topic", "all")
    n = int(request.args.get("n", 5))
    mode = request.args.get("mode", "random")

    with get_db() as conn:
        practiced_ids = {row["qid"] for row in conn.execute("SELECT qid FROM progress WHERE attempts>0")}
        wrong_ids = {row["qid"] for row in conn.execute("SELECT qid FROM progress WHERE attempts>0 AND correct=0")}

    pool = QUESTIONS if topic == "all" else [q for q in QUESTIONS if q["topic_norm"] == topic]
    if mode == "new": pool = [q for q in pool if q["id"] not in practiced_ids] or pool
    elif mode == "wrong": pool = [q for q in pool if q["id"] in wrong_ids] or pool

    selected = random.sample(pool, min(n, len(pool)))
    return jsonify([{
        "id": q["id"], "year": q.get("year",""), "topic": q["topic_norm"],
        "topic_label": q["topic_label"], "question": q["question"],
        "options": q.get("options",""), "ref": q.get("ref",""),
    } for q in selected])

@app.route("/api/answer", methods=["POST"])
def submit_answer():
    data = request.json
    qid = data.get("id")
    user_ans = str(data.get("answer","")).upper()
    q = next((x for x in QUESTIONS if x["id"] == qid), None)
    if not q: return jsonify({"error":"not found"}), 404

    correct_ans = normalize_answer(q.get("answer",""))
    is_correct = (user_ans == correct_ans)
    today = str(datetime.date.today())

    with get_db() as conn:
        existing = conn.execute("SELECT * FROM progress WHERE qid=?", (qid,)).fetchone()
        if existing:
            conn.execute("UPDATE progress SET attempts=attempts+1, correct=?, last_answer=?, last_date=? WHERE qid=?",
                        (int(is_correct), user_ans, today, qid))
        else:
            conn.execute("INSERT INTO progress (qid, attempts, correct, last_answer, last_date) VALUES (?,1,?,?,?)",
                        (qid, int(is_correct), user_ans, today))
        conn.execute("INSERT INTO history (qid, answer, correct, date) VALUES (?,?,?,?)",
                    (qid, user_ans, int(is_correct), today))
        conn.commit()

    return jsonify({
        "correct": is_correct, "correct_answer": correct_ans,
        "user_answer": user_ans, "ref": q.get("ref",""), "year": q.get("year",""),
    })

@app.route("/api/stats")
def get_stats():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM progress WHERE attempts>0").fetchall()
        daily_rows = conn.execute("SELECT date, correct, 1 as cnt FROM history ORDER BY date").fetchall()

    total_practiced = len(rows)
    total_correct = sum(r["correct"] for r in rows)
    accuracy = round(total_correct/total_practiced*100) if total_practiced else 0

    daily = defaultdict(lambda: {"total":0,"correct":0})
    for r in daily_rows:
        daily[r["date"]]["total"] += 1
        if r["correct"]: daily[r["date"]]["correct"] += 1

    return jsonify({
        "total_questions": len(QUESTIONS),
        "total_practiced": total_practiced,
        "total_correct": total_correct,
        "accuracy": accuracy,
        "completion": round(total_practiced/len(QUESTIONS)*100) if QUESTIONS else 0,
        "daily": dict(sorted(daily.items())[-14:]),
        "days_left": (EXAM_DATE - datetime.date.today()).days,
    })

@app.route("/api/reset_progress", methods=["POST"])
def reset_progress():
    with get_db() as conn:
        conn.execute("DELETE FROM progress")
        conn.execute("DELETE FROM history")
        conn.commit()
    return jsonify({"ok": True})

@app.route("/health")
def health():
    return "ok"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5002))
    app.run(host="0.0.0.0", port=port, debug=False)
