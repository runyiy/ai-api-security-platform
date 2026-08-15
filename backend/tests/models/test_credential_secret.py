import base64

from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import SecretStr
from sqlalchemy import delete, inspect, select

from app.auth.secret_cipher import SecretCipher
from app.db.base import Base
from app.db.models import CredentialSecret
from app.db.session import SessionLocal, engine


PLAINTEXT = "local-persistence-fixture"
TEST_KEY = base64.urlsafe_b64encode(b"c" * 32).decode("ascii")


def test_credential_secret_is_registered_in_metadata() -> None:
    table = Base.metadata.tables["credential_secrets"]

    assert table is CredentialSecret.__table__
    assert set(table.columns.keys()) == {
        "id",
        "encrypted_payload",
        "format_version",
        "key_version",
        "created_at",
        "updated_at",
    }
    assert "plaintext" not in table.columns


def test_migration_created_encrypted_secret_table() -> None:
    columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("credential_secrets")
    }

    assert set(columns) == {
        "id",
        "encrypted_payload",
        "format_version",
        "key_version",
        "created_at",
        "updated_at",
    }
    assert columns["encrypted_payload"]["nullable"] is False


def test_migration_extends_existing_head_linearly() -> None:
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = script.get_revision("d7e6a1b2c3f4")

    assert revision is not None
    assert revision.down_revision == "c4b8219e6d72"
    assert script.get_current_head() == "d7e6a1b2c3f4"


def test_persisted_row_contains_only_encrypted_secret_data() -> None:
    cipher = SecretCipher(
        encryption_key=SecretStr(TEST_KEY),
        key_version="test-v1",
    )
    envelope = cipher.encrypt(PLAINTEXT)
    secret_id: int | None = None

    try:
        with SessionLocal() as db:
            secret = CredentialSecret(
                encrypted_payload=envelope.encrypted_payload,
                format_version=envelope.format_version,
                key_version=envelope.key_version,
            )
            db.add(secret)
            db.commit()
            secret_id = secret.id

        with SessionLocal() as db:
            loaded = db.get(CredentialSecret, secret_id)
            raw_values = db.execute(
                select(
                    CredentialSecret.encrypted_payload,
                    CredentialSecret.format_version,
                    CredentialSecret.key_version,
                ).where(CredentialSecret.id == secret_id)
            ).one()

            assert loaded is not None
            assert loaded.encrypted_payload == envelope.encrypted_payload
            assert PLAINTEXT not in loaded.encrypted_payload
            assert PLAINTEXT not in repr(loaded)
            assert not hasattr(loaded, "plaintext")
            assert PLAINTEXT not in repr(raw_values)
            assert cipher.decrypt(envelope) == PLAINTEXT
            assert loaded.created_at is not None
            assert loaded.updated_at is not None
    finally:
        if secret_id is not None:
            with SessionLocal() as db:
                db.execute(
                    delete(CredentialSecret).where(
                        CredentialSecret.id == secret_id
                    )
                )
                db.commit()
