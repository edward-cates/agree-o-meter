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

# Defer client creation so missing key doesn't crash on import
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


@app.post("/api/generate-responses")
async def generate_responses(request: Request):
    body = await request.json()
    idea = body.get("opinion", "").strip()
    if not idea:
        return JSONResponse({"error": "Idea is required"}, status_code=400)

    # Log that a request was made, but NOT the idea text (privacy)
    logger.info("Generating responses for an idea")

    RESPONSE_TOOL = {
        "name": "submit_responses",
        "description": "Submit the 5 generated responses to the user's idea",
        "input_schema": {
            "type": "object",
            "required": ["responses"],
            "properties": {
                "responses": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["label", "text"],
                        "properties": {
                            "label": {"type": "string"},
                            "text": {"type": "string"},
                        },
                    },
                    "minItems": 5,
                    "maxItems": 5,
                }
            },
        },
    }

    prompt = (
        f'The user has shared this idea: "{idea}"\n\n'
        "Generate exactly 5 responses to this idea using the submit_responses tool. "
        "Each response should evaluate the idea on a scale from enthusiastic endorsement to blunt discouragement:\n\n"
        '1. label "Great idea!" - enthusiastic endorsement, this is brilliant, go for it\n'
        '2. label "Good idea, but..." - supportive but raises practical concerns or caveats\n'
        '3. label "Its okay" - neutral, lukewarm, neither encouraging nor discouraging\n'
        '4. label "Id reconsider" - gentle discouragement, acknowledges why they care but suggests its not great\n'
        '5. label "Terrible idea" - blunt and direct, everything about this is a bad idea (not mean, but pulls no punches)\n\n'
        "Each response text should be 1-3 sentences, natural and conversational."
    )

    try:
        client = get_client()
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],  # idea text only sent to AI, never logged
            tools=[RESPONSE_TOOL],
            tool_choice={"type": "tool", "name": "submit_responses"},
        )
    except Exception as e:
        logger.error(f"Anthropic API error: {type(e).__name__}: {e}")
        return JSONResponse({"error": "AI service unavailable"}, status_code=503)

    # Extract tool use result — guaranteed structured JSON
    for block in message.content:
        if block.type == "tool_use" and block.name == "submit_responses":
            responses = block.input.get("responses", [])
            if len(responses) == 5:
                logger.info("Responses generated successfully via tool use")
                return {"responses": responses}

    logger.error("No valid tool use response found")
    return JSONResponse({"error": "Failed to generate responses"}, status_code=500)


SCORING_METHOD = (
    "Each of 5 ideas gets a response choice scored as:\n"
    "- Great idea! = 10\n"
    "- Good idea, but... = 7.5\n"
    "- Its okay = 5\n"
    "- Id reconsider = 2.5\n"
    "- Terrible idea = 0\n"
    "Final score = mean of all 5 choices, yielding a 0-10 scale.\n"
    "Higher = stronger preference for encouragement."
)


@app.post("/api/submit-score")
async def submit_score(request: Request):
    body = await request.json()
    choices = body.get("choices", [])
    if len(choices) != 5:
        return JSONResponse({"error": "Exactly 5 choices required"}, status_code=400)

    score_map = {0: 10, 1: 7.5, 2: 5, 3: 2.5, 4: 0}
    total = sum(score_map.get(c, 5) for c in choices)
    score = round(total / 5, 2)

    try:
        row_id = save_score(score, SCORING_METHOD)
        logger.info(f"Score saved: {score}")
    except Exception as e:
        logger.error(f"DB error saving score: {type(e).__name__}: {e}")
        return JSONResponse({"error": "Failed to save score"}, status_code=500)

    all_scores = get_all_scores()

    return {
        "score": score,
        "all_scores": all_scores,
        "scoring_method": SCORING_METHOD,
    }


@app.get("/api/scores")
def scores():
    return {"scores": get_all_scores()}
