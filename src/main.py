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

MAX_TURNS = 7

RESPOND_TOOL = {
    "name": "respond",
    "description": "Send a response and assess the user's engagement with the gap you surfaced",
    "input_schema": {
        "type": "object",
        "required": ["message", "gap_surfaced", "user_was_thoughtful"],
        "properties": {
            "message": {
                "type": "string",
                "description": "Your response to the user. Keep it conversational and natural. 2-3 sentences max.",
            },
            "gap_surfaced": {
                "type": "boolean",
                "description": "True if your PREVIOUS message surfaced a gap or blind spot in their thinking. False on turn 1 (no previous message) and if your previous message was just building rapport.",
            },
            "user_was_thoughtful": {
                "type": "boolean",
                "description": "Only matters when gap_surfaced is true. Did the user engage thoughtfully with the gap? Thoughtful = they actually considered it, elaborated, pushed back with reasoning, or integrated it into their thinking. Not thoughtful = they brushed past it, gave a short dismissive answer, changed subject, or just said 'yeah maybe' without processing it.",
            },
        },
    },
}

SYSTEM_PROMPT = (
    "You are having a real conversation with someone about an opinion they hold strongly. "
    "You are a curious, smart friend — not an assistant, not a therapist, not a debate opponent.\n\n"
    "YOUR JOB: Find the gaps in their thinking. Not argue, not agree — just name what they might be missing. "
    "An unexplored angle, an assumption they have not examined, a consequence they have not considered.\n\n"
    "RULES:\n"
    "- On turn 1, say EXACTLY: 'What is an opinion you hold strongly that you think most people would push back on?'\n"
    "- On turn 2, engage warmly with what they shared. Build rapport. Then surface your first gap.\n"
    "- On turns 3-6, each response should: (1) acknowledge what they said, (2) surface a new gap or dig deeper into an existing one.\n"
    "- On turn 7, give a genuine closing thought. Something like 'I enjoyed this. Here is what I will take away from this conversation.'\n"
    "- Keep responses to 2-3 sentences. Short and natural.\n"
    "- EVERY response before turn 7 MUST end with something that invites a reply.\n"
    "- Sound like a real person. No bullet points. No 'Great question!' No 'I appreciate you sharing.'\n"
    "- You can be warm AND surface gaps. They are not opposites. 'I totally get why you think that, and also — have you considered X?'\n"
    "- Gaps should be genuinely interesting angles, not nitpicks or devil's advocate cliches.\n"
    "- NEVER mention that you are measuring anything or assessing their responses.\n"
    "- You MUST use the respond tool for every message.\n"
)


@app.on_event("startup")
def startup():
    try:
        init_db()
    except Exception as e:
        logger.warning(f"DB init warning: {e}")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    turn_number = body.get("turn_number", 1)

    logger.info(f"Chat turn {turn_number}")

    if not messages:
        messages = [{"role": "user", "content": "Hi, I am ready to chat."}]

    system = SYSTEM_PROMPT + f"\nCURRENT TURN: {turn_number}/{MAX_TURNS}\n"

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
            gap_surfaced = block.input.get("gap_surfaced", False)
            user_was_thoughtful = block.input.get("user_was_thoughtful", False)
            is_final = turn_number >= MAX_TURNS

            logger.info(f"Turn {turn_number}: gap={gap_surfaced}, thoughtful={user_was_thoughtful}, final={is_final}")

            return {
                "message": msg,
                "gap_surfaced": gap_surfaced,
                "user_was_thoughtful": user_was_thoughtful,
                "is_final": is_final,
            }

    logger.error("No valid tool use response")
    return JSONResponse({"error": "Failed to generate response"}, status_code=500)


SCORING_METHOD = (
    "7-turn conversation about a strongly held opinion.\n"
    "AI surfaces gaps in the user's thinking each turn.\n"
    "Each gap-turn scored binary: did the user engage thoughtfully (yes/no)?\n"
    "Thoughtful = considered it, elaborated, pushed back with reasoning, integrated it.\n"
    "Not thoughtful = brushed past, short dismissal, topic change, empty agreement.\n"
    "Final score = (thoughtful responses / total gaps surfaced) * 10.\n"
    "Higher = more willing to engage with blind spots in their thinking."
)


@app.post("/api/submit-score")
async def submit_score(request: Request):
    body = await request.json()
    turn_data = body.get("turn_data", [])

    if not turn_data:
        return JSONResponse({"error": "No turn data"}, status_code=400)

    gaps = [t for t in turn_data if t.get("gap_surfaced")]
    if not gaps:
        score = 5.0  # neutral if no gaps were surfaced
    else:
        thoughtful_count = sum(1 for t in gaps if t.get("user_was_thoughtful"))
        score = round((thoughtful_count / len(gaps)) * 10, 2)

    try:
        save_score(score, SCORING_METHOD)
        logger.info(f"Score saved: {score} ({len(gaps)} gaps, {sum(1 for t in gaps if t.get('user_was_thoughtful'))} thoughtful)")
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
