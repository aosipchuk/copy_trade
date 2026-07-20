from sqlalchemy.dialects import postgresql

from app.services.portfolio.subscription_lifecycle import (
    subscription_execution_allowed_clause,
)


def test_execution_guard_contains_new_wallet_parent_item_and_expiry_checks() -> None:
    compiled = str(
        subscription_execution_allowed_clause().compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "subscriptions.source_type = 'new_wallet'" in compiled
    assert "user_new_wallet_subscriptions.status = 'active'" in compiled
    assert "user_new_wallet_items.status = 'active'" in compiled
    assert "subscriptions.expires_at >" in compiled
