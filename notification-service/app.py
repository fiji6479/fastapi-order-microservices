import logging
import os
import sqlite3
from contextlib import asynccontextmanager, closing, contextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Path as PathParameter, Response
from pydantic import BaseModel, Field, StringConstraints


DB_PATH = Path(os.getenv("DB_PATH", str(Path(__file__).with_name("notifications.db"))))
SQLITE_MAX_INTEGER = 2**63 - 1
OrderId = Annotated[int, PathParameter(gt=0, le=SQLITE_MAX_INTEGER)]
logger = logging.getLogger("uvicorn.error")


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
            CREATE TABLE IF NOT EXISTS notifications (
                order_id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                message TEXT NOT NULL
            )
        """)
    yield


app = FastAPI(title="Notification Service", lifespan=lifespan)


class Notification(BaseModel):
    order_id: int = Field(gt=0, le=SQLITE_MAX_INTEGER)
    user_id: int = Field(gt=0, le=SQLITE_MAX_INTEGER)
    message: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=250)
    ]


@app.post("/notify", status_code=201)
def create_notification(data: Notification, response: Response) -> Notification:
    with database() as connection:
        created = connection.execute(
            """
            INSERT INTO notifications (order_id, user_id, message)
            VALUES (?, ?, ?)
            ON CONFLICT(order_id) DO NOTHING
            """,
            (data.order_id, data.user_id, data.message),
        ).rowcount
        row = connection.execute(
            "SELECT order_id, user_id, message FROM notifications WHERE order_id = ?",
            (data.order_id,),
        ).fetchone()

    notification = Notification(**dict(row))
    if created:
        logger.info("Уведомление для пользователя %s: %s", data.user_id, data.message)
        return notification
    if notification != data:
        raise HTTPException(status_code=409, detail="Для заказа уже есть другое уведомление")
    response.status_code = 200
    return notification


@app.get("/notifications")
def list_notifications() -> list[Notification]:
    with database() as connection:
        rows = connection.execute(
            "SELECT order_id, user_id, message FROM notifications ORDER BY order_id"
        ).fetchall()
    return [Notification(**dict(row)) for row in rows]


@app.get("/notifications/{order_id}")
def get_notification(order_id: OrderId) -> Notification:
    with database() as connection:
        row = connection.execute(
            "SELECT order_id, user_id, message FROM notifications WHERE order_id = ?",
            (order_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Уведомление не найдено")
    return Notification(**dict(row))