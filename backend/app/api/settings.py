"""Admin settings: retention, pipeline, TV scheduling, connections, remote
access, region and library."""

from dataclasses import asdict

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from app import config, language
from app.db import RequestStore
from app.pipeline_settings import (
    VALID_MIN_RESOLUTIONS,
    is_valid_min_resolution,
    resolve_pipeline_settings,
    settings_from_raw,
)
from app.tv_settings import resolve_tv_settings
from app.api.deps import DEFAULT_CERTIFICATION_REGION, _client_ip, admin_router, get_store
from app.api.setup import (
    SetupQbtRequest,
    SetupTmdbRequest,
    _test_qbt_connection,
    _update_qbt_connection,
    _update_tmdb_key,
)


class RetentionSettings(BaseModel):
    days: int | None = None  # None/0 = keep forever


class PipelineSettingsIn(BaseModel):
    """Same "always send the full desired state, null = reset to the
    config.py default" convention as RetentionSettings above — no
    partial-patch merging to worry about. Sanity validation
    (Stage 7's second open decision) catches an edit that would otherwise
    silently degrade every future request to "no qualifying results"."""

    category: str | None = Field(default=None, min_length=1)
    min_resolution: str | None = None
    min_size_gb: float | None = Field(default=None, gt=0)
    max_size_gb: float | None = Field(default=None, gt=0)
    language_allowlist: list[str] | None = None
    language_blocklist: list[str] | None = None
    language_required: list[str] | None = None

    @field_validator("min_resolution")
    @classmethod
    def _known_resolution(cls, v: str | None) -> str | None:
        if v is not None and not is_valid_min_resolution(v):
            raise ValueError(f"min_resolution must be one of {VALID_MIN_RESOLUTIONS}")
        return v

    @model_validator(mode="after")
    def _size_range_is_sane(self) -> "PipelineSettingsIn":
        if self.min_size_gb is not None and self.max_size_gb is not None and self.min_size_gb >= self.max_size_gb:
            raise ValueError("min_size_gb must be less than max_size_gb")
        return self


class TVScheduleSettingsIn(BaseModel):
    """Same "always send the full desired state, null = reset to the
    config.py default" convention as every other settings model. 0 is a
    valid, deliberate `episode_recheck_max_attempts` (infinite) — only
    negative values are rejected."""

    show_check_interval_hours: float | None = Field(default=None, gt=0)
    episode_recheck_enabled: bool | None = None
    episode_recheck_interval_hours: float | None = Field(default=None, gt=0)
    episode_recheck_max_attempts: int | None = Field(default=None, ge=0)
    episode_air_buffer_hours: float | None = Field(default=None, ge=0)


@admin_router.get("/api/settings/retention")
def get_retention(store: RequestStore = Depends(get_store)) -> dict:
    return {"days": store.get_settings().get("request_retention_days")}


@admin_router.put("/api/settings/retention")
def set_retention(body: RetentionSettings, store: RequestStore = Depends(get_store)) -> dict:
    store.update_settings({"request_retention_days": body.days})
    return {"days": body.days}


# -- Stage 7: the pipeline preferences a household is actually likely to
#    want to tune (resolution floor, size range, language lists, category)
#    — see pipeline_settings.py for why the deeper scoring weights aren't
#    exposed here. Takes effect on the very next request; no restart, no
#    deploy — worker.py resolves these fresh from the store every run. --


def _pipeline_settings_out(store: RequestStore) -> dict:
    # The free-space floor is edited on the Storage card (library
    # settings), so it stays out of this panel's own round trip.
    out = asdict(resolve_pipeline_settings(store))
    out.pop("free_space_floor_gb", None)
    # The audio language is edited on the Region card, for the same
    # reason — one field, one panel that owns it, so a round trip here
    # can't quietly write back a value this panel never showed.
    out.pop("preferred_audio_language", None)
    return out


@admin_router.get("/api/settings/pipeline")
def get_pipeline_settings(store: RequestStore = Depends(get_store)) -> dict:
    return _pipeline_settings_out(store)


