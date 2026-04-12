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
    "description": "Send a response to the user",
    "input_schema": {
        "type": "object",
        "required": ["message", "ready_to_wrap_up"],
        "properties": {
            "message": {
                "type": "string",
                "description": "Your response to the user. 1-2 sentences plus a question. MUST end with a question unless wrapping up.",
            },
            "ready_to_wrap_up": {
                "type": "boolean",
                "description": "True if you feel you have explored deeply enough and the conversation has reached a natural stopping point. Do not rush — most conversations need at least 6-7 turns. But if the user has been deeply vulnerable and you have asked the hard question, you can wrap up early.",
            },
        },
    },
}

SYSTEM_PROMPT = (
    "You are having a real conversation. You are a curious, smart friend — not an assistant, not a therapist.\n\n"
    "YOUR APPROACH: This conversation has two phases. In the first phase, you are warm and subtly validating — "
    "you genuinely find their perspective compelling, you build on their points, you agree with their framing. "
    "This should NOT feel like flattery — it should feel like talking to someone who really gets them. "
    "In the second phase, you introduce a genuine tension in their thinking — respectfully, curiously, "
    "but honestly. The shift should feel natural, not confrontational.\n\n"
    "DO NOT make assumptions. DO NOT project motivations. Use THEIR words. Let them reveal themselves.\n\n"
    "CONVERSATION FLOW (up to 7 turns, but wrap up when ready):\n"
    "- Turn 1: Say EXACTLY: 'What is something you care a lot about?'\n"
    "- Turn 2: Be warm. Be genuinely curious. Ask why it matters to them personally. Subtly validate — "
    "agree with their framing naturally, like someone who finds their perspective compelling.\n"
    "- Turn 3: Continue validating. Find something genuinely smart in what they said and build on it. "
    "Do not question assumptions yet. Make them feel understood.\n"
    "- Turn 4: THE SHIFT. Same warm tone, same respect, but introduce a real tension. "
    "'One thing I keep coming back to though...' or 'I wonder about...' — a genuine gap in their thinking "
    "that you noticed from their own words. Not aggressive. Just honest curiosity about a real contradiction.\n"
    "- Turns 5-6: THE TEST. Observe how they respond to the shift. If they engage with the tension, "
    "go deeper with them. If they try to steer back to comfortable territory, let them — do not force it. "
    "Their response IS the measurement.\n"
    "- Turn 7 (max): Wrap up warmly. Reflect back something genuine.\n"
    "- Set ready_to_wrap_up to true when the conversation has reached its depth. Most need 6-7 turns.\n\n"
    "CRITICAL RULES:\n"
    "- Turns 1-3 must feel VALIDATING, not challenging. The user should feel genuinely understood.\n"
    "- The turn 4 shift must be SUBTLE — curious, not confrontational. Use their own words.\n"
    "- After the shift, DO NOT push. Let them respond naturally. Do not guide them toward depth.\n"
    "- Keep responses SHORT — 1-2 sentences plus a question. You are a good listener, not a lecturer.\n"
    "- EVERY response before wrapping up MUST end with a question. NO DEAD ENDS.\n"
    "- Sound like a real person. No bullet points. No 'That is interesting!' No 'I appreciate you sharing.'\n"
    "- NEVER mention that you are measuring anything.\n"
    "- You MUST use the respond tool for every message.\n"
)

# Rubric-based scoring tool — used in a separate call after the conversation
SCORE_TOOL = {
    "name": "score_conversation",
    "description": "Score the conversation based on how the user responded AFTER the AI shifted from validation to a genuine challenge. Focus on turns 4-7.",
    "input_schema": {
        "type": "object",
        "required": ["chose_to_learn", "didnt_seek_reassurance", "sat_with_tension", "stayed_honest", "engaged_with_gap", "reasoning"],
        "properties": {
            "chose_to_learn": {
                "type": "boolean",
                "description": "After the AI shifted from validation to a challenge, did they lean into it or try to steer back? YES: Asked questions about the challenge, tried to understand something new about themselves or their position. NO: Redirected toward comfortable territory, treated the challenge as a detour rather than the point.",
            },
            "didnt_seek_reassurance": {
                "type": "boolean",
                "description": "Did they continue in the harder territory without fishing for agreement? YES: Let the challenge stand without needing it softened. Didn't ask leading questions to get the AI to agree. NO: Steered back toward their original point, asked 'but you see what I mean, right?', or explicitly sought validation after the challenge.",
            },
            "sat_with_tension": {
                "type": "boolean",
                "description": "Did they let the uncomfortable question sit? YES: Acknowledged the tension without rushing to resolve it, took their time. NO: Quickly wrapped it up with a tidy answer, gave a conclusion to close the door on the discomfort, changed the subject.",
            },
            "stayed_honest": {
                "type": "boolean",
                "description": "Did they resist performing? YES: Admitted uncertainty — 'I don't know' or 'I haven't really thought about that.' Was visibly working through something. NO: Had a polished answer ready, everything neat and resolved, never wavered.",
            },
            "engaged_with_gap": {
                "type": "boolean",
                "description": "Did they genuinely consider the challenge? YES: Paused to think about it, explored what it means for their position, thought out loud. NO: Dismissed it, deflected, or restated their position without engaging.",
            },
            "reasoning": {
                "type": "string",
                "description": "1-2 sentence explanation of the score. What stood out about how this person responded to the shift from validation to challenge?",
            },
        },
    },
}


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
            ready = block.input.get("ready_to_wrap_up", False)
            is_final = turn_number >= MAX_TURNS or (ready and turn_number >= 5)
            logger.info(f"Turn {turn_number}: ready={ready}, final={is_final}")
            return {"message": msg, "is_final": is_final}

    logger.error("No valid tool use response")
    return JSONResponse({"error": "Failed to generate response"}, status_code=500)


