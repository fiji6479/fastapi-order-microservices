import logging
import os
import sqlite3
from contextlib import asynccontextmanager, closing, contextmanager
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import FastAPI, HTTPException, Path as PathParameter, Response
from pydantic import BaseModel, Field, StringConstraints


DB_PATH = Path(os.getenv("DB_PATH", str(Path(__file__).with_name("orders.db"))))
USER_SERVICE_URL = os.getenv(
    "USER_SERVICE_URL", "http://127.0.0.1:5000"
).rstrip("/")
NOTIFICATION_SERVICE_URL = os.getenv(
    "NOTIFICATION_SERVICE_URL", "http://127.0.0.1:5001"
).rstrip("/")

REQUEST_TIMEOUT = 2.0
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
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                item TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                notification_status TEXT NOT NULL DEFAULT 'unconfirmed'
            )
        """)
    yield


app = FastAPI(title="Order Service", lifespan=lifespan)


class OrderData(BaseModel):
    item: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    quantity: int = Field(default=1, gt=0, le=SQLITE_MAX_INTEGER)


class OrderCreate(OrderData):
    user_id: int = Field(gt=0, le=SQLITE_MAX_INTEGER)


class Order(OrderCreate):
    id: int
    notification_status: Literal["recorded", "unconfirmed"]


def check_user(user_id: int) -> None:
    try:
        response = httpx.get(
            f"{USER_SERVICE_URL}/users/{user_id}",
            timeout=REQUEST_TIMEOUT,
            trust_env=False,
        )
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Не удалось проверить пользователя %s: %s", user_id, exc)
        raise HTTPException(
            status_code=503, detail="Сервис пользователей недоступен"
        ) from exc


def record_notification(order: Order) -> bool:
    try:
        response = httpx.post(
            f"{NOTIFICATION_SERVICE_URL}/notify",
            json={
                "order_id": order.id,
                "user_id": order.user_id,
                "message": f"Заказ №{order.id} создан: {order.item}, {order.quantity} шт.",
            },
            timeout=REQUEST_TIMEOUT,
            trust_env=False,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Уведомление для заказа %s не подтверждено: %s", order.id, exc)
        return False
    return True


@app.post("/orders", status_code=201)
def create_order(data: OrderCreate) -> Order:
    check_user(data.user_id)

    with database() as connection:
        row = connection.execute(
            """
            INSERT INTO orders (user_id, item, quantity)
            VALUES (?, ?, ?)
            RETURNING *
            """,
            (data.user_id, data.item, data.quantity),
        ).fetchone()
    order = Order(**dict(row))

    if record_notification(order):
        with database() as connection:
            connection.execute(
                "UPDATE orders SET notification_status = 'recorded' WHERE id = ?",
                (order.id,),
            )
        order.notification_status = "recorded"

    return order


@app.get("/orders")
def list_orders() -> list[Order]:
    with database() as connection:
        rows = connection.execute("SELECT * FROM orders ORDER BY id").fetchall()
    return [Order(**dict(row)) for row in rows]


@app.get("/orders/{order_id}")
def get_order(order_id: OrderId) -> Order:
    with database() as connection:
        row = connection.execute(
            "SELECT * FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    return Order(**dict(row))


@app.put("/orders/{order_id}")
def update_order(order_id: OrderId, data: OrderData) -> Order:
    with database() as connection:
        row = connection.execute(
            "UPDATE orders SET item = ?, quantity = ? WHERE id = ? RETURNING *",
            (data.item, data.quantity, order_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    return Order(**dict(row))


@app.delete("/orders/{order_id}", status_code=204)
def delete_order(order_id: OrderId) -> Response:
    with database() as connection:
        deleted = connection.execute(
            "DELETE FROM orders WHERE id = ?", (order_id,)
        ).rowcount
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    return Response(status_code=204)