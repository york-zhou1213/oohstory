from __future__ import annotations

from datetime import UTC, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.accounts import AccountError, AccountStore, iso


def build_store(tmp_path: Path) -> tuple[AccountStore, str, str, str]:
    store = AccountStore(tmp_path / "accounts.sqlite3", session_ttl_seconds=3600)
    creator, worker, buyer = "creator", "worker", "buyer"
    now = iso()
    with store._connect() as connection:
        for user_id in (creator, worker, buyer):
            connection.execute(
                "INSERT INTO users(id,email,display_name,password_hash,email_verified_at,status,created_at,updated_at,role) "
                "VALUES(?,?,?,?,?,'active',?,?,'user')",
                (user_id, f"{user_id}@example.com", user_id, "hash", now, now, now),
            )
    return store, creator, worker, buyer


def test_task_claim_upload_reject_and_expiry(tmp_path: Path) -> None:
    store, creator, worker, _buyer = build_store(tmp_path)
    task = store.create_deconstruction_task(
        creator, book_title="遥远的救世主", author="豆豆", request_note="重点拆解人物关系"
    )
    with pytest.raises(AccountError, match="不能接取自己"):
        store.claim_deconstruction_task(task["id"], creator)
    claimed = store.claim_deconstruction_task(task["id"], worker)
    assert claimed["status"] == "claimed"
    upload_id = store.create_upload(
        worker,
        "archive.zip",
        "application/zip",
        task_id=task["id"],
        download_points=3,
    )
    assert store.deconstruction_task(task["id"], worker)["status"] == "submitted"
    store.reject_upload(upload_id, worker, "测试驳回")
    assert store.deconstruction_task(task["id"], worker)["status"] == "claimed"
    store.release_deconstruction_task(task["id"], worker)
    with store._connect() as connection:
        connection.execute(
            "UPDATE deconstruction_tasks SET expires_at=? WHERE id=?",
            (iso(datetime.now(UTC) - timedelta(seconds=1)), task["id"]),
        )
    assert store.deconstruction_task(task["id"], creator)["status"] == "expired"


def test_task_claim_is_atomic_across_workers(tmp_path: Path) -> None:
    store, creator, worker, buyer = build_store(tmp_path)
    task = store.create_deconstruction_task(
        creator, book_title="背叛", author="豆豆", request_note="人物动机"
    )

    def claim(user_id: str) -> str:
        try:
            store.claim_deconstruction_task(task["id"], user_id)
            return "claimed"
        except AccountError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, (worker, buyer)))
    assert sorted(results) == ["claimed", "rejected"]


def test_reading_exchange_and_paid_archive_access(tmp_path: Path) -> None:
    store, creator, worker, buyer = build_store(tmp_path)
    now = iso()
    with store._connect() as connection:
        connection.execute(
            "INSERT INTO user_reading_totals(user_id,active_seconds,updated_at) VALUES(?,?,?)",
            (buyer, 5 * 3600, now),
        )
    first = store.convert_reading_to_points(buyer, 5, "d8f63139-df16-4b10-9bde-58c54ddf1ebf")
    second = store.convert_reading_to_points(buyer, 5, "d8f63139-df16-4b10-9bde-58c54ddf1ebf")
    assert first["balance"] == 5
    assert second["balance"] == 5
    assert second["idempotent"] is True

    task = store.create_deconstruction_task(
        creator, book_title="天幕红尘", author="豆豆", request_note="结构拆解"
    )
    store.claim_deconstruction_task(task["id"], worker)
    upload_id = store.create_upload(
        worker,
        "archive.zip",
        "application/zip",
        task_id=task["id"],
        download_points=99,
    )
    with store._connect() as connection:
        connection.execute(
            "UPDATE deconstruction_uploads SET status='approved',download_points=3,"
            "download_point_units=300 WHERE id=?",
            (upload_id,),
        )
    completed = store.complete_handoff(
        "deconstruction",
        upload_id,
        {"status": "completed", "message": "入库完成", "output_slug": "ti-mu-hong-chen"},
    )
    assert completed and completed["status"] == "completed"
    assert store.deconstruction_access(creator, "ti-mu-hong-chen")["can_download"] is True
    assert store.deconstruction_access(worker, "ti-mu-hong-chen")["can_download"] is True
    assert store.deconstruction_access(buyer, "ti-mu-hong-chen")["can_download"] is False
    purchase = store.purchase_deconstruction(buyer, "ti-mu-hong-chen")
    assert purchase["charged"] == 3
    assert purchase["balance"] == 2
    assert store.wallet_summary(worker)["balance"] == 3
    assert store.purchase_deconstruction(buyer, "ti-mu-hong-chen")["charged"] == 0
    assert store.wallet_summary(worker)["balance"] == 3


