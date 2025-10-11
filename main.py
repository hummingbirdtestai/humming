# main.py
# ──────────────────────────────────────────────
# 🐦 Hummingbird FastAPI — clean final version (with phase_type normalization)
# ──────────────────────────────────────────────
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client
from openai import OpenAI
from dotenv import load_dotenv
import os
import logging
import json

# ──────────────────────────────────────────────
# ⚙️ ENV + LOGGING
# ──────────────────────────────────────────────
load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

if not all([SUPABASE_URL, SUPABASE_KEY, OPENAI_KEY]):
    logging.warning("⚠️ Missing environment variables!")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
openai_client = OpenAI(api_key=OPENAI_KEY)

# ──────────────────────────────────────────────
# 🧩 FASTAPI SETUP
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
# 🪶 Utility: Safe RPC wrapper
# ──────────────────────────────────────────────
def safe_rpc(name: str, payload: dict):
    """Executes a Supabase RPC safely and logs result."""
    try:
        res = supabase.rpc(name, payload).execute()
        if hasattr(res, "data") and res.data is not None:
            logging.info(f"✅ RPC {name} executed successfully")
            return res
        logging.warning(f"⚠️ RPC {name} returned no data")
        return None
    except Exception as e:
        logging.error(f"❌ RPC {name} failed: {e}")
        return None

# ──────────────────────────────────────────────
# 🏠 Root — Health Check
# ──────────────────────────────────────────────
@app.get("/")
def root():
    return {"status": "Hummingbird FastAPI running 🐦", "ok": True}

# ──────────────────────────────────────────────
# 🧠 Mentor API Router
# ──────────────────────────────────────────────
@app.post("/mentor_api")
async def mentor_router(req: Request):
    """Main endpoint handling AdaptiveChat intents."""
    try:
        data = await req.json()
    except Exception as e:
        logging.error(f"❌ Invalid JSON: {e}")
        return {"error": "Invalid JSON payload"}

    intent = data.get("intent")
    user_id = data.get("user_id")
    chapter_id = data.get("chapter_id")
    is_correct = data.get("is_correct")  # only relevant for MCQ completion

    logging.info(f"📩 Intent={intent} | User={user_id} | Chapter={chapter_id}")

    # ──────────────────────────────────────────────
    # 🟢 START / RESUME FLOW
    # ──────────────────────────────────────────────
    if intent in ("start", "resume"):
        try:
            # 1️⃣ Get pointer status
            pointer = safe_rpc("get_pointer_status", {
                "p_student_id": user_id,
                "p_chapter_id": chapter_id
            })
            react_order = pointer.data[0]["react_order"] if pointer and pointer.data else None
            is_completed = pointer.data[0]["is_completed"] if pointer and pointer.data else None

            # 2️⃣ Get current or first phase
            phase_res = safe_rpc("get_phase_content", {
                "p_chapter_id": chapter_id,
                "p_react_order": react_order,
                "p_is_completed": is_completed,
                "p_is_correct": None
            })
            if not phase_res or not phase_res.data:
                return {"error": "No phase content found"}

            phase = phase_res.data[0]
            phase_type = phase.get("phase_type")

            # 🔽 Normalize case and naming to match AdaptiveChat
            if phase_type:
                phase_type = phase_type.lower()
                if phase_type == "flashcards":
                    phase_type = "flashcard"

            next_react = phase.get("react_order")

            # 3️⃣ Start pointer for this react_order
            safe_rpc("update_pointer_status", {
                "p_student_id": user_id,
                "p_chapter_id": chapter_id,
                "p_react_order": next_react
            })

            logging.info(f"🕒 Pointer updated to start react_order={next_react}")
            logging.info(f"🧩 Normalized phase_type → {phase_type}")

            return {
                "type": phase_type,
                "data": phase.get("phase_content"),
                "react_order": next_react,
                "messages": [
                    {"sender": "ai", "type": "text", "content": f"Starting {phase_type}"}
                ]
            }

        except Exception as e:
            logging.error(f"❌ Error in start/resume flow: {e}")
            return {"error": str(e)}

    # ──────────────────────────────────────────────
    # 🟣 NEXT FLOW
    # ──────────────────────────────────────────────
    elif intent == "next":
        try:
            # 1️⃣ Get current pointer (to identify react_order)
            pointer = safe_rpc("get_pointer_status", {
                "p_student_id": user_id,
                "p_chapter_id": chapter_id
            })
            if not pointer or not pointer.data:
                return {"error": "No active pointer found"}

            current_react = pointer.data[0]["react_order"]

            # 2️⃣ Mark current pointer complete
            safe_rpc("complete_pointer_status", {
                "p_student_id": user_id,
                "p_chapter_id": chapter_id,
                "p_react_order": current_react,
                "p_is_correct": is_correct  # ✅ pass correctness
            })
            logging.info(f"✅ Completed pointer react_order={current_react}, is_correct={is_correct}")

            # 3️⃣ Get next phase content
            next_phase = safe_rpc("get_phase_content", {
                "p_chapter_id": chapter_id,
                "p_react_order": current_react,
                "p_is_completed": True,
                "p_is_correct": is_correct
            })
            if not next_phase or not next_phase.data or next_phase.data[0]["react_order"] is None:
                logging.info("🎉 Chapter complete — no further content")
                return {"message": "🎉 Chapter completed!"}

            phase = next_phase.data[0]
            next_react = phase.get("react_order")
            phase_type = phase.get("phase_type")

            # 🔽 Normalize case and naming to match AdaptiveChat
            if phase_type:
                phase_type = phase_type.lower()
                if phase_type == "flashcards":
                    phase_type = "flashcard"

            # 4️⃣ Start new pointer for next react_order
            safe_rpc("update_pointer_status", {
                "p_student_id": user_id,
                "p_chapter_id": chapter_id,
                "p_react_order": next_react
            })
            logging.info(f"🕒 New pointer started for react_order={next_react}")
            logging.info(f"🧩 Normalized phase_type → {phase_type}")

            return {
                "type": phase_type,
                "data": phase.get("phase_content"),
                "react_order": next_react,
                "messages": [
                    {"sender": "ai", "type": "text", "content": f"Next {phase_type}"}
                ]
            }

        except Exception as e:
            logging.error(f"❌ Error in next flow: {e}")
            return {"error": str(e)}

    # ──────────────────────────────────────────────
    # 💬 CHAT / ASK DOUBT
    # ──────────────────────────────────────────────
    elif intent in ("chat", "ask_doubt"):
        q = data.get("question", "")
        if not q:
            return {"error": "No question provided"}
        try:
            ans = openai_client.chat.completions.create(
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
            logging.error(f"❌ GPT/Supabase error: {e}")
            return {"error": str(e)}

    # ──────────────────────────────────────────────
    # ⚠️ Unknown intent
    # ──────────────────────────────────────────────
    else:
        return {"error": f"Unknown intent: {intent}"}
