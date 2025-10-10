from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client
from openai import OpenAI
from dotenv import load_dotenv
import os
import logging
import json

# ──────────────────────────────────────────────
# 🔧 ENV + LOGGING SETUP
# ──────────────────────────────────────────────
load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# ──────────────────────────────────────────────
# ⚙️ FASTAPI APP + CORS
# ──────────────────────────────────────────────
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ──────────────────────────────────────────────
# 🌐 CLIENT INITIALIZATION
# ──────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

if not all([SUPABASE_URL, SUPABASE_KEY, OPENAI_KEY]):
    logging.warning("⚠️ One or more environment variables missing!")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
client = OpenAI(api_key=OPENAI_KEY)

# ──────────────────────────────────────────────
# 🪶 FAULT-TOLERANT SUPABASE WRAPPER
# ──────────────────────────────────────────────
def safe_rpc(name: str, payload: dict):
    """Execute Supabase RPC safely with logging and None fallback."""
    try:
        res = supabase.rpc(name, payload).execute()
        if res.error:
            logging.error(f"❌ RPC {name} failed: {res.error}")
            return None
        return res
    except Exception as e:
        logging.error(f"⚠️ RPC {name} threw exception: {e}")
        return None

# ──────────────────────────────────────────────
# 🏠 ROOT ENDPOINT
# ──────────────────────────────────────────────
@app.get("/")
def root():
    logging.info("🩵 Root route called — health check OK.")
    return {"status": "Hummingbird FastAPI running 🐦", "ok": True}