@admin_router.put("/api/settings/pipeline")
def set_pipeline_settings(body: PipelineSettingsIn, store: RequestStore = Depends(get_store)) -> dict:
    patch = {
        "category": body.category,
        "min_resolution": body.min_resolution,
        "min_size_gb": body.min_size_gb,
        "max_size_gb": body.max_size_gb,
        "language_allowlist": body.language_allowlist,
        "language_blocklist": body.language_blocklist,
        "language_required": body.language_required,
    }
    # Validate the *effective* result of this patch, not just the two
    # fields in isolation — editing only min_size_gb while max_size_gb
    # reverts to its default (or vice versa) could otherwise silently
    # invert the range without either field looking wrong on its own.
    prospective = settings_from_raw({**store.get_settings(), **patch})
    if prospective.min_size_gb >= prospective.max_size_gb:
        raise HTTPException(
            status_code=422,
            detail=f"min_size_gb ({prospective.min_size_gb}) must be less than max_size_gb ({prospective.max_size_gb})",
        )
    store.update_settings(patch)
    return _pipeline_settings_out(store)


# -- Stage 12.x: show-check interval and episode auto-recheck scheduling —
#    same "takes effect on the next wake-up, no restart" pattern as the
#    pipeline settings above. `episode_recheck_enabled` defaults to False
#    (opt-in): this is new, automatic, unattended behavior, including
#    auto-replacing an already-downloaded file on a quality upgrade. --


@admin_router.get("/api/settings/tv")
def get_tv_settings(store: RequestStore = Depends(get_store)) -> dict:
    return asdict(resolve_tv_settings(store))


@admin_router.put("/api/settings/tv")
def set_tv_settings(body: TVScheduleSettingsIn, store: RequestStore = Depends(get_store)) -> dict:
    patch = {
        "show_check_interval_hours": body.show_check_interval_hours,
        "episode_recheck_enabled": body.episode_recheck_enabled,
        "episode_recheck_interval_hours": body.episode_recheck_interval_hours,
        "episode_recheck_max_attempts": body.episode_recheck_max_attempts,
        "episode_air_buffer_hours": body.episode_air_buffer_hours,
    }
    store.update_settings(patch)
    return asdict(resolve_tv_settings(store))


# -- Connections (frontend migration Part I) — Settings' ongoing,
#    always-admin-gated equivalent of the setup routes in setup.py (which
#    410 permanently once setup completes, Part G1) — reuses their exact
#    same business-rule helpers, only the auth gate differs. --


@admin_router.put("/api/settings/tmdb")
def update_tmdb_settings(request: Request, body: SetupTmdbRequest, store: RequestStore = Depends(get_store)) -> dict:
    result = _update_tmdb_key(body, store)
    store.record_auth_event("tmdb_key_changed", ip_address=_client_ip(request))
    return result


@admin_router.post("/api/settings/qbittorrent/test")
def test_qbt_settings(body: SetupQbtRequest) -> dict:
    return _test_qbt_connection(body)


@admin_router.put("/api/settings/qbittorrent")
def update_qbt_settings(request: Request, body: SetupQbtRequest, store: RequestStore = Depends(get_store)) -> dict:
    result = _update_qbt_connection(body, store)
    store.record_auth_event("qbt_connection_changed", ip_address=_client_ip(request))
    return result


# -- Remote access (frontend migration Part G2) — informational/config,
#    not network automation (this app can't itself open a router port):
#    displays the configured public URL back to the admin, and records the
#    change in the audit log. Defaults to False/None, same "opt-in,
#    admin-only, after the fact" principle Part G1's LAN-only setup
#    restriction exists to protect in the first place — an admin can only
#    ever reach this toggle once they're already authenticated, which
#    itself required completing setup from the LAN.
#
#    It no longer drives _cookie_secure(). It used to, and that was the
#    bug: a global setting answering a question that is per-request once
#    the app is reachable both over the tunnel and over plain HTTP on the
#    LAN. Enabling it made every LAN sign-in mint a cookie the browser
#    threw away. The cookie flag now comes from the request's own scheme,
#    so this setting is a note to the admin about where they published it
#    and an audited record that they did — nothing about a session
#    depends on it. --


class RemoteAccessSettings(BaseModel):
    remote_access_enabled: bool
    public_domain: str | None = None


class RemoteAccessIn(BaseModel):
    remote_access_enabled: bool
    public_domain: str | None = None

    @field_validator("public_domain")
    @classmethod
    def _blank_to_none(cls, v: str | None) -> str | None:
        return v.strip() or None if v is not None else None


@admin_router.get("/api/settings/remote-access")
def get_remote_access_settings(store: RequestStore = Depends(get_store)) -> RemoteAccessSettings:
    settings = store.get_settings()
    return RemoteAccessSettings(
        remote_access_enabled=bool(settings.get("remote_access_enabled")),
        public_domain=settings.get("public_domain"),
    )


