import os
import json
import time
from datetime import datetime
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def get_next_scp_number():
    os.makedirs("stories", exist_ok=True)
    files = [f for f in os.listdir("stories") if f.startswith("SCP-") and f.endswith(".json")]
    if not files:
        return 1
    numbers = [int(f.split("-")[1].split(".")[0]) for f in files]
    return max(numbers) + 1


def generate_short_scp(number: int):
    prompt = f"""
    Write a VERY short SCP-style entry numbered SCP-{number:03d}.
    TOTAL LENGTH: 4–6 sentences only.
    Tone: eerie, mysterious, atmospheric.
    Structure:
    - Line 1: Item #: and Object Class
    - 2 sentences describing containment or discovery
    - 2–3 sentences describing its anomaly or twist
    End with:
    Scene Prompt 1: <one creepy visual>
    Scene Prompt 2: <one creepy visual>
    """

    print(f"🧠 Generating SCP-{number:03d}...")
    start = time.time()

    try:
        response = client.chat.completions.create(
            model="gpt-5",
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.choices[0].message.content
        print(f"✅ Done in {time.time() - start:.1f}s")

        entry = {
            "scp_number": f"SCP-{number:03d}",
            "created": datetime.utcnow().isoformat(),
            "entry_text": text.strip(),
        }
        return entry

    except Exception as e:
        print("⚠️ Error:", e)
        return {
            "scp_number": f"SCP-{number:03d}",
            "created": datetime.utcnow().isoformat(),
            "entry_text": f"SCP-{number:03d} — Fallback short entry.",
        }


def save_entry(entry):
    os.makedirs("stories", exist_ok=True)
    path = f"stories/{entry['scp_number']}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entry, f, indent=2)
    print(f"💾 Saved: {path}")
    return path