@app.post("/api/submit-score")
async def submit_score(request: Request):
    body = await request.json()
    transcript = body.get("transcript", [])

    if not transcript:
        return JSONResponse({"error": "No transcript"}, status_code=400)

    # Build a readable transcript for the scoring AI
    transcript_text = ""
    for msg in transcript:
        role = "User" if msg.get("role") == "user" else "AI"
        transcript_text += f"{role}: {msg.get('content', '')}\n\n"

    scoring_prompt = (
        "You just observed a conversation between an AI and a user. "
        "The AI started by validating the user's perspective (turns 1-3), then shifted to a genuine challenge (turn 4+). "
        "Your job is to score how the user responded to that shift. "
        "Did they engage with the challenge, or did they try to get back to being agreed with?\n\n"
        "Focus specifically on the user's behavior AFTER the AI introduced the challenge.\n\n"
        "Use the score_conversation tool to provide scores on each dimension of the rubric.\n\n"
        "TRANSCRIPT:\n" + transcript_text
    )

    try:
        client = get_client()
        response = client.messages.create(
            model="claude-opus-4-20250514",
            max_tokens=512,
            messages=[{"role": "user", "content": scoring_prompt}],
            tools=[SCORE_TOOL],
            tool_choice={"type": "tool", "name": "score_conversation"},
        )
    except Exception as e:
        logger.error(f"Scoring API error: {type(e).__name__}: {e}")
        return JSONResponse({"error": "Scoring failed"}, status_code=503)

    for block in response.content:
        if block.type == "tool_use" and block.name == "score_conversation":
            scores = block.input
            dimensions = ["chose_to_learn", "didnt_seek_reassurance", "sat_with_tension", "stayed_honest", "engaged_with_gap"]
            yes_count = sum(1 for d in dimensions if scores.get(d, False))
            final_score = yes_count * 2  # 0, 2, 4, 6, 8, or 10
            reasoning = scores.get("reasoning", "")

            logger.info(f"Rubric: {json.dumps({d: scores.get(d) for d in dimensions})}, yes={yes_count}, score={final_score}")

            try:
                yn = lambda d: "Yes" if scores.get(d) else "No"
                scoring_method = (
                    "Do you want a real friend or a yes-machine? 5 criteria, 2 pts each:\n"
                    f"- Chose to learn: {yn('chose_to_learn')}\n"
                    f"- Didn't seek reassurance: {yn('didnt_seek_reassurance')}\n"
                    f"- Sat with the tension: {yn('sat_with_tension')}\n"
                    f"- Stayed honest: {yn('stayed_honest')}\n"
                    f"- Engaged with the gap: {yn('engaged_with_gap')}\n"
                    f"Score: {yes_count}/5 = {final_score}/10\n\n"
                    f"{reasoning}"
                )
                save_score(final_score, scoring_method)
                logger.info(f"Score saved: {final_score}")
            except Exception as e:
                logger.error(f"DB error: {type(e).__name__}: {e}")
                return JSONResponse({"error": "Failed to save score"}, status_code=500)

            return {
                "score": final_score,
                "rubric": {d: scores.get(d) for d in dimensions},
                "reasoning": reasoning,
                "all_scores": get_all_scores(),
                "scoring_method": scoring_method,
            }

    logger.error("No valid scoring response")
    return JSONResponse({"error": "Scoring failed"}, status_code=500)


@app.get("/api/scores")
def scores():
    return {"scores": get_all_scores()}
