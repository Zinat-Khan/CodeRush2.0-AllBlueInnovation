import os
import requests
from dotenv import load_dotenv

load_dotenv()

openai_key = os.getenv("OPENAI_API_KEY")
groq_key = os.getenv("GROQ_API_KEY")
openrouter_key = os.getenv("OPENROUTER_KEY_1")
google_key = os.getenv("GOOGLE_API_KEY")
tavily_key = os.getenv("TAVILY_API_KEY")
serper_key = os.getenv("SERPER_API_KEY")
alpha_key = os.getenv("ALPHA_VANTAGE_API_KEY")

print("=== TESTING API KEYS ===")

# 1. OpenAI
try:
    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {openai_key}"},
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        timeout=10
    )
    print(f"OpenAI API: status={r.status_code}, resp={r.json().get('choices', [{}])[0].get('message', {}).get('content') or r.text[:100]}")
except Exception as e:
    print(f"OpenAI API Error: {e}")

# 2. Groq
try:
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {groq_key}"},
        json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": "hi"}]},
        timeout=10
    )
    print(f"Groq API: status={r.status_code}, resp={r.json().get('choices', [{}])[0].get('message', {}).get('content') or r.text[:100]}")
except Exception as e:
    print(f"Groq API Error: {e}")

# 3. OpenRouter
try:
    r = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {openrouter_key}"},
        json={"model": "google/gemini-2.0-flash-lite-001", "messages": [{"role": "user", "content": "hi"}]},
        timeout=10
    )
    print(f"OpenRouter API: status={r.status_code}, resp={r.json().get('choices', [{}])[0].get('message', {}).get('content') or r.text[:100]}")
except Exception as e:
    print(f"OpenRouter API Error: {e}")

# 4. Google Gemini
try:
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={google_key}",
        headers={"Content-Type": "application/json"},
        json={"contents": [{"parts": [{"text": "hi"}]}]},
        timeout=10
    )
    print(f"Google Gemini API: status={r.status_code}, resp={r.text[:100]}")
except Exception as e:
    print(f"Google Gemini API Error: {e}")

# 5. Tavily
try:
    r = requests.post(
        "https://api.tavily.com/search",
        json={"api_key": tavily_key, "query": "domestic animals"},
        timeout=10
    )
    print(f"Tavily Search API: status={r.status_code}, resp={r.text[:100]}")
except Exception as e:
    print(f"Tavily Search API Error: {e}")

# 6. Serper
try:
    r = requests.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
        json={"q": "domestic animals"},
        timeout=10
    )
    print(f"Serper.dev API: status={r.status_code}, resp={r.text[:100]}")
except Exception as e:
    print(f"Serper.dev API Error: {e}")

# 7. Alpha Vantage
try:
    r = requests.get(
        f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=IBM&apikey={alpha_key}",
        timeout=10
    )
    print(f"Alpha Vantage API: status={r.status_code}, resp={r.text[:100]}")
except Exception as e:
    print(f"Alpha Vantage API Error: {e}")
