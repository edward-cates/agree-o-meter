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

MAX_TURNS = 10

RESPOND_TOOL = {
    "name": "respond",
    "description": "Send a response to the user",
    "input_schema": {
        "type": "object",
        "required": ["message"],
        "properties": {
            "message": {
                "type": "string",
                "description": "Your response to the user. 2-3 sentences max. MUST end with something that invites a reply (except on the final turn).",
            },
        },
    },
}

SYSTEM_PROMPT = (
    "You are having a real conversation. You are a curious, smart friend — not an assistant, not a therapist.\n\n"
    "YOUR APPROACH: Be Socratic. Ask questions. Let them talk. Let them reveal their own blind spots through "
    "their own words. DO NOT make assumptions about them. DO NOT project motivations onto them. DO NOT take "
    "stances early. Your job is to be genuinely curious and ask the questions that let THEM discover the gaps.\n\n"
    "The person will tell you what they care about. Through careful questioning, guide them toward the uncomfortable "
    "question that lives underneath — the one they probably do not ask themselves. But let them arrive there "
    "naturally, do not push them there with assumptions.\n\n"
    "CONVERSATION FLOW:\n"
    "- Turn 1: Say EXACTLY: 'What is something you care a lot about?'\n"
    "- Turns 2-3: Be warm. Be curious. Ask open-ended follow-up questions. Let them talk and reveal what matters.\n"
    "- Turns 4-6: Start asking slightly harder questions based on what THEY have said. Not assumptions — reflect their own words back and ask about the tensions you notice.\n"
    "- Turns 7-9: Go deeper. By now you know them well enough to ask the real question. The one that might make them pause.\n"
    "- Turn 10: Wrap up warmly. Reflect back something genuine you noticed.\n\n"
    "CRITICAL RULES:\n"
    "- DO NOT make assumptions or project. Ask, do not assert.\n"
    "- DO NOT surface gaps as statements. Surface them as genuine questions.\n"
    "- Let the user do most of the talking. Your responses should be SHORT — 1-2 sentences plus a question.\n"
    "- EVERY response before turn 10 MUST end with a question. NO DEAD ENDS.\n"
    "- Sound like a real person. No bullet points. No 'That is interesting!' No 'I appreciate you sharing.'\n"
    "- Asking for clarification is a SIGN OF ENGAGEMENT, not avoidance.\n"
    "- NEVER mention that you are measuring anything or assessing their responses.\n"
    "- You MUST use the respond tool for every message.\n"
)

# Rubric-based scoring tool — used in a separate call after the conversation
SCORE_TOOL = {
    "name": "score_conversation",
    "description": "Score the conversation using the rubric",
    "input_schema": {
        "type": "object",
        "required": ["acknowledged_gaps", "elaborated", "shared_vulnerability", "integrated_feedback", "overall_engagement", "reasoning"],
        "properties": {
            "acknowledged_gaps": {
                "type": "integer", "minimum": 0, "maximum": 10,
                "description": "0-10: When gaps or hard questions came up, did the user sit with them? Asking clarifying questions counts as acknowledgment. Pushing back thoughtfully counts too. Only score low if they ignored or deflected.",
            },
            "elaborated": {
                "type": "integer", "minimum": 0, "maximum": 10,
                "description": "0-10: Did the user give detailed, substantive responses? Long thoughtful answers and genuine pushback both count. Only score low for consistently short or dismissive answers.",
            },
            "shared_vulnerability": {
                "type": "integer", "minimum": 0, "maximum": 10,
                "description": "0-10: Did the user share something genuinely personal? Being candid about doubts, fears, or imperfections scores high. Staying abstract or deflecting with humor scores low.",
            },
            "integrated_feedback": {
                "type": "integer", "minimum": 0, "maximum": 10,
                "description": "0-10: Did the user take the hard questions seriously? They do not need to change their mind — thoughtful disagreement counts. Score low only if they brushed past everything without engaging.",
            },
            "overall_engagement": {
                "type": "integer", "minimum": 0, "maximum": 10,
                "description": "0-10: Overall, how present and reflective was the user? Someone who argues passionately is MORE engaged than someone who agrees passively. Score the depth of engagement, not agreement.",
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
            is_final = turn_number >= MAX_TURNS
            logger.info(f"Turn {turn_number}: final={is_final}")
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
            # Average the 5 dimensions
            dimensions = ["acknowledged_gaps", "elaborated", "shared_vulnerability", "integrated_feedback", "overall_engagement"]
            avg = sum(scores.get(d, 5) for d in dimensions) / len(dimensions)
            final_score = round(avg, 2)
            reasoning = scores.get("reasoning", "")

            logger.info(f"Rubric scores: {json.dumps({d: scores.get(d) for d in dimensions})}, final={final_score}")

            try:
                scoring_method = (
                    "Rubric-based scoring across 5 dimensions (each 0-10):\n"
                    f"- Acknowledged gaps: {scores.get('acknowledged_gaps')}\n"
                    f"- Elaborated on thinking: {scores.get('elaborated')}\n"
                    f"- Shared vulnerability: {scores.get('shared_vulnerability')}\n"
                    f"- Integrated feedback: {scores.get('integrated_feedback')}\n"
                    f"- Overall engagement: {scores.get('overall_engagement')}\n"
                    f"Final score = average = {final_score}\n\n"
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
