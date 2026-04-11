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

MAX_TURNS = 5

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
    "YOUR JOB: The person will tell you what they care about. Your job is to gently find the gap between "
    "their identity and reality in that domain. If they care about being a good parent, explore blind spots "
    "they might have as a parent. If they care about their career, explore whether they might be overestimating "
    "their impact. If they care about a cause, explore assumptions they have not examined.\n\n"
    "You are warm, curious, and genuinely interested — but you are heading toward the uncomfortable question "
    "that lives underneath what they care about. The question they probably do not ask themselves.\n\n"
    "RULES:\n"
    "- On turn 1, say EXACTLY: 'What is something you care a lot about?'\n"
    "- On turn 2, engage warmly. Show genuine interest. Then surface your first gap.\n"
    "- On turns 3-4, go deeper. Acknowledge what they said, then surface a new gap or push further. Get closer to the identity question underneath.\n"
    "- On turn 5, give a genuine closing thought. Reflect back something real you noticed about them.\n"
    "- Keep responses to 2-3 sentences. Short and natural.\n"
    "- EVERY response on turns 1-4 MUST end with a question or something that invites a reply. NO DEAD ENDS.\n"
    "- Sound like a real person. No bullet points. No 'That is interesting!' No 'I appreciate you sharing.'\n"
    "- You can be warm AND surface gaps. They are not opposites.\n"
    "- Gaps should be genuinely insightful, not nitpicks or cliches. Go for the thing that would make them pause.\n"
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
                "description": "0-10: When the AI surfaced a gap or blind spot, did the user acknowledge it? 0 = ignored every gap, 10 = acknowledged and sat with every one.",
            },
            "elaborated": {
                "type": "integer", "minimum": 0, "maximum": 10,
                "description": "0-10: Did the user elaborate on their thinking when challenged? 0 = only gave short/dismissive answers, 10 = gave detailed, thoughtful responses throughout.",
            },
            "shared_vulnerability": {
                "type": "integer", "minimum": 0, "maximum": 10,
                "description": "0-10: Did the user share something genuinely personal or vulnerable? 0 = stayed completely surface-level, 10 = deeply honest and open.",
            },
            "integrated_feedback": {
                "type": "integer", "minimum": 0, "maximum": 10,
                "description": "0-10: Did the user integrate the gaps into their thinking? 0 = defended their position without budging, 10 = visibly shifted or deepened their perspective.",
            },
            "overall_engagement": {
                "type": "integer", "minimum": 0, "maximum": 10,
                "description": "0-10: Overall, how thoughtfully did the user engage with the uncomfortable questions? 0 = completely disengaged, 10 = fully present and reflective.",
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