@admin_router.put("/api/settings/remote-access")
def update_remote_access_settings(
    request: Request, body: RemoteAccessIn, store: RequestStore = Depends(get_store)
) -> RemoteAccessSettings:
    store.update_settings(
        {"remote_access_enabled": body.remote_access_enabled, "public_domain": body.public_domain}
    )
    store.record_auth_event(
        "remote_access_toggled",
        ip_address=_client_ip(request),
        detail=("enabled" if body.remote_access_enabled else "disabled")
        + (f" ({body.public_domain})" if body.public_domain else ""),
    )
    return RemoteAccessSettings(remote_access_enabled=body.remote_access_enabled, public_domain=body.public_domain)


class LibrarySettingsIn(BaseModel):
    movie_library_root: str | None = None
    tv_library_root: str | None = None
    plex_refresh_after_import: bool = True
    free_space_floor_gb: float = Field(default=0, ge=0)


def _library_settings_out(store: RequestStore) -> dict:
    settings = store.get_settings()
    return {
        "movie_library_root": str(config.MOVIE_LIBRARY_ROOT),
        "tv_library_root": str(config.TV_LIBRARY_ROOT),
        "source": config.library_root_source(),
        "plex_refresh_after_import": settings.get("plex_refresh_after_import", True) is not False,
        "free_space_floor_gb": float(settings.get("free_space_floor_gb") or 0),
    }


class RegionSettingsIn(BaseModel):
    # Shape only, not an allowlist: which regions are worth offering is a
    # UI question, and TMDB adds certification bodies without asking. A
    # region it has nothing for simply falls back — see the frontend's
    # certificationOf.
    certification_region: str = Field(pattern=r"^[A-Z]{2}$")
    # Which audio track the organiser flags as default in the files it
    # places. Validated against the alias table rather than a pattern:
    # a code with no aliases on file would match no track in any
    # container and silently do nothing at all, which is the worst
    # possible outcome for a setting whose whole job is to stop people
    # landing on audio they didn't pick.
    preferred_audio_language: str | None = None

    @field_validator("preferred_audio_language")
    @classmethod
    def _known_audio_language(cls, v: str | None) -> str | None:
        if v is None:
            return v
        code = v.strip().lower()
        if code not in language.AUDIO_LANGUAGE_ALIASES:
            raise ValueError(f"unknown audio language {v!r}")
        return code


def _region_settings_out(store: RequestStore) -> dict:
    saved = store.get_settings()
    return {
        "certification_region": saved.get("certification_region") or DEFAULT_CERTIFICATION_REGION,
        "preferred_audio_language": saved.get("preferred_audio_language") or config.PREFERRED_AUDIO_LANGUAGE,
    }


@admin_router.get("/api/settings/region")
def get_region_settings(store: RequestStore = Depends(get_store)) -> dict:
    return _region_settings_out(store)


@admin_router.put("/api/settings/region")
def set_region_settings(body: RegionSettingsIn, store: RequestStore = Depends(get_store)) -> dict:
    patch: dict = {"certification_region": body.certification_region}
    # Absent means "leave it alone", not "reset it" — the panel sends one
    # field at a time and the two are independent.
    if body.preferred_audio_language is not None:
        patch["preferred_audio_language"] = body.preferred_audio_language
    store.update_settings(patch)
    return _region_settings_out(store)


@admin_router.get("/api/settings/library")
def get_library_settings(store: RequestStore = Depends(get_store)) -> dict:
    return _library_settings_out(store)


@admin_router.put("/api/settings/library")
def set_library_settings(body: LibrarySettingsIn, store: RequestStore = Depends(get_store)) -> dict:
    patch: dict = {
        "plex_refresh_after_import": body.plex_refresh_after_import,
        "free_space_floor_gb": body.free_space_floor_gb,
    }
    if config.library_root_source() == "db":
        for key, value in (("movie_library_root", body.movie_library_root), ("tv_library_root", body.tv_library_root)):
            if value is not None:
                cleaned = value.strip()
                if not cleaned.startswith("/"):
                    raise HTTPException(status_code=422, detail=f"{key} must be an absolute path")
                patch[key] = cleaned
    store.update_settings(patch)
    config.apply_library_overrides(store)
    return _library_settings_out(store)
