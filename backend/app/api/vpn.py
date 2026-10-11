"""The box's VPN from the app: Settings › VPN for the admin, and the setup
wizard's VPN step before there is one (setup-code gated, like the rest of
/api/setup/*). Both only check what's entered and leave it for the NAS
updater to apply — see app/vpn.py. Neither returns a secret, ever."""

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

from app import updates, vpn
from app.db import RequestStore
from app.api.deps import _client_ip, admin_router, get_store, limiter, require_setup_token
from app.api.main import app


class VPNSettings(BaseModel):
    provider: str
    type: str | None = None
    private_key: str | None = None
    public_key: str | None = None
    preshared_key: str | None = None
    addresses: str | None = None
    endpoint_ip: str | None = None
    endpoint_port: str | None = None
    user: str | None = None
    password: str | None = None
    server_countries: str | None = None
    # A provider's WireGuard file, pasted whole, in place of the fields.
    wireguard_file: str | None = None


def _overview() -> dict:
    if not vpn.managed():
        return {"managed": False}
    return {
        "managed": True,
        "providers": vpn.providers(),
        "applied": vpn.applied(),
        "status": vpn.status(),
        "updater_running": updates.updater_running(),
    }


def _save(body: VPNSettings) -> dict:
    if not vpn.managed():
        raise HTTPException(status_code=404, detail="This install has no VPN of its own")
    if not updates.updater_running():
        raise HTTPException(status_code=409, detail="The box's updater isn't running, so this can't be applied")
    fields = body.model_dump(exclude_none=True)
    try:
        if body.wireguard_file:
            fields = {**vpn.parse_wireguard_file(body.wireguard_file), "server_countries": body.server_countries or ""}
        settings = vpn.clean(fields)
    except vpn.VPNSettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    vpn.request(settings)
    return {"pending": True, "provider": settings["provider"], "type": settings["type"]}


@admin_router.get("/api/admin/vpn")
def get_vpn() -> dict:
    return _overview()


@admin_router.put("/api/admin/vpn", status_code=202)
def put_vpn(request: Request, body: VPNSettings, store: RequestStore = Depends(get_store)) -> dict:
    result = _save(body)
    store.record_auth_event("vpn_changed", ip_address=_client_ip(request), detail=vpn.PROVIDERS[result["provider"]]["label"])
    return result


@app.get("/api/setup/vpn", dependencies=[Depends(require_setup_token)])
def setup_get_vpn() -> dict:
    return _overview()


@app.put("/api/setup/vpn", status_code=202, dependencies=[Depends(require_setup_token)])
@limiter.limit("20/minute")
def setup_put_vpn(request: Request, body: VPNSettings) -> dict:
    return _save(body)
