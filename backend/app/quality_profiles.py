"""Named quality profiles for the request modal (frontend parity with
design-exploration/obsidian.html's "Request" sheet). A profile is a
household-friendly name over the one per-request knob the pipeline
actually honours today — `min_resolution` (CreateRequest / BulkDownload
already accept it). `typical_size_gb` is a display estimate shown next
to the choice, not a limit; the real size gate stays the global
Pipeline & Quality range. Stored as one JSON list in the settings table
under `quality_profiles`, with `default_profile_id` naming the one
pre-selected in the modal (and used by one-tap requests elsewhere).
"""

from app.pipeline_settings import is_valid_min_resolution

DEFAULT_PROFILES: list[dict] = [
    {
        "id": "default",
        "name": "Household default",
        "description": "Uses the Downloads setting",
        "min_resolution": None,
        "typical_size_gb": None,
    },
    {
        "id": "4k",
        "name": "4K",
        "description": "2160p or better; the biggest, sharpest file available",
        "min_resolution": "2160p",
        "typical_size_gb": 55,
    },
    {
        "id": "1080p",
        "name": "1080p",
        "description": "Balanced size and quality",
        "min_resolution": "1080p",
        "typical_size_gb": 8,
    },
]
DEFAULT_PROFILE_ID = "default"

_ID_RE = __import__("re").compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def resolve_profiles(settings: dict) -> dict:
    """The stored profiles (or the shipped defaults when none were ever
    saved), always with a valid `default_profile_id`."""
    profiles = settings.get("quality_profiles")
    if not isinstance(profiles, list) or not profiles:
        profiles = [dict(p) for p in DEFAULT_PROFILES]
    default_id = settings.get("default_profile_id")
    if not any(p.get("id") == default_id for p in profiles):
        default_id = profiles[0]["id"]
    return {"profiles": profiles, "default_profile_id": default_id}


def validate_profiles(profiles: list[dict], default_id: str) -> None:
    if not profiles:
        raise ValueError("at least one profile is required")
    seen: set[str] = set()
    for p in profiles:
        pid = p.get("id")
        if not isinstance(pid, str) or not _ID_RE.match(pid):
            raise ValueError("profile ids must be short lowercase slugs")
        if pid in seen:
            raise ValueError(f"duplicate profile id {pid!r}")
        seen.add(pid)
        if not isinstance(p.get("name"), str) or not p["name"].strip():
            raise ValueError(f"profile {pid!r} needs a name")
        res = p.get("min_resolution")
        if res is not None and not is_valid_min_resolution(res):
            raise ValueError(f"profile {pid!r}: unknown min_resolution {res!r}")
        size = p.get("typical_size_gb")
        if size is not None and (not isinstance(size, (int, float)) or size <= 0):
            raise ValueError(f"profile {pid!r}: typical_size_gb must be a positive number")
    if default_id not in seen:
        raise ValueError("default_profile_id must name one of the profiles")


def profile_min_resolution(settings: dict, profile_id: str) -> str | None:
    """The resolution floor a profile stands for; KeyError for an unknown
    id so the API can 400 rather than silently fall back."""
    for p in resolve_profiles(settings)["profiles"]:
        if p["id"] == profile_id:
            return p.get("min_resolution")
    raise KeyError(profile_id)
