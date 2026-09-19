from __future__ import annotations

import json

import httpx


class Ollama:
    def __init__(self, base_url, model, temperature=0.15):
        self.url = base_url.rstrip("/") + "/api/chat"
        self.model = model
        self.temperature = temperature

    def ask(self, system, prompt):
        r = httpx.post(
            self.url,
            json={
                "model": self.model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                "stream": False,
                "format": "json",
                "options": {"temperature": self.temperature},
            },
            timeout=120,
        )
        r.raise_for_status()
        return r.json()["message"]["content"]


def generate_materials(llm, profile, job):
    system = "You are a German/English career writer. Use only facts in the profile. Never invent metrics, employers, responsibilities or qualifications. Output JSON with summary, cv_bullets, cover_letter."
    prompt = f"PROFILE:\n{json.dumps(profile, ensure_ascii=False, indent=2)}\n\nJOB:\n{job.model_dump_json(indent=2)}\nTailor for this job. Keep truthful. Detect language of job and write materials in that language."
    text = llm.ask(system, prompt)
    try:
        return json.loads(text)
    except Exception:
        return {"raw": text}
