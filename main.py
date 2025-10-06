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
            # Step 1: get_pointer_status
            pointer_res = supabase.rpc("get_pointer_status", {
                "p_student_id": user_id,
                "p_chapter_id": chapter_id
            }).execute()
            logging.info(f"🧭 get_pointer_status → {pointer_res.data}")

            react_order = pointer_res.data[0]["react_order"] if pointer_res.data else None
            is_completed = pointer_res.data[0]["is_completed"] if pointer_res.data else None

            # Step 2: get_phase_content
            phase_res = supabase.rpc("get_phase_content", {
                "p_chapter_id": chapter_id,
                "p_react_order": react_order,
                "p_is_completed": is_completed
            }).execute()
            logging.info(f"📚 get_phase_content → {len(phase_res.data)} rows")

            # Step 3: update_pointer_status
            if phase_res.data:
                react_order = phase_res.data[0]["react_order"]
                supabase.rpc("update_pointer_status", {
                    "p_student_id": user_id,
                    "p_chapter_id": chapter_id,
                    "p_react_order": react_order
                }).execute()
                logging.info(f"🕒 update_pointer_status → {react_order}")

            return {"phase_data": phase_res.data, "react_order": react_order}

        except Exception as e:
            logging.error(f"❌ Error in start/resume flow: {e}")
            return {"error": str(e)}

    # ──────────────────────────────────────────────
    # 🟣 NEXT PHASE FLOW
    # ──────────────────────────────────────────────
    elif intent == "next":
        try:
            # 1️⃣ Mark current phase complete
            pointer_res = supabase.rpc("get_pointer_status", {
                "p_student_id": user_id,
                "p_chapter_id": chapter_id
            }).execute()

            if pointer_res.data:
                react_order = pointer_res.data[0]["react_order"]
                supabase.rpc("complete_pointer_status", {
                    "p_student_id": user_id,
                    "p_chapter_id": chapter_id,
                    "p_react_order": react_order
                }).execute()
                logging.info(f"✅ complete_pointer_status → {react_order}")

            # 2️⃣ Fetch next phase (same as start)
            phase_res = supabase.rpc("get_phase_content", {
                "p_chapter_id": chapter_id,
                "p_react_order": react_order,
                "p_is_completed": True
            }).execute()
            logging.info(f"➡️ get_phase_content(next) → {len(phase_res.data)} rows")

            if phase_res.data:
                next_react_order = phase_res.data[0]["react_order"]
                supabase.rpc("update_pointer_status", {
                    "p_student_id": user_id,
                    "p_chapter_id": chapter_id,
                    "p_react_order": next_react_order
                }).execute()
                logging.info(f"🕒 update_pointer_status(next) → {next_react_order}")

            return {"next_phase": phase_res.data}

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
            logging.info(f"💬 GPT query: {q}")
            ans = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": q}]
            )
            reply = ans.choices[0].message.content
            logging.info(f"🤖 GPT reply: {reply[:120]}...")

            # Optional: store in Supabase (student_doubts)
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
