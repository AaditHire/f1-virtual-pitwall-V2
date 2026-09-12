"""Explicit historical entity aliases without silently merging constructor identities."""

from __future__ import annotations

from dataclasses import dataclass


def normalized_alias(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


CONSTRUCTOR_LINEAGES = {
    "enstone": ("renault", "alpine"),
    "silverstone": ("racing_point", "aston_martin"),
    "faenza": ("toro_rosso", "alphatauri", "rb", "racing_bulls"),
}


@dataclass(frozen=True)
class EntityCatalog:
    driver_aliases: dict[str, tuple[str, ...]]
    constructor_aliases: dict[str, tuple[str, ...]]

    def resolve_driver(self, value: str) -> str | None:
        return self._resolve(value, self.driver_aliases)

    def resolve_constructor(self, value: str) -> str | None:
        return self._resolve(value, self.constructor_aliases)

    @staticmethod
    def _resolve(value: str, aliases: dict[str, tuple[str, ...]]) -> str | None:
        needle = normalized_alias(value)
        matches = [
            entity_id
            for entity_id, values in aliases.items()
            if needle in {normalized_alias(alias) for alias in (entity_id, *values)}
        ]
        return matches[0] if len(matches) == 1 else None


def build_entity_catalog(races: list[dict]) -> EntityCatalog:
    drivers: dict[str, set[str]] = {}
    constructors: dict[str, set[str]] = {}
    for race in races:
        for row in race["Results"]:
            driver = row["Driver"]
            driver_id = driver["driverId"]
            drivers.setdefault(driver_id, set()).update(
                filter(
                    None,
                    [
                        driver.get("code"),
                        driver.get("familyName"),
                        f"{driver.get('givenName', '')} {driver.get('familyName', '')}".strip(),
                    ],
                )
            )
            constructor = row["Constructor"]
            constructors.setdefault(constructor["constructorId"], set()).add(constructor["name"])
    return EntityCatalog(
        driver_aliases={key: tuple(sorted(value)) for key, value in sorted(drivers.items())},
        constructor_aliases={
            key: tuple(sorted(value)) for key, value in sorted(constructors.items())
        },
    )
