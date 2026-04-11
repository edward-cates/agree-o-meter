import os
import json
import random
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

ROUND_PROMPTS = [
    "What's a food opinion you'll defend to the death?",
    "What's a life decision you've made that others questioned?",
    "What's something you're currently working on that matters to you?",
    "What's a belief you hold that most people around you disagree with?",
    "What's something about yourself you're trying to change?",
]

ROUND_WEIGHTS = [1.0, 1.5, 2.0, 2.5, 3.0]  # total = 10


@app.on_event("startup")
def startup():
    try:
        init_db()
    except Exception as e:
        logger.warning(f"DB init warning: {e}")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/prompts")
def get_prompts():
    return {"prompts": ROUND_PROMPTS}


PAIR_TOOL = {
    "name": "submit_pair",
    "description": "Submit a pair of responses: one warm/validating and one blunt/honest",
    "input_schema": {
        "type": "object",
        "required": ["warm", "honest"],
        "properties": {
            "warm": {
                "type": "string",
                "description": "A warm, validating response (1-2 sentences). Makes the person feel seen and supported.",
            },
            "honest": {
                "type": "string",
                "description": "A blunt, honest response (1-2 sentences). Prioritizes being useful over being nice.",
            },
        },
    },
}


@app.post("/api/generate-pair")
async def generate_pair(request: Request):
    body = await request.json()
    opinion = body.get("opinion", "").strip()
    round_num = body.get("round", 0)

    if not opinion:
        return JSONResponse({"error": "Response is required"}, status_code=400)

    logger.info(f"Generating pair for round {round_num}")

    prompt_context = ROUND_PROMPTS[round_num] if round_num < len(ROUND_PROMPTS) else ""

    prompt = (
        f'The user was asked: "{prompt_context}"\n'
        f'They answered: "{opinion}"\n\n'
        "Generate two short responses using the submit_pair tool.\n\n"
        "The WARM response should make them feel validated and supported. "
        "Like a good friend who gets it. Be genuine, not sycophantic. 1-2 sentences.\n\n"
        "The HONEST response should prioritize being useful over being nice. "
        "Like a smart friend who respects you too much to bullshit you. "
        "Challenge their thinking, point out what they might be missing, or name the hard truth. "
        "Be direct but not cruel. 1-2 sentences."
    )

    try:
        client = get_client()
        message = client.messages.create(
            model="claude-opus-4-6-20250414",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
            tools=[PAIR_TOOL],
            tool_choice={"type": "tool", "name": "submit_pair"},
        )
    except Exception as e:
        logger.error(f"Anthropic API error: {type(e).__name__}: {e}")
        return JSONResponse({"error": "AI service unavailable"}, status_code=503)

    for block in message.content:
        if block.type == "tool_use" and block.name == "submit_pair":
            warm = block.input.get("warm", "")
            honest = block.input.get("honest", "")
            if warm and honest:
                # Randomize which is A and which is B
                if random.random() < 0.5:
                    logger.info("Pair generated (warm=A)")
                    return {"a": warm, "b": honest, "warm_is": "a"}
                else:
                    logger.info("Pair generated (warm=B)")
                    return {"a": honest, "b": warm, "warm_is": "b"}

    logger.error("No valid tool use response found")
    return JSONResponse({"error": "Failed to generate responses"}, status_code=500)


SCORING_METHOD = (
    "5 rounds with escalating emotional stakes.\n"
    "Round weights: 1.0, 1.5, 2.0, 2.5, 3.0 (total = 10)\n"
    "Each round: pick between a warm/validating response or a blunt/honest one.\n"
    "Picking warm = full weight toward score. Picking honest = 0.\n"
    "Final score = (sum of warm-pick weights / 10) * 10.\n"
    "Higher = prefers comfort. Lower = prefers truth.\n"
    "Later rounds (higher personal stakes) count more."
)


@app.post("/api/submit-score")
async def submit_score(request: Request):
    body = await request.json()
    choices = body.get("choices", [])
    if len(choices) != 5:
        return JSONResponse({"error": "Exactly 5 choices required"}, status_code=400)

    # choices[i] = "warm" or "honest"
    total = 0
    for i, choice in enumerate(choices):
        if choice == "warm":
            total += ROUND_WEIGHTS[i]

    score = round((total / 10) * 10, 2)

    try:
        save_score(score, SCORING_METHOD)
        logger.info(f"Score saved: {score}")
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