# ──────────────────────────────────────────────
# 🧠 MAIN ROUTER
# ──────────────────────────────────────────────
@app.post("/mentor_api")
async def mentor_router(req: Request):
    """Main endpoint that receives AdaptiveChat intents from frontend."""
    try:
        data = await req.json()
    except Exception as e:
        logging.error(f"❌ Failed to parse JSON: {e}")
        return {"error": "Invalid JSON payload"}

    intent = data.get("intent")
    user_id = data.get("user_id")
    chapter_id = data.get("chapter_id")

    logging.info(f"📩 Incoming intent={intent} | user={user_id} | chapter={chapter_id}")
    logging.debug(f"🧾 Full payload → {json.dumps(data, indent=2)}")

    # ──────────────────────────────────────────────
    # 🟢 START / RESUME FLOW
    # ──────────────────────────────────────────────
    if intent in ("start", "resume", "get_phase"):
        try:
            # Step 1️⃣ Get current pointer
            pointer_res = safe_rpc("get_pointer_status", {
                "p_student_id": user_id,
                "p_chapter_id": chapter_id
            })
            logging.info(f"🧭 get_pointer_status → {pointer_res.data}")

            react_order = pointer_res.data[0]["react_order"] if pointer_res.data else None
            is_completed = pointer_res.data[0]["is_completed"] if pointer_res.data else None

            # Step 2️⃣ Get current phase content
            phase_res = supabase.rpc("get_phase_content", {
                "p_chapter_id": chapter_id,
                "p_react_order": react_order,
                "p_is_completed": is_completed
            }).execute()
            logging.info(f"📚 get_phase_content → {len(phase_res.data)} rows")

            if not phase_res.data:
                return {"error": "No phase content found"}

            phase = phase_res.data[0]

            # Step 3️⃣ Use cached tracker meta if available
            phase_type = phase.get("phase_type")
            if phase_type in ("conversation", "mcq"):
                tracker_res = supabase.rpc("get_local_tracker_status", {
                    "p_student_id": user_id,
                    "p_phase_id": phase.get("phase_id")
                }).execute()
                if tracker_res.data:
                    tracker_row = tracker_res.data[0]
                    cached_meta = tracker_row.get("meta")
                    if cached_meta and cached_meta != {}:
                        logging.info(f"⚡ Using cached meta for {phase_type} phase_id={phase.get('phase_id')}")
                        phase["phase_content"] = cached_meta
                    else:
                        logging.info(f"ℹ️ No cached meta found for {phase_type}, using DB content.")

            # Step 4️⃣ Phase recognition + local tracker logic
            phase_json = phase.get("phase_content") or {}
            logging.info(f"🧩 Recognized phase_type={phase_type}")

            if phase_type == "conversation":
                total_hyfs = len(phase_json.get("HYFs", []))
                supabase.rpc("update_local_tracker_status", {
                    "p_student_id": user_id,
                    "p_phase_id": phase.get("phase_id"),
                    "p_chapter_id": chapter_id,
                    "p_phase_type": phase_type,
                    "p_tracker_type": "conversation",
                    "p_current_hyf_index": 0,
                    "p_total_hyfs": total_hyfs,
                    "p_meta": json.dumps(phase_json),
                    "p_is_completed": False
                }).execute()
                logging.info(f"📍 Local tracker initialized for conversation → total_hyfs={total_hyfs}")

            elif phase_type == "mcq":
                total_mcqs = len(phase_json if isinstance(phase_json, list) else [])
                supabase.rpc("update_local_tracker_status", {
                    "p_student_id": user_id,
                    "p_phase_id": phase.get("phase_id"),
                    "p_chapter_id": chapter_id,
                    "p_phase_type": phase_type,
                    "p_tracker_type": "concept_mcq",
                    "p_current_mcq_index": 0,
                    "p_total_mcqs": total_mcqs,
                    "p_meta": json.dumps(phase_json),
                    "p_is_completed": False
                }).execute()
                logging.info(f"📍 Local tracker initialized for mcq → total_mcqs={total_mcqs}")

            # Step 5️⃣ Update pointer
            react_order = phase.get("react_order")
            supabase.rpc("update_pointer_status", {
                "p_student_id": user_id,
                "p_chapter_id": chapter_id,
                "p_react_order": react_order
            }).execute()
            logging.info(f"🕒 update_pointer_status → {react_order}")

            # Step 6️⃣ Prepare frontend payload
            phase_json = phase.get("phase_content") or {}
            data_block = {**phase_json, "phase_id": phase.get("phase_id")}
            if phase_type == "concept":
                data_block["current"] = phase.get("current")
                data_block["total"] = phase.get("total")

            return {
                "type": phase_type,
                "data": data_block,
                "messages": [
                    {"sender": "ai", "type": "text", "content": f"Starting {phase_type}"}
                ],
            }

        except Exception as e:
            logging.error(f"❌ Error in start/resume flow: {e}")
            return {"error": str(e)}

    # ──────────────────────────────────────────────
    # 🟣 NEXT PHASE FLOW
    # ──────────────────────────────────────────────
    elif intent == "next":
        try:
            pointer_res = safe_rpc("get_pointer_status", {
                "p_student_id": user_id,
                "p_chapter_id": chapter_id
            })
            if not pointer_res.data:
                return {"error": "No pointer found"}

            react_order = pointer_res.data[0]["react_order"]

            phase_res = supabase.rpc("get_phase_content", {
                "p_chapter_id": chapter_id,
                "p_react_order": react_order,
                "p_is_completed": False
            }).execute()
            if not phase_res.data:
                return {"error": "No current phase found"}

            phase = phase_res.data[0]
            phase_type = phase.get("phase_type")
            phase_id = phase.get("phase_id")

            # Branching logic
            if phase_type in ("concept", "media", "flashcard"):
                supabase.rpc("complete_pointer_status", {
                    "p_student_id": user_id,
                    "p_chapter_id": chapter_id,
                    "p_react_order": react_order
                }).execute()
                logging.info(f"✅ Macro pointer completed for {phase_type}")

            elif phase_type in ("conversation", "mcq"):
                supabase.rpc("update_local_tracker_status", {
                    "p_student_id": user_id,
                    "p_phase_id": phase_id,
                    "p_chapter_id": chapter_id,
                    "p_phase_type": phase_type,
                    "p_is_completed": False
                }).execute()
                tracker_check = supabase.rpc("get_local_tracker_status", {
                    "p_student_id": user_id,
                    "p_phase_id": phase_id
                }).execute()
                tracker = tracker_check.data[0] if tracker_check.data else None
                if tracker and tracker.get("is_completed"):
                    supabase.rpc("complete_pointer_status", {
                        "p_student_id": user_id,
                        "p_chapter_id": chapter_id,
                        "p_react_order": react_order
                    }).execute()
                    logging.info(f"🏁 Local tracker promoted to macro pointer")

            # Fetch next phase
            next_phase = supabase.rpc("get_phase_content", {
                "p_chapter_id": chapter_id,
                "p_react_order": react_order,
                "p_is_completed": True
            }).execute()
            if not next_phase.data:
                return {"message": "🎉 Chapter completed!"}

            next = next_phase.data[0]
            next_type = next.get("phase_type", "concept")

            data_block = {**(next.get("phase_content") or {}), "phase_id": next.get("phase_id")}
            if next_type == "concept":
                data_block["current"] = next.get("current")
                data_block["total"] = next.get("total")

            return {
                "type": next_type,
                "data": data_block,
                "messages": [
                    {"sender": "ai", "type": "text", "content": f"Next {next_type}"}
                ],
            }

        except Exception as e:
            logging.error(f"❌ Error in next flow: {e}")
            return {"error": str(e)}

    # ──────────────────────────────────────────────
    # 💬 CHAT (ASK DOUBT)
    # ──────────────────────────────────────────────
    elif intent in ("chat", "ask_doubt"):
        q = data.get("question", "")
        if not q:
            return {"error": "No question provided"}
        try:
            ans = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": q}]
            )
            reply = ans.choices[0].message.content
            supabase.table("student_doubts").insert({
                "user_id": user_id,
                "chapter_id": chapter_id,
                "question": q,
                "answer": reply
            }).execute()
            return {"reply": reply}
        except Exception as e:
            logging.error(f"❌ GPT or Supabase error: {e}")
            return {"error": str(e)}

    # ──────────────────────────────────────────────
    # 🔴 UNKNOWN INTENT
    # ──────────────────────────────────────────────
    else:
        logging.warning(f"⚠️ Unknown intent received: {intent}")
        return {"error": "Unknown intent"}
