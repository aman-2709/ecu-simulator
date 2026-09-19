# The arithmetic lives in ecu_simulator.dtc.codes now; these tables and wrappers stay
# only until the legacy UDS module that uses them is deleted.
from ecu_simulator.dtc import codes

DTC_GROUP = {"P": "00", "C": "01", "B": "10", "U": "11"}

DTC_TYPE = {"0": "00", "1": "01", "2": "10", "3": "11"}

DTC_LENGTH = 5

BIG_ENDIAN = "big"

UDS_DTC_HIGH_BYTE = 0x01

UDS_DTC_DEFAULT_STATUS = 0x2F


def encode_obd_dtcs(dtcs):
    dtcs_bytes = bytearray()
    for dtc in dtcs:
        if is_dtc_valid(dtc):
            dtcs_bytes += get_dtc_first_byte(dtc) + get_dtc_second_byte(dtc)
    return dtcs_bytes


def encode_uds_dtcs(dtcs):
    dtcs_bytes = bytearray()
    for dtc in dtcs:
        if is_dtc_valid(dtc):
            dtcs_bytes += (
                get_dtc_first_byte(dtc)
                + get_dtc_second_byte(dtc)
                + bytes([UDS_DTC_HIGH_BYTE])
                + bytes([UDS_DTC_DEFAULT_STATUS])
            )
    return dtcs_bytes


def is_dtc_valid(dtc):
    return codes.is_valid(dtc)


def get_dtc_first_byte(dtc):
    return bytes([codes.code_number(dtc) >> 8])


def get_dtc_second_byte(dtc):
    return bytes([codes.code_number(dtc) & 0xFF])


def is_hex_value(value):
    try:
        int(value, 16)
        return True
    except ValueError:
        return False
