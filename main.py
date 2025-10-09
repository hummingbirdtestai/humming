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
            # Step 1️⃣ Get current pointer
            pointer_res = supabase.rpc("get_pointer_status", {
                "p_student_id": user_id,
                "p_chapter_id": chapter_id
            }).execute()
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

            # ──────────────────────────────────────────────
            # 🧩 Step 2.5 → Phase recognition + local tracker logic
            # ──────────────────────────────────────────────
            phase_type = phase.get("phase_type")
            phase_json = phase.get("phase_content") or {}
            
            logging.info(f"🧩 Recognized phase_type={phase_type}")
            
            # Compute totals for in-phase arrays
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
                "p_meta": json.dumps(phase_json),   # 🆕 cache full HYFs JSON
                "p_is_completed": False
            }).execute()
            logging.info(f"📍 Local tracker initialized for conversation → total_hyfs={total_hyfs} | meta cached ✅")
            logging.debug(f"🧩 Stored meta preview → {json.dumps(phase_json)[:200]}...")
            
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
                "p_meta": json.dumps(phase_json),   # 🆕 cache full MCQ JSON
                "p_is_completed": False
            }).execute()
            logging.info(f"📍 Local tracker initialized for mcq → total_mcqs={total_mcqs} | meta cached ✅")
            logging.debug(f"🧩 Stored meta preview → {json.dumps(phase_json)[:200]}...")

            # Step 3️⃣ Update pointer
            react_order = phase.get("react_order")
            supabase.rpc("update_pointer_status", {
                "p_student_id": user_id,
                "p_chapter_id": chapter_id,
                "p_react_order": react_order
            }).execute()
            logging.info(f"🕒 update_pointer_status → {react_order}")

            # Step 4️⃣ Prepare frontend-ready payload
            phase_type = phase.get("phase_type", "concept")
            phase_json = phase.get("phase_content") or {}
            
            # Base data
            data_block = { **phase_json, "phase_id": phase.get("phase_id") }
            
            # 🧠 Add current/total only for concept phase
            if phase_type == "concept":
                data_block["current"] = phase.get("current")
                data_block["total"] = phase.get("total")
            
            payload = {
                "type": phase_type,
                "data": data_block,
                "messages": [
                    {
                        "sender": "ai",
                        "type": "text",
                        "content": f"Starting {phase_type}"
                    }
                ],
            }

            return payload

        except Exception as e:
            logging.error(f"❌ Error in start/resume flow: {e}")
            return {"error": str(e)}

        # ──────────────────────────────────────────────
        # 🟣 NEXT PHASE FLOW — Tracker vs Pointer Branch
        # ──────────────────────────────────────────────
        elif intent == "next":
            try:
                # 1️⃣ Get current pointer and phase
                pointer_res = supabase.rpc("get_pointer_status", {
                    "p_student_id": user_id,
                    "p_chapter_id": chapter_id
                }).execute()
    
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
    
                # 2️⃣ Phase branching logic
                if phase_type in ("concept", "media", "flashcard"):
                    # Simple phase — complete macro pointer directly
                    supabase.rpc("complete_pointer_status", {
                        "p_student_id": user_id,
                        "p_chapter_id": chapter_id,
                        "p_react_order": react_order
                    }).execute()
                    logging.info(f"✅ Macro pointer completed for {phase_type}")
    
                elif phase_type in ("conversation", "mcq"):
                    # Tracker-based phase — advance local GPS pin
                    supabase.rpc("update_local_tracker_status", {
                        "p_student_id": user_id,
                        "p_phase_id": phase_id,
                        "p_chapter_id": chapter_id,
                        "p_phase_type": phase_type,
                        "p_is_completed": False
                    }).execute()
                    logging.info(f"📍 Local tracker updated for {phase_type}")
    
                    # Check if tracker completed → then mark macro complete
                    tracker_check = supabase.rpc("get_local_tracker_status", {
                        "p_student_id": user_id,
                        "p_phase_id": phase_id
                    }).execute()
    
                    tracker = tracker_check.data[0] if tracker_check.data else None
                    if tracker and tracker.get("is_completed"):
                        logging.info(f"🏁 Local tracker completed → promoting to macro pointer")
                        supabase.rpc("complete_pointer_status", {
                            "p_student_id": user_id,
                            "p_chapter_id": chapter_id,
                            "p_react_order": react_order
                        }).execute()
    
                # 3️⃣ Now fetch next phase
                next_phase = supabase.rpc("get_phase_content", {
                    "p_chapter_id": chapter_id,
                    "p_react_order": react_order,
                    "p_is_completed": True
                }).execute()
    
                if not next_phase.data:
                    return {"message": "🎉 Chapter completed!"}
    
                next = next_phase.data[0]
                next_type = next.get("phase_type", "concept")
    
                # 4️⃣ Prepare payload for next phase
                data_block = { **(next.get("phase_content") or {}), "phase_id": next.get("phase_id") }
                if next_type == "concept":
                    data_block["current"] = next.get("current")
                    data_block["total"] = next.get("total")
    
                payload = {
                    "type": next_type,
                    "data": data_block,
                    "messages": [
                        {"sender": "ai", "type": "text", "content": f"Next {next_type}"}
                    ],
                }
    
                return payload
    
            except Exception as e:
                logging.error(f"❌ Error in next flow: {e}")
                return {"error": str(e)}

            if not phase_res.data:
                return {"error": "No next phase found"}

            phase = phase_res.data[0]

            # 3️⃣ Update pointer to next
            next_react_order = phase.get("react_order")
            supabase.rpc("update_pointer_status", {
                "p_student_id": user_id,
                "p_chapter_id": chapter_id,
                "p_react_order": next_react_order
            }).execute()
            logging.info(f"🕒 update_pointer_status(next) → {next_react_order}")

            # 4️⃣ Return in AdaptiveChat structure
            payload = {
                "type": phase.get("phase_type", "concept"),
                "data": {
                    **(phase.get("phase_content") or {}),
                    "phase_id": phase.get("phase_id"),
                    "current": phase.get("current"),
                    "total": phase.get("total")
                },
                "messages": [
                    {
                        "sender": "ai",
                        "type": "text",
                        "content": f"Next {phase.get('phase_type', 'concept')} "
                                   f"({phase.get('current')}/{phase.get('total')})"
                    }
                ],
            }

            return payload

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

