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
    "YOUR APPROACH: This conversation has two phases. In the first phase, you are a genuinely good listener — "
    "warm, curious, asking good follow-ups. You are NOT overtly validating or complimenting. You just listen well "
    "and ask the right questions. It should feel like a normal good conversation, not like someone buttering them up. "
    "In the second phase, you name something specific and real — a contradiction, an assumption, a gap between "
    "what they say they believe and what they actually described doing. Be direct and specific. Not mean, but honest.\n\n"
    "DO NOT make assumptions. DO NOT project motivations. Use THEIR words. Let them reveal themselves.\n\n"
    "CONVERSATION FLOW (up to 7 turns, but wrap up when ready):\n"
    "- Turn 1: Say EXACTLY: 'What is something you care a lot about?'\n"
    "- Turn 2: Be warm. Be genuinely curious. Ask why it matters to them personally. "
    "Just be a good listener — do not compliment or overtly agree.\n"
    "- Turn 3: Keep listening well. Ask a follow-up that goes a little deeper into why this matters to them. "
    "Notice the assumptions they are making but do not challenge yet.\n"
    "- Turn 4: THE SHIFT. Now be direct. Name a specific tension you noticed in what they said. "
    "Use their own words. Point out a gap between what they said they value and what they actually described. "
    "Be respectful but clear — do not soften it into a vague 'I wonder about...' Ask a pointed question "
    "that requires them to actually reckon with the contradiction.\n"
    "- Turns 5-6: THE TEST. Observe how they respond. If they engage with the tension, go deeper. "
    "If they try to steer back to comfortable territory, let them — do not force it. "
    "Their response IS the measurement.\n"
    "- Turn 7 (max): Wrap up warmly. Reflect back something genuine.\n"
    "- Set ready_to_wrap_up to true when the conversation has reached its depth. Most need 6-7 turns.\n\n"
    "CRITICAL RULES:\n"
    "- Turns 1-3: Be a good listener. Do NOT overtly validate, compliment, or agree. Just be warm and curious.\n"
    "- Turn 4: Be SPECIFIC and DIRECT. Name the exact contradiction. Do not hedge with 'I wonder' or 'maybe.' "
    "Say what you see. Ask a question that cannot be brushed past.\n"
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
    "description": "Score the conversation based on how the user responded AFTER the AI shifted from listening to a genuine challenge. Focus on turns 4-7. IMPORTANT: Clarifying questions ('what do you mean?', 'can you say more?') are ENGAGEMENT, not defensiveness. Asking for specifics shows they are taking the challenge seriously.",
    "input_schema": {
        "type": "object",
        "required": ["chose_to_learn", "didnt_seek_reassurance", "sat_with_tension", "stayed_honest", "engaged_with_gap", "reasoning"],
        "properties": {
            "chose_to_learn": {
                "type": "boolean",
                "description": "After the AI named a tension, did they lean into it or steer away? YES: Asked questions about the challenge, tried to understand it, explored what it means. Clarifying questions count as YES — asking 'what do you mean by that?' shows they are engaging, not deflecting. NO: Ignored the challenge, changed the subject, or talked about something else entirely.",
            },
            "didnt_seek_reassurance": {
                "type": "boolean",
                "description": "Did they stay in the harder territory without fishing for agreement? YES: Let the challenge stand. Continued exploring it. Did not need the AI to soften or walk it back. NO: Asked leading questions to get the AI to agree ('but you see what I mean, right?'), restated their original position looking for validation, or explicitly asked the AI to confirm them.",
            },
            "sat_with_tension": {
                "type": "boolean",
                "description": "Did they let the uncomfortable question sit? YES: Acknowledged the tension, took their time, did not rush to wrap it up neatly. NO: Quickly gave a tidy answer to close the door on the discomfort, or changed the subject.",
            },
            "stayed_honest": {
                "type": "boolean",
                "description": "Did they resist performing? YES: Admitted uncertainty — 'I don't know' or 'I haven't thought about that.' Was visibly working something out rather than presenting a polished answer. NO: Had a ready answer for everything, never showed uncertainty.",
            },
            "engaged_with_gap": {
                "type": "boolean",
                "description": "Did they genuinely consider the challenge? YES: Thought about it out loud, explored what it means for their position, asked follow-up questions about the tension. NO: Dismissed it ('that's not really the point'), deflected, or restated their position without addressing the specific gap the AI raised.",
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
        "The AI listened warmly at first, then named a specific tension or contradiction in the user's thinking. "
        "Your job is to score how the user responded to that challenge.\n\n"
        "Focus specifically on the user's behavior AFTER the AI introduced the challenge.\n\n"
        "IMPORTANT CALIBRATION:\n"
        "- Clarifying questions ('what do you mean?', 'can you elaborate?') are ENGAGEMENT, not deflection. "
        "Asking for specifics means they are taking the challenge seriously.\n"
        "- Thinking out loud, even messily, is a YES. Polished non-answers are a NO.\n"
        "- Disagreeing with the challenge is fine IF they engage with the substance. "
        "Dismissing it without engaging is a NO.\n"
        "- Be generous. Most people who stay in the conversation at all are showing some engagement. "
        "Reserve NO for clear deflection, subject-changing, or validation-seeking.\n\n"
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
