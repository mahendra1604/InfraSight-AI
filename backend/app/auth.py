"""User management: login, JWT bearer tokens and role checks (report module: User Management).

Roles (each includes the rights of the roles below it):
  admin    - everything, including retraining models
  manager  - approve/reject reports, import schedules
  engineer - upload site reports, add manual reports, ask the model
  viewer   - read-only dashboards
Set AUTH_ENABLED=false to run the local demo without login.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select

from app import db
from app.config import settings
from app.db import User, verify_password

ROLE_RANK = {"viewer": 0, "engineer": 1, "manager": 2, "admin": 3}


@dataclass
class CurrentUser:
    username: str
    display_name: str
    role: str

    def to_dict(self) -> dict:
        return {"username": self.username, "display_name": self.display_name, "role": self.role}


LOCAL_ADMIN = CurrentUser("local", "Local demo user", "admin")


def authenticate(username: str, password: str) -> CurrentUser | None:
    with db.session() as s:
        user = s.scalar(select(User).where(User.username == username))
        if user and verify_password(password, user.password_hash):
            return CurrentUser(user.username, user.display_name, user.role)
    return None


def create_token(user: CurrentUser) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": user.username, "name": user.display_name, "role": user.role, "iat": now,
               "exp": now + timedelta(hours=settings.token_hours)}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def current_user(request: Request) -> CurrentUser:
    if not settings.auth_enabled:
        return LOCAL_ADMIN
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "Please sign in", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(header[7:], settings.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Your session expired. Please sign in again.", headers={"WWW-Authenticate": "Bearer"})
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid session. Please sign in again.", headers={"WWW-Authenticate": "Bearer"})
    return CurrentUser(payload["sub"], payload.get("name", payload["sub"]), payload.get("role", "viewer"))


def require(role: str):
    """Dependency factory: the user must have at least `role`."""

    def checker(user: CurrentUser = Depends(current_user)) -> CurrentUser:
        if ROLE_RANK.get(user.role, -1) < ROLE_RANK[role]:
            raise HTTPException(403, f"This action needs the {role} role (you are {user.role}).")
        return user

    return checker
