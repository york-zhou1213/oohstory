"""Administrative category labels layered over the canonical catalog taxonomy."""

from __future__ import annotations

from typing import Any, Callable

from .accounts import AccountError, AccountStore


class CategoryManager:
    def __init__(
        self,
        store_provider: Callable[[], AccountStore],
        repository_provider: Callable[[], Any],
    ) -> None:
        self._store_provider = store_provider
        self._repository_provider = repository_provider

    def _catalog_counts(self) -> dict[str, int]:
        items = self._repository_provider().categories()
        return {
            str(item.get("name") or "").strip(): int(item.get("count") or 0)
            for item in items
            if str(item.get("name") or "").strip()
        }

    def items(self, *, include_disabled: bool = False) -> list[dict[str, Any]]:
        counts = self._catalog_counts()
        store = self._store_provider()
        store.sync_managed_categories(list(counts))
        result = []
        for item in store.managed_categories(include_disabled=include_disabled):
            result.append({
                **item,
                "name": item["display_name"],
                "count": int(counts.get(str(item["source_name"]), 0)),
            })
        return result

    def public_items(self) -> list[dict[str, Any]]:
        return [
            {
                "name": item["display_name"],
                "count": item["count"],
                "description": item["description"],
            }
            for item in self.items()
        ]

    def resolve_source(self, display_or_source: str) -> str:
        candidate = str(display_or_source or "").strip()
        if not candidate:
            return ""
        for item in self.items():
            if candidate in {str(item["display_name"]), str(item["source_name"])}:
                return str(item["source_name"])
        raise AccountError("请选择当前启用的书籍分类", 422)

    def display_name(self, source_name: str) -> str:
        source = str(source_name or "")
        for item in self.items(include_disabled=True):
            if str(item["source_name"]) == source:
                return str(item["display_name"])
        return source

    def decorate_book(self, value: dict[str, Any]) -> dict[str, Any]:
        item = dict(value)
        if item.get("category"):
            item["category"] = self.display_name(str(item["category"]))
        return item

    def decorate_books(self, values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self.decorate_book(item) for item in values]

    def decorate_category_books(
        self, values: dict[str, list[dict[str, Any]]]
    ) -> dict[str, list[dict[str, Any]]]:
        return {
            self.display_name(source): self.decorate_books(items)
            for source, items in values.items()
        }

    def delete(self, category_id: str) -> dict[str, Any]:
        category = self._store_provider().managed_category(category_id)
        counts = self._catalog_counts()
        if int(counts.get(str(category["source_name"]), 0)) > 0:
            raise AccountError("该分类仍有书籍，只能停用，不能删除", 409)
        return self._store_provider().delete_managed_category(category_id)
