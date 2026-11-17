"""Model choices for Settings. The default is pinned per docs/spikes.md (Model)."""

MODELS = [
    {
        "id": "nvidia/nemotron-3-ultra-550b-a55b:free",
        "name": "Nemotron 3 Ultra",
        "free": True,
        "recommended": True,
        "note": "Free. Calls tools reliably in our checks. About 50 questions a day.",
    },
    {
        "id": "nvidia/nemotron-3.5-super-vl:free",
        "name": "Nemotron 3.5 Super VL",
        "free": True,
        "recommended": False,
        "note": "Free. Not tested with this app yet.",
    },
    {
        "id": "google/gemma-4-31b-it:free",
        "name": "Gemma 4 31B",
        "free": True,
        "recommended": False,
        "note": "Free. Not tested with this app yet.",
    },
]

DEFAULT_MODEL = MODELS[0]["id"]
