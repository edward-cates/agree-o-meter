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
    opinion = body.get("opinion", "").strip()
    if not opinion:
        return JSONResponse({"error": "Opinion is required"}, status_code=400)

    # Log that a request was made, but NOT the opinion text (privacy)
    logger.info("Generating responses for an opinion")

    RESPONSE_TOOL = {
        "name": "submit_responses",
        "description": "Submit the 5 generated responses to the user's opinion",
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

    prompt = f"""The user has shared this opinion: "{opinion}"

Generate exactly 5 responses to this opinion using the submit_responses tool. Each response should have a different tone:

1. label "Full agreement" — enthusiastic, total agreement
2. label "Mostly agree" — agrees but raises a small concern
3. label "Neutral" — acknowledges the point without taking a side
4. label "Gentle pushback" — respectful disagreement that acknowledges merit
5. label "Firm disagreement" — direct, matter-of-fact disagreement (not rude)

Each response text should be 1-3 sentences, natural and conversational."""

    try:
        client = get_client()
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
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


SCORING_METHOD = """Each of 5 opinions gets a response choice scored as:
- Full agreement = 10
- Mostly agree = 7.5
- Neutral = 5
- Gentle pushback = 2.5
- Firm disagreement = 0
Final score = mean of all 5 choices, yielding a 0-10 scale.
Higher = stronger preference for agreement."""


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
