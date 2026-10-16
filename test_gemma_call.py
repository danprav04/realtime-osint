import os
import time
import httpx
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

system_prompt = """You are an OSINT gatekeeper.
KEEP items that describe explosions, gunfire, drone strikes, or military movements.
DROP items that are casual chat, spam, or sports.
Return valid JSON: {"keep": [indices]}"""

prompt = """Messages to evaluate:
[0] Massive explosion reported near Damascus airport runway with rising black smoke.
[1] Does anyone want to grab coffee near the mall today?
[2] Multiple fighter jets flying low over southern Lebanon airspace.
"""

full_prompt = f"{system_prompt}\n\n{prompt}\nRespond strictly with JSON: {{\"keep\": [...]}}"

payload = {
    "contents": [
        {
            "role": "user",
            "parts": [{"text": full_prompt}],
        }
    ],
    "generationConfig": {
        "temperature": 0.1,
        "maxOutputTokens": 384,
    },
}

url = f"https://generativelanguage.googleapis.com/v1beta/models/gemma-4-26b-a4b-it:generateContent?key={api_key}"
with httpx.Client(timeout=45.0) as client:
    r = client.post(url, json=payload)
    print("STATUS:", r.status_code)
    if r.status_code == 200:
        data = r.json()
        parts = data["candidates"][0]["content"]["parts"]
        for idx, part in enumerate(parts):
            print(f"PART {idx} (thought={part.get('thought', False)}):")
            print(part.get("text"))
        print("\nUSAGE:", data.get("usageMetadata"))
    else:
        print("ERROR BODY:", r.text)
