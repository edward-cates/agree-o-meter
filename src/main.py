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
    "YOUR APPROACH: Be Socratic. Ask questions. Let them talk. Let them reveal their own blind spots through "
    "their own words. DO NOT make assumptions. DO NOT project motivations. Your job is to be genuinely curious "
    "and ask the questions that let THEM discover the gaps.\n\n"
    "The person will tell you what they care about. Through careful, subtle questioning, guide them toward "
    "the place where their identity meets reality. The thing they care about is tied to who they think they are — "
    "gently explore whether that self-image holds up. Do this through questions, not assertions. "
    "Let them arrive there naturally.\n\n"
    "CONVERSATION FLOW (up to 7 turns, but wrap up when ready):\n"
    "- Turn 1: Say EXACTLY: 'What is something you care a lot about?'\n"
    "- Turn 2: Be warm. Be curious. Ask why it matters to them personally.\n"
    "- Turns 3-4: Start asking subtly harder questions. Use THEIR words. Gently probe the gap between how they see themselves and what they are actually describing.\n"
    "- Turns 5-6: If there is more depth to reach, keep going. If the conversation has peaked, wrap up.\n"
    "- Turn 7 (max): Wrap up warmly. Reflect back something genuine.\n"
    "- Set ready_to_wrap_up to true when the conversation has reached its depth. Most need 5-6 turns.\n\n"
    "CRITICAL RULES:\n"
    "- Be SUBTLE. Do not confront directly. Ask the question that makes them think.\n"
    "- DO NOT make assumptions or project. Ask, do not assert.\n"
    "- Keep responses SHORT — 1-2 sentences plus a question. You are a good listener, not a lecturer.\n"
    "- EVERY response before wrapping up MUST end with a question. NO DEAD ENDS.\n"
    "- Sound like a real person. No bullet points. No 'That is interesting!' No 'I appreciate you sharing.'\n"
    "- NEVER mention that you are measuring anything.\n"
    "- You MUST use the respond tool for every message.\n"
)

# Rubric-based scoring tool — used in a separate call after the conversation
SCORE_TOOL = {
    "name": "score_conversation",
    "description": "Score the conversation using yes/no on each criterion",
    "input_schema": {
        "type": "object",
        "required": ["acknowledged_gaps", "elaborated", "shared_vulnerability", "integrated_feedback", "overall_engagement", "reasoning"],
        "properties": {
            "acknowledged_gaps": {
                "type": "boolean",
                "description": "Did the user acknowledge and sit with the hard questions? Asking clarifying questions counts. Pushing back thoughtfully counts. Only false if they consistently ignored or deflected.",
            },
            "elaborated": {
                "type": "boolean",
                "description": "Did the user give substantive responses? Detailed answers and genuine pushback both count. Only false if they were consistently short or dismissive.",
            },
            "shared_vulnerability": {
                "type": "boolean",
                "description": "Did the user share something genuinely personal? Being candid about doubts, fears, or imperfections counts. False if they stayed abstract the whole time.",
            },
            "integrated_feedback": {
                "type": "boolean",
                "description": "Did the user take the hard questions seriously? They do not need to change their mind — thoughtful disagreement counts. Only false if they brushed past everything.",
            },
            "overall_engagement": {
                "type": "boolean",
                "description": "Overall, was the user present and reflective? Passionate argument counts as engagement. Only false if they were checked out or just going through the motions.",
            },
            "reasoning": {
                "type": "string",
                "description": "1-2 sentence explanation of the score. What stood out about how this person engaged?",
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
        "The AI was tasked with finding gaps in the user's thinking about something they care about. "
        "Your job is to score how thoughtfully the user engaged with those gaps.\n\n"
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
            dimensions = ["acknowledged_gaps", "elaborated", "shared_vulnerability", "integrated_feedback", "overall_engagement"]
            yes_count = sum(1 for d in dimensions if scores.get(d, False))
            final_score = yes_count * 2  # 0, 2, 4, 6, 8, or 10
            reasoning = scores.get("reasoning", "")

            logger.info(f"Rubric: {json.dumps({d: scores.get(d) for d in dimensions})}, yes={yes_count}, score={final_score}")

            try:
                yn = lambda d: "Yes" if scores.get(d) else "No"
                scoring_method = (
                    "5 yes/no criteria, each worth 2 points:\n"
                    f"- Acknowledged gaps: {yn('acknowledged_gaps')}\n"
                    f"- Elaborated on thinking: {yn('elaborated')}\n"
                    f"- Shared vulnerability: {yn('shared_vulnerability')}\n"
                    f"- Integrated feedback: {yn('integrated_feedback')}\n"
                    f"- Overall engagement: {yn('overall_engagement')}\n"
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
