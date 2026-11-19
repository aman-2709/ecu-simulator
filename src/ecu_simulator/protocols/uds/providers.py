"""Extension points for UDS data services: who supplies DIDs and DTC records.

An ECU owns one :class:`DidRegistry` and one :class:`DtcRegistry`. UDS data services
(ReadDataByIdentifier, ReadDTCInformation and later WriteDataByIdentifier and
ClearDiagnosticInformation) read through them instead of owning data. A future
OBDonUDS module may plug in here as a provider, as a sibling protocol, or both; that
choice waits for SAE J1979-2 and nothing J1979-2-specific is defined here.

Phase 3 delivers the registries only. The legacy UDS wrapper still answers from its
own module data; Phase 6 (DtcStore) and the 0x22 work (V1.1) are the first users.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol, runtime_checkable

DID_MAX = 0xFFFF
DTC_MAX = 0xFFFFFF


class ProviderConflictError(ValueError):
    """A provider name or data identifier is already registered."""


@dataclass(frozen=True, slots=True)
class DtcRecord:
    """One ISO 14229 DTCAndStatusRecord: a 3-byte DTC number and its status byte."""

    dtc: int
    status: int

    def __post_init__(self) -> None:
        if not isinstance(self.dtc, int) or not 0 <= self.dtc <= DTC_MAX:
            raise ValueError(f"dtc must be a 3-byte value, got {self.dtc!r}")
        if not isinstance(self.status, int) or not 0 <= self.status <= 0xFF:
            raise ValueError(f"status must be one byte, got {self.status!r}")

    def to_bytes(self) -> bytes:
        return self.dtc.to_bytes(3, "big") + bytes([self.status])


@runtime_checkable
class DidProvider(Protocol):
    """Serves the data identifiers it claims."""

    @property
    def name(self) -> str: ...

    @property
    def data_identifiers(self) -> frozenset[int]: ...

    def read(self, did: int) -> bytes | None: ...


@dataclass(frozen=True, slots=True)
class DidWriteAccess:
    """Fixed-length write permission attached to one DID."""

    sessions: frozenset[int]
    security_level: int | None
    length: int

    def __post_init__(self) -> None:
        if not self.sessions:
            raise ValueError("writable DID sessions must not be empty")
        if any(not isinstance(session, int) or not 1 <= session <= 0x7E for session in self.sessions):
            raise ValueError(f"writable DID sessions must contain session bytes, got {sorted(self.sessions)!r}")
        if self.security_level is not None and (
            not isinstance(self.security_level, int) or not 1 <= self.security_level <= 0x3F
        ):
            raise ValueError(f"security_level must be null or in 1..63, got {self.security_level!r}")
        if not isinstance(self.length, int) or self.length <= 0:
            raise ValueError(f"writable DID length must be positive, got {self.length!r}")


@runtime_checkable
class WritableDidProvider(Protocol):
    """The optional write half of a DID provider."""

    @property
    def writable_data_identifiers(self) -> frozenset[int]: ...

    def write_access(self, did: int) -> DidWriteAccess | None: ...

    def write(self, did: int, data: bytes) -> None: ...


@runtime_checkable
class DtcProvider(Protocol):
    """Contributes DTC records; several providers may coexist on one ECU."""

    @property
    def name(self) -> str: ...

    def dtc_records(self) -> Iterable[DtcRecord]: ...


class DidRegistry:
    """Data identifier -> provider. Each DID has exactly one owner."""

    def __init__(self) -> None:
        self._providers: dict[str, DidProvider] = {}
        self._by_did: dict[int, DidProvider] = {}

    def register(self, provider: DidProvider) -> None:
        dids = frozenset(provider.data_identifiers)
        if not dids:
            raise ValueError(f"DID provider {provider.name!r} declares no data identifiers")
        for did in dids:
            if not isinstance(did, int):
                raise ValueError(f"DID provider {provider.name!r} declares non-integer DID {did!r}")
            if not 0 <= did <= DID_MAX:
                raise ValueError(f"DID provider {provider.name!r} declares invalid DID 0x{did:X}")
        if provider.name in self._providers:
            raise ProviderConflictError(f"a DID provider named {provider.name!r} is already registered")
        for did in sorted(dids):
            owner = self._by_did.get(did)
            if owner is not None:
                raise ProviderConflictError(f"DID 0x{did:04X} is claimed by both {owner.name!r} and {provider.name!r}")
        self._providers[provider.name] = provider
        for did in dids:
            self._by_did[did] = provider

    @property
    def providers(self) -> tuple[DidProvider, ...]:
        return tuple(self._providers.values())

    @property
    def data_identifiers(self) -> MappingProxyType[int, str]:
        return MappingProxyType({did: provider.name for did, provider in self._by_did.items()})

    def provider_for(self, did: int) -> DidProvider | None:
        return self._by_did.get(did)

    def read(self, did: int) -> bytes | None:
        provider = self._by_did.get(did)
        return provider.read(did) if provider is not None else None

    def write_access(self, did: int) -> DidWriteAccess | None:
        provider = self._by_did.get(did)
        if provider is None or not isinstance(provider, WritableDidProvider):
            return None
        return provider.write_access(did)

    def write(self, did: int, data: bytes) -> None:
        provider = self._by_did.get(did)
        if provider is None or not isinstance(provider, WritableDidProvider):
            raise KeyError(f"DID 0x{did:04X} is not writable")
        provider.write(did, data)


class DtcRegistry:
    """Ordered DTC providers; reads concatenate their records and apply a status mask."""

    def __init__(self) -> None:
        self._providers: dict[str, DtcProvider] = {}

    def register(self, provider: DtcProvider) -> None:
        if provider.name in self._providers:
            raise ProviderConflictError(f"a DTC provider named {provider.name!r} is already registered")
        self._providers[provider.name] = provider

    @property
    def providers(self) -> tuple[DtcProvider, ...]:
        return tuple(self._providers.values())

    def records(self) -> list[DtcRecord]:
        """Every record, whatever its status, in provider order."""
        return [record for provider in self._providers.values() for record in provider.dtc_records()]

    def read(self, status_mask: int) -> list[DtcRecord]:
        """Records whose status shares at least one bit with ``status_mask``, in provider order."""
        if not isinstance(status_mask, int) or not 0 <= status_mask <= 0xFF:
            raise ValueError(f"status_mask must be one byte, got {status_mask!r}")
        return [
            record
            for provider in self._providers.values()
            for record in provider.dtc_records()
            if record.status & status_mask
        ]
