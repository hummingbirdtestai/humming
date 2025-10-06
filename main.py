from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client
from openai import OpenAI
from dotenv import load_dotenv
import os

load_dotenv()

app = FastAPI()

# --- Middleware for CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Initialize Clients ---
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

@app.get("/")
def root():
    return {"status": "Hummingbird FastAPI running locally 🐦"}

@app.post("/mentor_api")
async def mentor_router(req: Request):
    data = await req.json()
    intent = data.get("intent")

    if intent == "get_phase":
        res = supabase.rpc("get_phase_content", {
            "chapter_id": data["chapter_id"],
            "user_id": data["user_id"]
        }).execute()
        return res.data

    elif intent == "ask_doubt":
        q = data.get("question", "")
        ans = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": q}]
        )
        return {"reply": ans.choices[0].message.content}

    else:
        return {"error": "Unknown intent"}
