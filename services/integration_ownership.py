"""Provider-independent ownership of opaque integration secret references."""
from __future__ import annotations

from services.product_state import UserProductStateStore


class IntegrationOwnershipService:
    ALLOWED = frozenset({'google_calendar', 'tpu_source', 'alfacrm'})

    def __init__(self, store: UserProductStateStore): self.store = store

    def bind(self, user_id: str, integration: str, secret_reference: str) -> None:
        if integration not in self.ALLOWED: raise ValueError('Unsupported integration')
        if not secret_reference.startswith(('keychain:', 'vault:', 'oauth:')):
            raise ValueError('Integration must use an opaque secret reference')
        state = self.store.load(user_id)
        state.secret_references[integration] = secret_reference
        self.store.save(state)

    def reference_for(self, user_id: str, integration: str) -> str | None:
        if integration not in self.ALLOWED: raise ValueError('Unsupported integration')
        return self.store.load(user_id).secret_references.get(integration)
