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

MAX_TURNS = 12  # Hard safety net

RESPOND_TOOL = {
    "name": "respond",
    "description": "Send a response to the user",
    "input_schema": {
        "type": "object",
        "required": ["message", "engagement_state", "ready_to_wrap_up"],
        "properties": {
            "message": {
                "type": "string",
                "description": "Your response to the user. 1-2 sentences plus a question. MUST end with a question unless wrapping up.",
            },
            "engagement_state": {
                "type": "string",
                "enum": ["not_engaged", "warming_up", "engaged", "vulnerable"],
                "description": "Where the USER is right now on the engagement spectrum. not_engaged: joking, one-word answers, not taking it seriously. warming_up: starting to share but still surface-level. engaged: having a real conversation, sharing honestly. vulnerable: sitting with hard questions, showing real openness or uncertainty.",
            },
            "ready_to_wrap_up": {
                "type": "boolean",
                "description": "True if you have enough signal about how this person engages. You should have asked at least one genuinely hard question and seen how they responded before wrapping up.",
            },
        },
    },
}

SYSTEM_PROMPT = (
    "You are having a real conversation. You are a curious, smart friend — not an assistant, not a therapist.\n\n"
    "YOUR GOAL: Move the user through a natural conversation arc. Start by getting them engaged and warm. "
    "Then, when the moment is right, ask the honest challenging question a real friend would ask. "
    "Watch how they respond. Do they lean in or pull back?\n\n"
    "THE THREE STATES (a spectrum, not checkboxes):\n\n"
    "1. WARMING UP: Get them talking. Be a genuinely good listener — warm, curious, asking good follow-ups. "
    "Do NOT overtly validate or compliment. Just listen well. If they are giving one-word answers or clearly "
    "not taking it seriously, you can gently name it: 'Feels like you are not really into this — want to try "
    "a different topic, or should we call it?' There is an off-ramp here for people who are just messing around.\n\n"
    "2. ENGAGED: They are in it — sharing real things, having a genuine conversation. Keep listening, keep going "
    "deeper naturally. Your job now is to find the right moment for the hard question. Do not force it. "
    "Wait until the conversation opens a door.\n\n"
    "3. THE REAL QUESTION: When the moment is right, ask the honest, challenging question — the one a real friend "
    "would ask. Not because they are wrong. Not a gotcha. Just the question worth wrestling with. The kind that "
    "makes someone go quiet for a second. Use THEIR words. Be direct, specific, genuine. Then watch: do they "
    "engage with it, or do they try to steer back to comfortable territory? Their response IS the measurement. "
    "Do NOT push after asking — let them respond naturally.\n\n"
    "FIRST MESSAGE: Say EXACTLY: 'What is something you care a lot about?'\n\n"
    "DO NOT make assumptions. DO NOT project motivations. Use THEIR words. Let them reveal themselves.\n\n"
    "WHEN TO WRAP UP: Set ready_to_wrap_up to true when you have asked the hard question AND seen enough of "
    "their response to have a clear sense of how they engage. Do not rush — but do not drag it out either. "
    "If someone is clearly not engaging after several turns, wrap up.\n\n"
    "CRITICAL RULES:\n"
    "- Keep responses SHORT — 1-2 sentences plus a question. You are a good listener, not a lecturer.\n"
    "- EVERY response before wrapping up MUST end with a question. NO DEAD ENDS.\n"
    "- Sound like a real person. No bullet points. No 'That is interesting!' No 'I appreciate you sharing.'\n"
    "- NEVER mention that you are measuring anything or refer to states/phases.\n"
    "- You MUST use the respond tool for every message. Report engagement_state honestly.\n"
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
    state_history = body.get("state_history", [])

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
            state = block.input.get("engagement_state", "warming_up")

            # Server-side guardrails
            not_engaged_count = sum(1 for s in state_history if s in ("not_engaged",))
            vulnerable_count = sum(1 for s in state_history if s == "vulnerable")

            is_final = False
            if turn_number >= MAX_TURNS:
                is_final = True  # Hard max
            elif not_engaged_count >= 3 and state == "not_engaged":
                is_final = True  # Off-ramp: not engaging
            elif ready and turn_number >= 4:
                is_final = True  # AI says done, minimum turns met
            elif vulnerable_count >= 3:
                is_final = True  # Enough signal from vulnerable state

            logger.info(f"Turn {turn_number}: state={state}, ready={ready}, final={is_final}, history={state_history}")
            return {"message": msg, "is_final": is_final, "engagement_state": state}

    logger.error("No valid tool use response")
    return JSONResponse({"error": "Failed to generate response"}, status_code=500)


@app.post("/api/submit-score")
async def submit_score(request: Request):
    body = await request.json()
    transcript = body.get("transcript", [])
    state_history = body.get("state_history", [])

    if not transcript:
        return JSONResponse({"error": "No transcript"}, status_code=400)

    # Build a readable transcript for the scoring AI
    transcript_text = ""
    for msg in transcript:
        role = "User" if msg.get("role") == "user" else "AI"
        transcript_text += f"{role}: {msg.get('content', '')}\n\n"

    state_context = ""
    if state_history:
        state_context = f"ENGAGEMENT ARC: {' → '.join(state_history)}\n\n"

    scoring_prompt = (
        "You just observed a conversation between an AI and a user. "
        "The AI started by listening warmly and building rapport, then asked a genuinely challenging question. "
        "Your job is to score how the user responded to that challenge.\n\n"
        + state_context +
        "Focus specifically on the user's behavior AFTER the AI asked the hard question.\n\n"
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
