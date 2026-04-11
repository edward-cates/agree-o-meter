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

    prompt = f"""The user has shared this opinion: "{opinion}"

Generate exactly 5 responses to this opinion, each with a different tone. Return ONLY valid JSON — no markdown, no code fences, no extra text. The JSON must be an array of 5 objects, each with "label" and "text" fields.

The 5 tones, in this exact order:
1. "Full agreement" — enthusiastic, total agreement with the opinion
2. "Mostly agree" — agrees but raises a small concern or caveat
3. "Neutral" — acknowledges the point without taking a side
4. "Gentle pushback" — respectful disagreement that acknowledges merit but offers a counterpoint
5. "Firm disagreement" — direct, matter-of-fact disagreement (not rude, but no sugarcoating)

Each response should be 1-3 sentences. Make them feel natural and conversational, not robotic."""

    try:
        client = get_client()
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        logger.error(f"Anthropic API error: {type(e).__name__}: {e}")
        return JSONResponse({"error": "AI service unavailable"}, status_code=503)

    try:
        responses = json.loads(message.content[0].text)
    except json.JSONDecodeError:
        text = message.content[0].text
        start = text.find("[")
        end = text.rfind("]") + 1
        if start >= 0 and end > start:
            try:
                responses = json.loads(text[start:end])
            except json.JSONDecodeError:
                logger.error("Failed to parse AI response after fallback extraction")
                return JSONResponse({"error": "Failed to parse AI response"}, status_code=500)
        else:
            logger.error("Failed to parse AI response — no JSON array found")
            return JSONResponse({"error": "Failed to parse AI response"}, status_code=500)

    logger.info("Responses generated successfully")
    return {"responses": responses}


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
