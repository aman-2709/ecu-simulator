"""The AUTOSAR E2E Profile 1 and Profile 2 checksums, as rule-file presets ``e2e_p01`` and ``e2e_p02``.

Sources, both published by AUTOSAR on autosar.org:

- PRS: "E2E Protocol Specification", AUTOSAR_PRS_E2EProtocol, document ID 849, AUTOSAR FO R22-11. Profile 1 is
  section 6.5, Profile 2 section 6.6.
- CRC: "Specification of CRC Routines", AUTOSAR_SWS_CRCLibrary, document ID 16, AUTOSAR CP R22-11, section 7.2.1.

Both profiles are legacy in AUTOSAR (new projects use Profiles 11 and 22), and both are supported here only at their
standard positions: the CRC is byte 0 and the 4-bit counter the low nibble of byte 1. Profile 1 lets a configuration
move them; its variants 1A, 1B and 1C use these positions (PRS Table 6.17, CRCOffset 0 and CounterOffset 8), as does
Profile 2 with its default Offset 0 (PRS_E2E_00122, PRS_E2E_00123). ``ecu_simulator.car.rules`` refuses a rule whose
signals sit anywhere else.

Profile 1 (``e2e_p01``):

- The CRC is CRC-8 with the SAE J1850 polynomial 0x1D, but with start value and XOR value 0x00 (PRS_E2E_00070, PRS
  Table 6.12). The library function ``Crc_CalculateCRC8`` uses 0xFF for both (CRC Table 7.1); PRS Figures 6.55 and
  6.56 chain it with start value 0xFF and XOR the end result with 0xFF, which is the same CRC with 0x00 and 0x00.
- It runs first over the Data ID and then over every byte after the CRC byte (PRS_E2E_00082). How the 16-bit Data ID
  enters is ``data_id_mode`` (PRS_E2E_00163 and Figure 6.56): ``both``, the low byte then the high byte; ``low``, the
  low byte only, with a high byte of 0x00 (PRS_E2E_00169); ``alt``, the low byte when the counter is even and the
  high byte when it is odd; ``nibble``, the low byte and then a zero byte (PRS_E2E_00640), with the low nibble of the
  high byte sent in the high nibble of byte 1 (DataIDNibbleOffset 12, variant 1C, PRS Figure 6.53) and so covered as
  part of the data, and a Data ID of at most 12 bits (PRS_E2E_00306).
- The counter counts 0 to 14 and skips 15 (PRS_E2E_00075); the rule binds it with that maximum.
- The data is at most 32 bytes (DataLength <= 256 bits, PRS Table 6.17).

Profile 2 (``e2e_p02``):

- The CRC is ``Crc_CalculateCRC8H2F``: polynomial 0x2F, start value 0xFF, XOR value 0xFF, not reflected
  (PRS_E2E_00117, PRS_E2E_00118, PRS Table 6.28, CRC Table 7.3).
- It runs over bytes 1 to N-1 and then one byte, the entry of the 16-entry ``data_id_list`` that the counter selects
  (PRS_E2E_00120, PRS_E2E_00132, Figure 6.65). The Data ID is never sent.
- The counter counts 0 to 15 (PRS_E2E_00128, PRS_E2E_00129).

The functions follow the ``ChecksumFn`` convention: the integrity engine has written the counter and zeroed the CRC
byte when it calls them, and they read the counter back from the frame. In ``nibble`` mode the Profile 1 function
also writes the Data ID nibble into the frame before computing, as E2E_P01Protect does (PRS_E2E_00195), because the
nibble is part of what the CRC covers and of what the receiver checks.

The worked examples of PRS sections 6.5.5 and 6.6.5 are reproduced in ``tests/unit/test_car_e2e.py``.
"""

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, NamedTuple

from ecu_simulator.car.bits import BitField
from ecu_simulator.car.checksums import ChecksumFn
from ecu_simulator.car.crc import crc

if TYPE_CHECKING:
    from ecu_simulator.car.rules import ChecksumSpec

DataIdMode = Literal["both", "low", "alt", "nibble"]
E2EAlgorithm = Literal["e2e_p01", "e2e_p02"]


class Crc8(NamedTuple):
    """An 8-bit CRC whose input and result are not reflected."""

    poly: int
    init: int
    xorout: int


# The AUTOSAR CRC library functions (CRC Tables 7.1 and 7.3), and the CRC each profile computes.
CRC8_SAE_J1850 = Crc8(poly=0x1D, init=0xFF, xorout=0xFF)
CRC8H2F = Crc8(poly=0x2F, init=0xFF, xorout=0xFF)
P01_CRC = Crc8(poly=CRC8_SAE_J1850.poly, init=0x00, xorout=0x00)
P02_CRC = CRC8H2F

# Where both profiles put their CRC and counter, in either DBC byte order: as (size, msb, lsb) of the field.
CRC_POSITION = (8, 7, 0)
COUNTER_POSITION = (4, 11, 8)
MIN_BYTES = 2
COUNTER_MAX: Mapping[str, int] = MappingProxyType({"e2e_p01": 14, "e2e_p02": 15})
P01_MAX_BYTES = 32
P01_DATA_ID_MAX: Mapping[DataIdMode, int] = MappingProxyType(
    {"both": 0xFFFF, "alt": 0xFFFF, "low": 0xFF, "nibble": 0xFFF}
)
DATA_ID_LIST_LENGTH = 16


def crc8(params: Crc8, data: Sequence[int]) -> int:
    """The CRC of ``data`` under ``params``."""
    return crc(data, 8, params.poly, params.init, params.xorout, False)


def at(field: BitField, position: tuple[int, int, int]) -> bool:
    """Whether ``field`` is exactly the bits of ``position``, a (size, msb, lsb) triple."""
    return (field.size, field.msb, field.lsb) == position


def e2e_p01(spec: "ChecksumSpec", field_size: int) -> ChecksumFn:
    """The Profile 1 CRC of a frame laid out as variants 1A, 1B and 1C lay it out."""
    if spec.data_id is None or spec.data_id_mode is None:
        raise ValueError("e2e_p01 needs a data_id and a data_id_mode")
    mode: DataIdMode = spec.data_id_mode
    low, high = spec.data_id & 0xFF, spec.data_id >> 8
    mask = (1 << field_size) - 1

    def checksum(address: int, data: bytearray, field: BitField, signal_name: str) -> int:
        if mode == "nibble":
            data[1] = (data[1] & 0x0F) | ((high & 0x0F) << 4)
        counter = data[1] & 0x0F
        match mode:
            case "both":
                data_id = [low, high]
            case "low":
                data_id = [low]
            case "alt":
                data_id = [low if counter % 2 == 0 else high]
            case "nibble":
                data_id = [low, 0x00]
        return crc8(P01_CRC, [*data_id, *data[1:]]) & mask

    return checksum


def e2e_p02(spec: "ChecksumSpec", field_size: int) -> ChecksumFn:
    """The Profile 2 CRC of a frame with the E2E header at offset 0."""
    if spec.data_id_list is None or len(spec.data_id_list) != DATA_ID_LIST_LENGTH:
        raise ValueError(f"e2e_p02 needs a data_id_list of {DATA_ID_LIST_LENGTH} entries")
    data_id_list = tuple(spec.data_id_list)
    mask = (1 << field_size) - 1

    def checksum(address: int, data: bytearray, field: BitField, signal_name: str) -> int:
        return crc8(P02_CRC, [*data[1:], data_id_list[data[1] & 0x0F]]) & mask

    return checksum
