# main.py
# ──────────────────────────────────────────────
# 🐦 Hummingbird FastAPI — Final Production Version
# (Pointer + MCQ UPSERT + Mentor Conversation Block)
# ──────────────────────────────────────────────
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client
from openai import OpenAI
from dotenv import load_dotenv
import os
import logging
import json
import datetime
import uuid

# ──────────────────────────────────────────────
# ⚙️ ENV + LOGGING
# ──────────────────────────────────────────────
load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

if not all([SUPABASE_URL, SUPABASE_KEY, OPENAI_KEY]):
    logging.warning("⚠️ Missing one or more environment variables!")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
openai_client = OpenAI(api_key=OPENAI_KEY)

# ──────────────────────────────────────────────
# 🧩 FASTAPI SETUP
# ──────────────────────────────────────────────
app = FastAPI(title="🐦 Hummingbird FastAPI")

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
    try:
        logging.info(f"🧩 Executing RPC: {name} with payload → {payload}")
        res = supabase.rpc(name, payload).execute()
        if hasattr(res, "data") and res.data is not None:
            return res
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
# 🧩 Submit MCQ Answer — ✅ UPDATED WITH CHAPTER_ID + REACT_ORDER
# ──────────────────────────────────────────────
@app.post("/submit_mcq_answer")
async def submit_mcq_answer(request: Request):
    try:
        data = await request.json()
        logging.info(f"🧾 MCQ Attempt Payload → {json.dumps(data, indent=2)}")

        response = supabase.table("student_mcq_attempts").upsert({
            "student_id": data.get("p_student_id"),
            "mcq_uuid": data.get("p_mcq_uuid"),
            "selected_option": data.get("p_selected_option"),
            "correct_answer": data.get("p_correct_answer"),
            "is_correct": data.get("p_is_correct"),
            # 🆕 Added contextual fields
            "chapter_id": data.get("p_chapter_id"),
            "react_order": data.get("p_react_order"),
        }).execute()

        logging.info("✅ MCQ upserted successfully.")
        return {"status": "success", "details": response.data}
    except Exception as e:
        logging.error(f"❌ Error in /submit_mcq_answer: {e}")
        return {"status": "error", "message": str(e)}

# ──────────────────────────────────────────────
# 💬 Mentor Chat / Ask Doubt (Conversation Block Mode)
# ──────────────────────────────────────────────
@app.post("/mentor_chat")
async def mentor_chat(request: Request):
    """
    Handles both first and subsequent chat messages.
    Expected payloads:
    ▶ First message:
       {
         "user_id": "...",
         "student_name": "Manu",
         "chapter_id": "...",
         "phase_json": {...},
         "question": "Why does gymnosperm xylem lack vessels?"
       }
    ▶ Next messages:
       {
         "user_id": "...",
         "student_name": "Manu",
         "chapter_id": "...",
         "block_id": "...",
         "question": "Then what about phloem?"
       }
    """
    try:
        data = await request.json()
        logging.info(f"💬 Incoming payload:\n{json.dumps(data, indent=2)}")
    except Exception as e:
        logging.error(f"❌ Invalid JSON: {e}")
        return {"error": "Invalid JSON payload"}

    user_id = data.get("user_id")
    student_name = data.get("student_name", "Student")
    chapter_id = data.get("chapter_id")
    question = data.get("question", "").strip()
    phase_json = data.get("phase_json", {})
    block_id = data.get("block_id")  # present if continuing same conversation

    if not user_id or not question:
        return {"error": "Missing user_id or question"}

    try:
        # 🧩 Prepare base prompt (used only once per block)
        mentor_prompt = (
            "You are AI Mentor, an expert teacher with years of experience tutoring students "
            "for NEET exams. The student is asking a doubt related to the pre-loaded study content "
            "given in the JSON below.\n\n"
            "Use that JSON context and the student’s question to give a clear, NCERT-aligned explanation with:\n"
            "• Simple step-by-step reasoning\n"
            "• High-yield facts (tables or lists)\n"
            "• Short anecdotes or analogies if helpful\n"
            "• Formulas and key terms in **bold** / *italic* with proper Unicode symbols\n"
            "• Next question suggestion and Next info tip at the end\n\n"
            "Output should be friendly, precise, and exam-oriented — like a real teacher guiding a student in person."
        )

        # ──────────────────────────────────────────────
        # 🟢 FIRST MESSAGE → new conversation block
        # ──────────────────────────────────────────────
        if not block_id:
            block_id = str(uuid.uuid4())
            messages = [
                {"role": "system", "content": mentor_prompt},
                {"role": "user", "content": f"Student Name: {student_name}"},
                {"role": "user", "content": f"Context JSON: {json.dumps(phase_json)}"},
                {"role": "user", "content": f"Question: {question}"}
            ]

            completion = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.7,
            )

            reply = completion.choices[0].message.content
            tokens_used = getattr(completion, "usage", {}).get("total_tokens") if hasattr(completion, "usage") else None
            messages.append({"role": "assistant", "content": reply})

            # 💾 Insert new conversation block
            supabase.table("student_conversation_log").insert({
                "user_id": user_id,
                "student_name": student_name,
                "chapter_id": chapter_id,
                "block_id": block_id,
                "prompt": question,
                "response": reply,
                "phase_context": phase_json,
                "messages": messages,
                "tokens_used": tokens_used,
                "created_at": datetime.datetime.utcnow().isoformat(),
            }).execute()

            return {"reply": reply, "block_id": block_id, "status": "success"}

        # ──────────────────────────────────────────────
        # 🟣 CONTINUED MESSAGE → existing block
        # ──────────────────────────────────────────────
        else:
            res = supabase.table("student_conversation_log").select("messages").eq("block_id", block_id).order("id", desc=True).limit(1).execute()
            if not res.data:
                return {"error": f"No active conversation found for block_id {block_id}"}

            messages = res.data[0]["messages"]
            if not isinstance(messages, list):
                messages = []

            # Append new question
            messages.append({"role": "user", "content": question})

            completion = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.7,
            )

            reply = completion.choices[0].message.content
            tokens_used = getattr(completion, "usage", {}).get("total_tokens") if hasattr(completion, "usage") else None
            messages.append({"role": "assistant", "content": reply})

            # Update same block in Supabase
            supabase.table("student_conversation_log").update({
                "prompt": question,
                "response": reply,
                "messages": messages,
                "tokens_used": tokens_used,
                "updated_at": datetime.datetime.utcnow().isoformat(),
            }).eq("block_id", block_id).execute()

            return {"reply": reply, "block_id": block_id, "status": "success"}

    except Exception as e:
        logging.error(f"❌ Mentor Chat error: {e}")
        return {"error": str(e)}
