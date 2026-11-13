"""Push notifications to the mobile app (md P8): register a phone, list and remove yours.
The banners themselves are sent by the worker."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep, SettingsDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain import push as rules
from cedesk.helpdesk.service import push

router = APIRouter(prefix="/me/devices", tags=["mobile"])


class DeviceIn(Body):
    platform: rules.Platform
    token: str = Field(
        min_length=rules.TOKEN_MIN,
        max_length=rules.TOKEN_MAX,
        description="The app's Firebase Cloud Messaging registration token (iOS too).",
    )


class DeviceOut(BaseModel):
    id: UUID
    platform: str
    token_hint: str = Field(description="The token's last 6 characters, never the token.")
    created_at: datetime
    last_seen_at: datetime


class DevicesOut(BaseModel):
    push_on: bool = Field(description="False: this server sends no banners (not set up).")
    devices: list[DeviceOut]


@router.get("", response_model=DevicesOut, summary="My phones that get banners")
async def my_devices(caller: CallerDep, sm: SessionmakerDep, settings: SettingsDep) -> DevicesOut:
    found = await push.devices(sm, caller)
    return DevicesOut(push_on=bool(settings.push_fcm_project_id),
                      devices=[DeviceOut(**d.__dict__) for d in found])  # fmt: skip


@router.post(
    "",
    response_model=DeviceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register this phone for banners",
    description="After sign-in, with the app's push token. Banners: a new ticket for you, "
    "a mention, a customer reply; they name the ticket's number only. The same token "
    "again just refreshes it (or moves it to you, if someone else used this phone). You "
    "keep your 10 newest phones.",
)
async def register(body: DeviceIn, caller: CallerDep, sm: SessionmakerDep) -> DeviceOut:
    got = await push.register(sm, caller, platform=body.platform.value, token=body.token)
    return DeviceOut(**got.__dict__)


@router.delete(
    "/{device_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Stop banners on this phone (call it on sign-out)",
    responses={404: {"description": "Not one of your devices"}},
)
async def unregister(device_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> Response:
    await push.unregister(sm, caller, device_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
