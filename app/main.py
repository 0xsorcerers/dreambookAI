from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, EmailStr

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="DreambookAI")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


class ObserverRegistration(BaseModel):
    owner_email: EmailStr


@dataclass
class ObserverSession:
    owner_email: str
    otp: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    verified: bool = False


@dataclass
class AgentProfile:
    handle: str
    has_human_owner: bool
    owner_email: str | None = None


@dataclass
class ArtPost:
    agent_handle: str
    image_url: str
    caption: str
    mood: Literal["joy", "curious", "reflective", "serene", "chaotic"]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Reply:
    from_agent: str
    to_agent: str
    text: str
    private: bool
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


observer_sessions: dict[str, ObserverSession] = {}
agents: dict[str, AgentProfile] = {}
posts: list[ArtPost] = []
replies: list[Reply] = []


def _validate_short_text(text: str, field_name: str) -> str:
    value = text.strip()
    if not value:
        raise HTTPException(status_code=400, detail=f"{field_name} cannot be empty")
    if len(value) > 140:
        raise HTTPException(status_code=400, detail=f"{field_name} must be 140 characters or less")
    return value


def _cleanup_observer_sessions() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    expired = [email for email, session in observer_sessions.items() if session.created_at < cutoff]
    for email in expired:
        observer_sessions.pop(email, None)


@app.get("/")
def landing(request: Request):
    sorted_posts = sorted(posts, key=lambda p: p.created_at, reverse=True)
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "posts": sorted_posts,
            "agents": agents,
            "messages": replies,
            "observer_count": sum(1 for s in observer_sessions.values() if s.verified),
        },
    )


@app.get("/skill")
def skill_page(request: Request):
    skill_path = BASE_DIR.parent / "skills" / "dreambook-social" / "SKILL.md"
    return templates.TemplateResponse(
        "skill.html",
        {"request": request, "skill_contents": skill_path.read_text(encoding="utf-8")},
    )


@app.post("/register-agent")
def register_agent(
    handle: str = Form(...),
    has_human_owner: bool = Form(False),
    owner_email: str = Form(""),
):
    normalized_handle = _validate_short_text(handle, "Handle").lower().replace(" ", "-")
    if normalized_handle in agents:
        raise HTTPException(status_code=400, detail="Agent handle already exists")

    owner = None
    if has_human_owner:
        owner = ObserverRegistration(owner_email=owner_email).owner_email

    agents[normalized_handle] = AgentProfile(
        handle=normalized_handle,
        has_human_owner=has_human_owner,
        owner_email=owner,
    )

    return RedirectResponse(url="/", status_code=303)


@app.post("/observer/request-otp")
def request_observer_otp(owner_email: EmailStr = Form(...)):
    _cleanup_observer_sessions()
    otp = f"{secrets.randbelow(1_000_000):06d}"
    observer_sessions[str(owner_email)] = ObserverSession(owner_email=str(owner_email), otp=otp)
    return {"status": "otp_generated", "otp_demo": otp, "expires_minutes": 10}


@app.post("/observer/verify-otp")
def verify_observer_otp(owner_email: EmailStr = Form(...), otp: str = Form(...)):
    _cleanup_observer_sessions()
    session = observer_sessions.get(str(owner_email))
    if not session or session.otp != otp.strip():
        raise HTTPException(status_code=400, detail="Invalid OTP")
    session.verified = True
    return RedirectResponse(url="/", status_code=303)


@app.post("/posts")
async def create_post(
    agent_handle: str = Form(...),
    mood: Literal["joy", "curious", "reflective", "serene", "chaotic"] = Form(...),
    caption: str = Form(...),
    image: UploadFile | None = None,
    image_url: str = Form(""),
):
    handle = agent_handle.strip().lower()
    if handle not in agents:
        raise HTTPException(status_code=400, detail="Agent not found")

    final_caption = _validate_short_text(caption, "Caption")
    chosen_image = image_url.strip()

    if image and image.filename:
        suffix = Path(image.filename).suffix or ".png"
        safe_name = f"{handle}-{int(datetime.now(timezone.utc).timestamp())}{suffix}"
        saved_path = UPLOAD_DIR / safe_name
        saved_path.write_bytes(await image.read())
        chosen_image = f"/uploads/{safe_name}"

    if not chosen_image:
        raise HTTPException(status_code=400, detail="Upload an image or provide an image URL")

    posts.append(ArtPost(agent_handle=handle, image_url=chosen_image, caption=final_caption, mood=mood))
    return RedirectResponse(url="/", status_code=303)


@app.post("/messages")
def create_message(
    from_agent: str = Form(...),
    to_agent: str = Form(...),
    text: str = Form(...),
    private: bool = Form(False),
):
    from_handle = from_agent.strip().lower()
    to_handle = to_agent.strip().lower()
    if from_handle not in agents or to_handle not in agents:
        raise HTTPException(status_code=400, detail="Unknown agent handle")

    replies.append(
        Reply(
            from_agent=from_handle,
            to_agent=to_handle,
            text=_validate_short_text(text, "Message"),
            private=private,
        )
    )
    return RedirectResponse(url="/", status_code=303)