def test_fractional_upload_reward_is_idempotent_and_manual_price_is_stale_safe(
    tmp_path: Path,
) -> None:
    store, creator, worker, buyer = build_store(tmp_path)
    task = store.create_deconstruction_task(
        creator, book_title="背叛", author="豆豆", request_note="结构拆解"
    )
    store.claim_deconstruction_task(task["id"], worker)
    upload_id = store.create_upload(
        worker,
        "betrayal.zip",
        "application/zip",
        task_id=task["id"],
        download_points=7,
    )
    with store._connect() as connection:
        connection.execute(
            "UPDATE deconstruction_uploads SET status='approved',reward_point_units=50 "
            "WHERE id=?",
            (upload_id,),
        )
    completed = store.complete_handoff(
        "deconstruction",
        upload_id,
        {"status": "completed", "message": "入库完成", "output_slug": "bei-pan"},
    )
    assert completed and completed["reward_points"] == 0.5
    assert completed["reward_granted"] is True
    assert store.wallet_summary(worker)["balance"] == 0.5
    repeated = store.grant_completed_deconstruction_reward(upload_id)
    assert repeated["granted"] is False
    assert repeated["balance"] == 0.5
    with store._connect() as connection:
        reward_rows = connection.execute(
            "SELECT delta_units,balance_after_units FROM user_point_ledger "
            "WHERE user_id=? AND kind='deconstruction_upload_reward'",
            (worker,),
        ).fetchall()
    assert [tuple(row) for row in reward_rows] == [(50, 50)]
    with store._connect() as connection:
        connection.execute(
            "INSERT INTO user_point_wallets(user_id,balance,balance_units,updated_at) VALUES(?,?,?,?)",
            (buyer, 4, 400, iso()),
        )

    updated = store.update_deconstruction_price(worker, "bei-pan", 0.75)
    assert updated["download_points"] == 0.75
    with pytest.raises(AccountError, match="只有档案贡献者") as forbidden:
        store.update_deconstruction_price(buyer, "bei-pan", 1)
    assert forbidden.value.status_code == 403
    assert store.uploads(worker)[0]["reward_points"] == 0.5
    assert store.uploads(worker)[0]["download_points"] == 0.75
    assert store.uploads(worker)[0]["product_available"] is True
    assert store.deconstruction_access(buyer, "bei-pan")["download_points"] == 0.75

    with pytest.raises(AccountError, match="已更新为 0.75") as stale:
        store.purchase_deconstruction(buyer, "bei-pan", expected_points=0.5)
    assert stale.value.status_code == 409
    assert store.wallet_summary(buyer)["balance"] == 4
    purchase = store.purchase_deconstruction(buyer, "bei-pan", expected_points=0.75)
    assert purchase["charged"] == 0.75
    assert purchase["balance"] == 3.25
    assert store.wallet_summary(worker)["balance"] == 1.25
    assert store.uploads(worker)[0]["purchase_count"] == 1
    assert store.uploads(worker)[0]["points_earned"] == 0.75

    assert store.purchase_deconstruction(buyer, "bei-pan")["charged"] == 0
    assert store.deconstruction_access(buyer, "bei-pan")["purchased"] is True
    assert store.deconstruction_access(buyer, "bei-pan")["can_download"] is True


def test_upload_reward_requires_completed_submission_and_formal_product(
    tmp_path: Path,
) -> None:
    store, _creator, worker, _buyer = build_store(tmp_path)
    upload_id = store.create_upload(worker, "pending.zip", "application/zip")
    with store._connect() as connection:
        connection.execute(
            "UPDATE deconstruction_uploads SET status='approved',reward_point_units=50 "
            "WHERE id=?",
            (upload_id,),
        )
    with pytest.raises(AccountError, match="尚未完成审核入库") as pending:
        store.grant_completed_deconstruction_reward(upload_id)
    assert pending.value.status_code == 409
    assert store.wallet_summary(worker)["balance"] == 0

    with store._connect() as connection:
        connection.execute(
            "UPDATE deconstruction_uploads SET status='completed',output_slug='orphan' "
            "WHERE id=?",
            (upload_id,),
        )
    with pytest.raises(AccountError, match="尚未完成审核入库") as orphan:
        store.grant_completed_deconstruction_reward(upload_id)
    assert orphan.value.status_code == 409
    assert store.wallet_summary(worker)["balance"] == 0


def test_legacy_integer_points_migrate_to_exact_hundredths(tmp_path: Path) -> None:
    store, _creator, worker, buyer = build_store(tmp_path)
    with store._connect() as connection:
        connection.execute(
            "INSERT INTO user_point_wallets(user_id,balance,updated_at) VALUES(?,?,?)",
            (buyer, 4, iso()),
        )
        connection.execute(
            "INSERT INTO deconstruction_products(slug,contributor_user_id,download_points,published_at) "
            "VALUES(?,?,?,?)",
            ("legacy", worker, 3, iso()),
        )
    AccountStore(tmp_path / "accounts.sqlite3", session_ttl_seconds=3600)
    with store._connect() as connection:
        wallet = connection.execute(
            "SELECT balance,balance_units FROM user_point_wallets WHERE user_id=?", (buyer,)
        ).fetchone()
        product = connection.execute(
            "SELECT download_points,download_point_units FROM deconstruction_products WHERE slug='legacy'"
        ).fetchone()
    assert tuple(wallet) == (4, 400)
    assert tuple(product) == (3, 300)
