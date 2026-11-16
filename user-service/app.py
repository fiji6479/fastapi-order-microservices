import os
import sqlite3
from contextlib import asynccontextmanager, closing, contextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Path as PathParameter, Response
from pydantic import BaseModel, StringConstraints


DB_PATH = Path(os.getenv("DB_PATH", str(Path(__file__).with_name("users.db"))))
UserId = Annotated[int, PathParameter(gt=0, le=2**63 - 1)]


@contextmanager
def database():
    with closing(sqlite3.connect(DB_PATH)) as connection, connection:
        connection.row_factory = sqlite3.Row
        yield connection


@asynccontextmanager
async def lifespan(app: FastAPI):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with database() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL
            )
        """)
    yield


app = FastAPI(title="User Service", lifespan=lifespan)


class UserData(BaseModel):
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]


class User(UserData):
    id: int


@app.post("/users", status_code=201)
def create_user(data: UserData) -> User:
    with database() as connection:
        row = connection.execute(
            "INSERT INTO users (name) VALUES (?) RETURNING id, name",
            (data.name,),
        ).fetchone()
    return User(**dict(row))


@app.get("/users")
def list_users() -> list[User]:
    with database() as connection:
        rows = connection.execute("SELECT id, name FROM users ORDER BY id").fetchall()
    return [User(**dict(row)) for row in rows]


@app.get("/users/{user_id}")
def get_user(user_id: UserId) -> User:
    with database() as connection:
        row = connection.execute(
            "SELECT id, name FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return User(**dict(row))


@app.put("/users/{user_id}")
def update_user(user_id: UserId, data: UserData) -> User:
    with database() as connection:
        row = connection.execute(
            "UPDATE users SET name = ? WHERE id = ? RETURNING id, name",
            (data.name, user_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return User(**dict(row))


@app.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: UserId) -> Response:
    with database() as connection:
        deleted = connection.execute(
            "DELETE FROM users WHERE id = ?",
            (user_id,),
        ).rowcount
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return Response(status_code=204)