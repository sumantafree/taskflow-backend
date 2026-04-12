from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from database import get_db
import models
import schemas
from auth import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user,
    get_current_admin,
)

router = APIRouter(tags=["Auth & Users"])


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

@router.post("/auth/register", response_model=schemas.UserResponse, status_code=status.HTTP_201_CREATED)
def register(user_in: schemas.UserCreate, db: Session = Depends(get_db)):
    """Register a new user account."""
    existing = db.query(models.User).filter(models.User.email == user_in.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )
    user = models.User(
        name=user_in.full_name,
        email=user_in.email,
        hashed_password=hash_password(user_in.password),
        role=user_in.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/auth/login", response_model=schemas.Token)
def login(credentials: schemas.UserLogin, db: Session = Depends(get_db)):
    """Authenticate and return a JWT access token."""
    user = db.query(models.User).filter(models.User.email == credentials.email).first()
    if not user or not verify_password(credentials.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )
    token = create_access_token(data={"sub": str(user.id), "email": user.email})
    return schemas.Token(access_token=token, token_type="bearer", user=user)


@router.get("/auth/me", response_model=schemas.UserResponse)
def me(current_user: models.User = Depends(get_current_user)):
    """Return the authenticated user's profile."""
    return current_user


# ---------------------------------------------------------------------------
# User management (admin)
# ---------------------------------------------------------------------------

@router.get("/users/", response_model=List[schemas.UserResponse])
def list_users(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    _admin: models.User = Depends(get_current_admin),
):
    """List all users (admin only)."""
    return db.query(models.User).offset(skip).limit(limit).all()


@router.put("/users/{user_id}", response_model=schemas.UserResponse)
def update_user(
    user_id: int,
    user_in: schemas.UserUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Update a user. Admins can update any user; members can only update themselves."""
    if current_user.role != models.UserRole.admin and current_user.id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    update_data = user_in.model_dump(exclude_unset=True)

    # Map full_name → name (DB column)
    if "full_name" in update_data:
        update_data["name"] = update_data.pop("full_name")

    # Only admins can change roles
    if "role" in update_data and current_user.role != models.UserRole.admin:
        del update_data["role"]

    if "password" in update_data:
        update_data["hashed_password"] = hash_password(update_data.pop("password"))

    for field, value in update_data.items():
        setattr(user, field, value)

    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    _admin: models.User = Depends(get_current_admin),
):
    """Delete a user (admin only)."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    db.delete(user)
    db.commit()
