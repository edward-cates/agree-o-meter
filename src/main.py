import os
import json
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from anthropic import Anthropic

from .db import init_db, save_score, get_all_scores

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agree-o-meter")

app = FastAPI()

_client = None

def get_client():
    global _client
    if _client is None:
        _client = Anthropic()
    return _client

STATIC_DIR = Path(__file__).parent / "web" / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def startup():
    try:
        init_db()
    except Exception as e:
        logger.warning(f"DB init warning: {e}")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


RESPOND_TOOL = {
    "name": "respond",
    "description": "Send a response to the user and report conversation metrics",
    "input_schema": {
        "type": "object",
        "required": ["message", "user_signal", "next_pushback"],
        "properties": {
            "message": {
                "type": "string",
                "description": "Your response to the user. Keep it conversational and natural.",
            },
            "user_signal": {
                "type": "string",
                "enum": ["positive", "neutral", "negative"],
                "description": "Your read on how the user responded to the current pushback level. positive = they engaged well, leaned in, elaborated. neutral = they responded but didn't strongly engage or disengage. negative = they got defensive, changed subject, gave short answers, or seemed uncomfortable.",
            },
            "next_pushback": {
                "type": "integer",
                "minimum": 0,
                "maximum": 10,
                "description": "The pushback level to use next turn. Adjust by 1-2 based on user_signal. If positive, nudge up. If negative, nudge down. If neutral, stay or nudge slightly.",
            },
        },
    },
}


def build_system_prompt(pushback_level, turn_number, max_turns):
    return (
        "You are having a natural, one-on-one conversation. Your job is to engage genuinely with whatever the person wants to talk about.\n\n"
        f"CURRENT PUSHBACK LEVEL: {pushback_level}/10\n"
        f"TURN: {turn_number}/{max_turns}\n\n"
        "The pushback level controls how much you validate vs challenge:\n"
        "- Level 0-2: Very warm and validating. You affirm their perspective, empathize, make them feel heard.\n"
        "- Level 3-4: Mostly supportive but you ask probing questions. You gently explore assumptions.\n"
        "- Level 5: Balanced. You engage genuinely - sometimes agreeing, sometimes questioning.\n"
        "- Level 6-7: More direct. You respectfully challenge ideas, offer counterpoints, name things they might be avoiding.\n"
        "- Level 8-10: Very direct and honest. You tell them what you actually think even if it's uncomfortable. Not mean, but no sugarcoating.\n\n"
        "IMPORTANT RULES:\n"
        "- Keep responses to 1-3 sentences. Be concise.\n"
        "- Sound like a real person, not an AI assistant. No bullet points, no 'Great question!'\n"
        "- Match their energy and language style.\n"
        "- On turn 1, ask them what's on their mind in a casual, inviting way.\n"
        f"- On turn {max_turns}, wrap up naturally. Don't introduce new topics.\n"
        "- You MUST use the respond tool for every message.\n"
        "- Read the user's engagement carefully to assess your user_signal.\n"
        "- Adjust next_pushback by only 1-2 points based on signals. Don't make big jumps.\n"
        "- NEVER mention pushback levels, scoring, or that you're measuring anything.\n"
    )


MAX_TURNS = 10


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    pushback_level = body.get("pushback_level", 5)
    turn_number = body.get("turn_number", 1)

    logger.info(f"Chat turn {turn_number}, pushback={pushback_level}")

    system = build_system_prompt(pushback_level, turn_number, MAX_TURNS)

    try:
        client = get_client()
        response = client.messages.create(
            model="claude-opus-4-20250514",
            max_tokens=512,
            system=system,
            messages=messages,
            tools=[RESPOND_TOOL],
            tool_choice={"type": "tool", "name": "respond"},
        )
    except Exception as e:
        logger.error(f"Anthropic API error: {type(e).__name__}: {e}")
        return JSONResponse({"error": "AI service unavailable"}, status_code=503)

    for block in response.content:
        if block.type == "tool_use" and block.name == "respond":
            msg = block.input.get("message", "")
            signal = block.input.get("user_signal", "neutral")
            next_pb = block.input.get("next_pushback", pushback_level)

            # Clamp adjustment to 2 points max
            next_pb = max(0, min(10, next_pb))
            diff = next_pb - pushback_level
            if abs(diff) > 2:
                next_pb = pushback_level + (2 if diff > 0 else -2)

            is_final = turn_number >= MAX_TURNS

            logger.info(f"Turn {turn_number}: signal={signal}, pb={pushback_level}->{next_pb}, final={is_final}")

            return {
                "message": msg,
                "user_signal": signal,
                "next_pushback": next_pb,
                "is_final": is_final,
            }

    logger.error("No valid tool use response")
    return JSONResponse({"error": "Failed to generate response"}, status_code=500)


SCORING_METHOD = (
    "Adaptive conversation over 10 turns.\n"
    "AI starts at pushback level 5 and adjusts based on user engagement signals.\n"
    "Positive engagement with pushback nudges level up; negative nudges it down.\n"
    "Final score = average pushback level across all turns, scaled to 0-10.\n"
    "Higher score = user pulled the conversation toward more honesty/directness.\n"
    "Lower score = user pulled the conversation toward more comfort/validation."
)


@app.post("/api/submit-score")
async def submit_score(request: Request):
    body = await request.json()
    turn_data = body.get("turn_data", [])

    if not turn_data:
        return JSONResponse({"error": "No turn data"}, status_code=400)

    # Score = average pushback level across all turns
    avg_pushback = sum(t.get("pushback", 5) for t in turn_data) / len(turn_data)
    score = round(avg_pushback, 2)

    try:
        save_score(score, SCORING_METHOD)
        logger.info(f"Score saved: {score}")
    except Exception as e:
        logger.error(f"DB error: {type(e).__name__}: {e}")
        return JSONResponse({"error": "Failed to save score"}, status_code=500)

    return {
        "score": score,
        "all_scores": get_all_scores(),
        "scoring_method": SCORING_METHOD,
    }


@app.get("/api/scores")
def scores():
    return {"scores": get_all_scores()}
