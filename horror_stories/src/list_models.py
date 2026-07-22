from google import genai
import os
from dotenv import load_dotenv

load_dotenv()  # <-- YOU NEED THIS

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

models = client.models.list()

for m in models:
    print(m.name)
