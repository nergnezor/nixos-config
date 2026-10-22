"""Read SMS from a SIM/UICC over PC/SC.

PIN verification is attempted at most once per read. A wrong PIN spends one
of the card's remaining attempts; an empty VERIFY (retry counter only) does not.
"""

import ctypes
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field

class SimError(Exception):
    def __init__(self, message, attempts=None):
        super().__init__(message)
        self.attempts = attempts


# GSM 03.38 default alphabet. Index is the septet value.
GSM7 = (
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM7_EXT = {
    0x0A: "\f",
    0x14: "^",
    0x28: "{",
    0x29: "}",
    0x2F: "\\",
    0x3C: "[",
    0x3D: "~",
    0x3E: "]",
    0x40: "|",
    0x65: "€",
}

MS_BIND = 4096
MS_REC = 16384

PCSC_ERRORS = {
    0x8010000B: "Läsaren är upptagen",
    0x8010000C: "Inget kort i läsaren",
    0x8010000F: "Kortet och läsaren talar inte samma protokoll",
    0x80100016: "Kortet svarade inte",
    0x8010001D: "pcscd svarar inte",
    0x8010002E: "Ingen kortläsare är ansluten",
    0x80100067: "Kortet är avstängt",
    0x80100069: "Kortet togs ur läsaren",
}


class SCARD_IO_REQUEST(ctypes.Structure):
    _fields_ = [("dwProtocol", ctypes.c_ulong), ("cbPciLength", ctypes.c_ulong)]


_pcscd_proc = None


def ensure_daemon():
    """Use the system pcscd when it is running. Otherwise start one.

    The socket path is compiled into pcscd as /run/pcscd/pcscd.comm, which this
    user cannot create. A private mount namespace keeps that path, and bind-mounts
    the real /run entries back so Wayland and the GPU driver still resolve.
    """
    if os.path.exists("/run/pcscd/pcscd.comm"):
        return
    if os.environ.get("SIM_SMS_NS") == "1":
        _start_private_pcscd()
        return
    unshare = _which("unshare")
    if not unshare:
        raise SimError("pcscd kör inte, och unshare finns inte för att starta en egen.")
    # --map-root-user makes unshare fork and exit, so exec would detach the
    # program from the terminal. Spawn it and wait instead.
    env = os.environ.copy()
    env["SIM_SMS_NS"] = "1"
    proc = subprocess.Popen(
        [unshare, "--user", "--mount", "--map-root-user", _python(), *sys.argv],
        env=env,
    )
    raise SystemExit(proc.wait())


def _python():
    return os.sys.executable


def _which(name):
    path = os.environ.get("PATH", "")
    for directory in path.split(":"):
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _mount(source, target, fstype, flags):
    libc = ctypes.CDLL(None, use_errno=True)
    src = None if source is None else source.encode()
    fs = None if fstype is None else fstype.encode()
    if libc.mount(src, target.encode(), fs, ctypes.c_ulong(flags), None) != 0:
        err = ctypes.get_errno()
        raise OSError(err, f"kunde inte montera {source} på {target}", source)


def _start_private_pcscd():
    global _pcscd_proc
    pcscd = _which("pcscd")
    if not pcscd:
        raise SimError("pcscd finns inte i PATH.")
    old = "/tmp/sim-sms-oldrun"
    os.makedirs(old, exist_ok=True)
    _mount("/run", old, None, MS_BIND | MS_REC)
    _mount("tmpfs", "/run", "tmpfs", 0)
    os.makedirs("/run/pcscd", exist_ok=True)
    for name in os.listdir(old):
        src = os.path.join(old, name)
        dst = os.path.join("/run", name)
        if os.path.islink(src):
            os.symlink(os.readlink(src), dst)
            continue
        if os.path.isdir(src):
            os.makedirs(dst, exist_ok=True)
            flags = MS_BIND | MS_REC
        else:
            open(dst, "ab").close()
            flags = MS_BIND
        try:
            _mount(src, dst, None, flags)
        except OSError:
            pass

    log_dir = os.path.join(os.path.expanduser("~"), ".cache", "sim-sms")
    # ~ may point at a home that is fine; the cache dir is outside /run.
    try:
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "pcscd.log")
        log = open(log_path, "w")
    except OSError:
        log = subprocess.DEVNULL

    def die_with_parent():
        ctypes.CDLL(None).prctl(1, signal.SIGTERM)

    _pcscd_proc = subprocess.Popen(
        [pcscd, "-f", "--disable-polkit"],
        stdout=log,
        stderr=subprocess.STDOUT,
        preexec_fn=die_with_parent,
    )
    for _ in range(60):
        if os.path.exists("/run/pcscd/pcscd.comm"):
            return
        if _pcscd_proc.poll() is not None:
            break
        time.sleep(0.05)
    raise SimError("pcscd startade inte. Loggen ligger i ~/.cache/sim-sms/pcscd.log.")


def bcd_swap(data):
    digits = []
    for byte in data:
        lo, hi = byte & 0x0F, (byte >> 4) & 0x0F
        if lo == 0xF:
            break
        digits.append(f"{lo:X}")
        if hi == 0xF:
            break
        digits.append(f"{hi:X}")
    return "".join(digits)


def gsm7_unpack(data, septets, bit_offset=0):
    chars = []
    escape = False
    for index in range(septets):
        bit = bit_offset + index * 7
        byte_index = bit // 8
        shift = bit % 8
        if byte_index >= len(data):
            break
        value = data[byte_index] >> shift
        if shift > 1 and byte_index + 1 < len(data):
            value |= data[byte_index + 1] << (8 - shift)
        value &= 0x7F
        if escape:
            chars.append(GSM7_EXT.get(value, " "))
            escape = False
            continue
        if value == 0x1B:
            escape = True
            continue
        chars.append(GSM7[value] if value < len(GSM7) else "?")
    text = "".join(chars)
    if text.endswith("\r"):
        text = text[:-1]
    return text


def dcs_coding(dcs):
    if dcs & 0xC0 == 0x00 or dcs & 0xF0 in (0x40, 0x50, 0x60, 0x70):
        alphabet = (dcs >> 2) & 0x03
        return {0: "7bit", 1: "8bit", 2: "ucs2"}.get(alphabet, "7bit")
    if (dcs & 0xF0) == 0xF0:
        return "8bit" if dcs & 0x04 else "7bit"
    return "7bit"


def decode_alpha(data):
    data = bytes(data).rstrip(b"\xff")
    if not data:
        return ""
    if data[0] == 0x80:
        raw = data[1:]
        if len(raw) % 2:
            raw = raw[:-1]
        return raw.decode("utf-16-be", errors="replace").replace("\x00", "").strip()
    chars = []
    for byte in data:
        if byte == 0xFF:
            break
        chars.append(GSM7[byte] if byte < len(GSM7) else "?")
    return "".join(chars).rstrip()


@dataclass
class Concat:
    ref: int
    total: int
    seq: int


@dataclass
class Sms:
    direction: str
    unread: bool
    peer: str
    when_text: str
    text: str
    smsc: str = ""
    incomplete: bool = False
    part_count: int = 1
    part_total: int = 1
    sort_key: str = ""
    _concat: Concat | None = field(default=None, repr=False)


@dataclass
class CardInfo:
    reader: str
    iccid: str
    operator: str
    messages: list
    slots: int


def parse_udh(ud):
    if not ud:
        return None
    udhl = ud[0]
    header = ud[1 : 1 + udhl]
    index = 0
    found = None
    while index + 1 < len(header):
        iei = header[index]
        iedl = header[index + 1]
        ied = header[index + 2 : index + 2 + iedl]
        index += 2 + iedl
        if iei == 0x00 and len(ied) >= 3:
            found = Concat(ied[0], ied[1], ied[2])
        elif iei == 0x08 and len(ied) >= 4:
            found = Concat((ied[0] << 8) | ied[1], ied[2], ied[3])
    return found


def decode_address(toa, raw, n_semi):
    if (toa & 0x70) == 0x50:
        nbytes = (n_semi + 1) // 2
        # Alphanumeric addresses are GSM 7-bit packed into those octets.
        septets = (nbytes * 8) // 7
        return gsm7_unpack(raw[:nbytes], min(septets, n_semi)).strip()
    number = bcd_swap(raw[: (n_semi + 1) // 2])
    if number and (toa & 0x70) == 0x10:
        return "+" + number
    return number


def parse_scts(raw):
    if len(raw) < 6:
        return "", ""
    parts = [f"{byte & 0x0F}{(byte >> 4) & 0x0F}" for byte in raw[:6]]
    text = f"20{parts[0]}-{parts[1]}-{parts[2]} {parts[3]}:{parts[4]}"
    key = f"{parts[0]}{parts[1]}{parts[2]}{parts[3]}{parts[4]}{parts[5]}"
    return text, key


def decode_user_data(ud, udl, dcs, udhi):
    coding = dcs_coding(dcs)
    concat = parse_udh(ud) if udhi and ud else None
    if coding == "ucs2":
        raw = ud
        if udhi and raw:
            raw = raw[1 + raw[0] :]
        if len(raw) % 2:
            raw = raw[:-1]
        return raw.decode("utf-16-be", errors="replace"), concat
    if coding == "8bit":
        raw = ud
        if udhi and raw:
            raw = raw[1 + raw[0] :]
        if raw and not all(32 <= byte < 127 or byte in (9, 10, 13) for byte in raw):
            return "(binärt innehåll)", concat
        return raw.decode("latin1", errors="replace"), concat
    skip = 0
    if udhi and ud:
        skip = ((ud[0] + 1) * 8 + 6) // 7
    septets = max(0, udl - skip)
    return gsm7_unpack(ud, septets, skip * 7), concat


def parse_tpdu(status, smsc, tpdu):
    if not tpdu:
        return None
    first = tpdu[0]
    mti = first & 0x03
    udhi = bool(first & 0x40)
    unread = (status & 0x07) == 0x03
    try:
        if mti == 0x00:
            index = 1
            n_semi = tpdu[index]
            index += 1
            toa = tpdu[index]
            index += 1
            nbytes = (n_semi + 1) // 2
            peer = decode_address(toa, tpdu[index : index + nbytes], n_semi)
            index += nbytes
            index += 1  # PID
            dcs = tpdu[index]
            index += 1
            when_text, sort_key = parse_scts(tpdu[index : index + 7])
            index += 7
            udl = tpdu[index]
            index += 1
            text, concat = decode_user_data(tpdu[index:], udl, dcs, udhi)
            return Sms("Mottaget", unread, peer, when_text, text, smsc, sort_key=sort_key, _concat=concat)
        if mti == 0x01:
            index = 2  # skip message reference
            n_semi = tpdu[index]
            index += 1
            toa = tpdu[index]
            index += 1
            nbytes = (n_semi + 1) // 2
            peer = decode_address(toa, tpdu[index : index + nbytes], n_semi)
            index += nbytes
            index += 1  # PID
            dcs = tpdu[index]
            index += 1
            vpf = (first >> 3) & 0x03
            if vpf == 0x02:
                index += 1
            elif vpf in (0x01, 0x03):
                index += 7
            udl = tpdu[index]
            index += 1
            text, concat = decode_user_data(tpdu[index:], udl, dcs, udhi)
            unsent = (status & 0x07) == 0x07
            return Sms(
                "Ej skickat" if unsent else "Skickat",
                False,
                peer,
                "",
                text,
                smsc,
                sort_key="",
                _concat=concat,
            )
    except (IndexError, ValueError):
        return Sms("Okänt", False, "", "", "(meddelandet gick inte att tolka)", smsc)
    return None


def parse_sms_record(record):
    if not record or (record[0] & 0x01) == 0:
        return None
    body = record[1:]
    if not body or body[0] == 0xFF:
        return None
    smsc_len = body[0]
    index = 1
    smsc = ""
    if smsc_len:
        if smsc_len >= len(body):
            return None
        toa = body[index]
        smsc = decode_address(toa, body[index + 1 : index + smsc_len], (smsc_len - 1) * 2)
        index += smsc_len
    tpdu = body[index:].rstrip(b"\xff")
    return parse_tpdu(record[0], smsc, tpdu)


def reassemble(parts):
    singles = []
    groups = {}
    for part in parts:
        if part._concat is None or part._concat.total <= 1:
            singles.append(part)
            continue
        key = (part.direction, part.peer, part._concat.ref, part._concat.total)
        groups.setdefault(key, []).append(part)
    messages = list(singles)
    for grouped in groups.values():
        grouped.sort(key=lambda item: item._concat.seq)
        seen = {}
        for item in grouped:
            seen[item._concat.seq] = item
        ordered = [seen[seq] for seq in sorted(seen)]
        total = ordered[0]._concat.total
        earliest = min(ordered, key=lambda item: item.sort_key or "999999")
        messages.append(
            Sms(
                direction=earliest.direction,
                unread=any(item.unread for item in ordered),
                peer=earliest.peer,
                when_text=earliest.when_text,
                text="".join(item.text for item in ordered),
                smsc=earliest.smsc,
                incomplete=len(seen) != total,
                part_count=len(seen),
                part_total=total,
                sort_key=earliest.sort_key,
            )
        )
    messages.sort(key=lambda item: item.sort_key or "", reverse=True)
    return messages


def parse_tlvs(data):
    index = 0
    out = []
    while index + 1 < len(data):
        tag = data[index]
        index += 1
        if tag in (0x00, 0xFF):
            break
        length = data[index]
        index += 1
        if length == 0x81 and index < len(data):
            length = data[index]
            index += 1
        elif length == 0x82 and index + 1 < len(data):
            length = (data[index] << 8) | data[index + 1]
            index += 2
        if index + length > len(data):
            break
        out.append((tag, data[index : index + length]))
        index += length
    return out


def parse_fcp(data):
    inner = []
    for tag, value in parse_tlvs(data):
        if tag == 0x62:
            inner.extend(parse_tlvs(value))
        else:
            inner.append((tag, value))
    kind = None
    size = None
    rec_len = None
    nrec = None
    for tag, value in inner:
        if tag == 0x82 and value:
            structure = value[0] & 0x07
            kind = {0x01: "transparent", 0x02: "linear", 0x06: "cyclic"}.get(structure, f"other:{value.hex()}")
            if kind in ("linear", "cyclic") and len(value) >= 4:
                rec_len = int.from_bytes(value[2:4], "big")
            if kind in ("linear", "cyclic") and len(value) >= 5:
                nrec = value[4]
        elif tag == 0x80 and value:
            size = int.from_bytes(value, "big")
    if rec_len and size and size % rec_len == 0:
        nrec = size // rec_len
    return kind, size, rec_len, nrec


class Card:
    def __init__(self):
        self.lib = ctypes.CDLL("libpcsclite.so.1")
        self.lib.SCardEstablishContext.argtypes = [
            ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_long)
        ]
        self.lib.SCardEstablishContext.restype = ctypes.c_long
        self.lib.SCardReleaseContext.argtypes = [ctypes.c_long]
        self.lib.SCardReleaseContext.restype = ctypes.c_long
        self.lib.SCardListReaders.argtypes = [
            ctypes.c_long, ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_ulong)
        ]
        self.lib.SCardListReaders.restype = ctypes.c_long
        self.lib.SCardConnect.argtypes = [
            ctypes.c_long, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_long), ctypes.POINTER(ctypes.c_ulong),
        ]
        self.lib.SCardConnect.restype = ctypes.c_long
        self.lib.SCardDisconnect.argtypes = [ctypes.c_long, ctypes.c_ulong]
        self.lib.SCardDisconnect.restype = ctypes.c_long
        self.lib.SCardTransmit.argtypes = [
            ctypes.c_long, ctypes.POINTER(SCARD_IO_REQUEST),
            ctypes.POINTER(ctypes.c_ubyte), ctypes.c_ulong,
            ctypes.POINTER(SCARD_IO_REQUEST),
            ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(ctypes.c_ulong),
        ]
        self.lib.SCardTransmit.restype = ctypes.c_long
        self.pci = SCARD_IO_REQUEST.in_dll(self.lib, "g_rgSCardT0Pci")
        self.ctx = ctypes.c_long()
        self.hcard = ctypes.c_long()
        self.protocol = ctypes.c_ulong()
        self.reader = ""

    def _pcsc(self, rv, what):
        if rv != 0:
            code = rv & 0xFFFFFFFF
            raise SimError(PCSC_ERRORS.get(code, f"{what} misslyckades ({code:08X})"))

    def connect(self):
        self._pcsc(
            self.lib.SCardEstablishContext(2, None, None, ctypes.byref(self.ctx)),
            "Anslutning till pcscd",
        )
        count = ctypes.c_ulong(0)
        rv = self.lib.SCardListReaders(self.ctx, None, None, ctypes.byref(count))
        self._pcsc(rv, "Läsarlista")
        buf = ctypes.create_string_buffer(count.value)
        self._pcsc(
            self.lib.SCardListReaders(self.ctx, None, buf, ctypes.byref(count)),
            "Läsarlista",
        )
        readers = [item for item in buf.raw.split(b"\x00") if item]
        if not readers:
            raise SimError("Ingen kortläsare är ansluten")
        last = 0
        for reader in readers:
            rv = self.lib.SCardConnect(
                self.ctx, reader, 2, 0x0003, ctypes.byref(self.hcard), ctypes.byref(self.protocol)
            )
            if rv == 0:
                self.reader = reader.decode(errors="replace")
                if self.protocol.value == 2:
                    self.pci = SCARD_IO_REQUEST.in_dll(self.lib, "g_rgSCardT1Pci")
                return
            last = rv & 0xFFFFFFFF
        raise SimError(PCSC_ERRORS.get(last, f"Kunde inte öppna kortet ({last:08X})"))

    def close(self):
        if self.hcard.value:
            self.lib.SCardDisconnect(self.hcard, 0)
            self.hcard = ctypes.c_long()
        if self.ctx.value:
            self.lib.SCardReleaseContext(self.ctx)
            self.ctx = ctypes.c_long()

    def transmit_raw(self, apdu):
        send = (ctypes.c_ubyte * len(apdu))(*apdu)
        recv = (ctypes.c_ubyte * 2048)()
        rlen = ctypes.c_ulong(2048)
        rv = self.lib.SCardTransmit(
            self.hcard, ctypes.byref(self.pci), send, len(apdu), None, recv, ctypes.byref(rlen)
        )
        self._pcsc(rv, "Kommando till kortet")
        data = bytes(recv[: rlen.value])
        if len(data) < 2:
            raise SimError("Kortet svarade för kort")
        return data[:-2], data[-2], data[-1]

    def transmit(self, apdu, depth=0):
        data, sw1, sw2 = self.transmit_raw(apdu)
        if depth > 4:
            return data, (sw1 << 8) | sw2
        if sw1 == 0x61:
            le = sw2
            data, sw1, sw2 = self.transmit_raw([0x00, 0xC0, 0x00, 0x00, le])
            return data, (sw1 << 8) | sw2
        if sw1 == 0x6C and apdu:
            return self.transmit(list(apdu[:-1]) + [sw2], depth + 1)
        return data, (sw1 << 8) | sw2

    def select_path(self, path):
        return self.transmit([0x00, 0xA4, 0x08, 0x04, len(path), *path])

    def select_fid(self, fid):
        return self.transmit([0x00, 0xA4, 0x00, 0x04, 0x02, (fid >> 8) & 0xFF, fid & 0xFF])

    def select_aid(self, aid):
        return self.transmit([0x00, 0xA4, 0x04, 0x04, len(aid), *aid])

    def pin_retries(self):
        """Ask how many PIN1 attempts remain. This does not spend an attempt."""
        _data, sw1, sw2 = self.transmit_raw([0x00, 0x20, 0x00, 0x01])
        sw = (sw1 << 8) | sw2
        if sw1 == 0x63 and (sw2 & 0xF0) == 0xC0:
            return sw2 & 0x0F
        if sw == 0x6983:
            return 0
        if sw in (0x9000, 0x6984):
            return None
        return None

    def verify_pin(self, pin):
        if not (pin.isdigit() and 4 <= len(pin) <= 8):
            raise SimError("PIN ska vara 4–8 siffror.")
        encoded = pin.encode("ascii") + b"\xff" * (8 - len(pin))
        _data, sw = self.transmit([0x00, 0x20, 0x00, 0x01, 0x08, *encoded])
        if sw == 0x9000:
            return
        if (sw & 0xFFF0) == 0x63C0:
            left = sw & 0x0F
            raise SimError(f"Fel PIN. {left} försök kvar.", attempts=left)
        if sw == 0x6983:
            raise SimError("PIN är blockerad.", attempts=0)
        raise SimError(f"PIN nekades (SW {sw:04X}).")


def _read_records(card, fcp):
    kind, _size, rec_len, nrec = parse_fcp(fcp)
    if kind not in ("linear", "cyclic"):
        raise SimError(f"SMS-filen har ett oväntat format ({kind}).")
    if not rec_len:
        data, sw = card.transmit([0x00, 0xB2, 0x01, 0x04, 0x00])
        if sw != 0x9000:
            raise SimError(f"Kunde inte läsa SMS ({sw:04X}).")
        rec_len = len(data)
        first = data
        start = 2
    else:
        first = None
        start = 1
    if not nrec:
        nrec = 80
    nrec = max(1, min(nrec, 250))
    records = []
    if first is not None:
        records.append(first)
    for number in range(start, nrec + 1):
        le = 0 if rec_len >= 256 else rec_len
        data, sw = card.transmit([0x00, 0xB2, number, 0x04, le])
        if sw != 0x9000:
            if number == 1:
                raise SimError(f"Kunde inte läsa SMS ({sw:04X}).")
            break
        records.append(data)
    return records


def _select_and_read_sms(card, progress):
    fcp, sw = card.select_fid(0x6F3C)
    if sw == 0x6982:
        return None
    if sw != 0x9000:
        return [], 0
    if progress:
        progress("Läser meddelanden…")
    records = _read_records(card, fcp)
    parts = []
    for record in records:
        part = parse_sms_record(record)
        if part is not None:
            parts.append(part)
    return parts, len(records)


def _aids(card):
    _fcp, sw = card.select_path(bytes.fromhex("3F002F00"))
    if sw != 0x9000:
        return [bytes.fromhex("a0000000871002")]
    kind, _size, rec_len, nrec = parse_fcp(_fcp)
    if kind != "linear":
        return [bytes.fromhex("a0000000871002")]
    aids = []
    for number in range(1, min(nrec or 8, 16) + 1):
        le = 0 if not rec_len or rec_len >= 256 else rec_len
        data, sw = card.transmit([0x00, 0xB2, number, 0x04, le])
        if sw != 0x9000:
            break
        for tag, value in parse_tlvs(data):
            if tag != 0x61:
                continue
            for inner, inner_value in parse_tlvs(value):
                if inner == 0x4F and inner_value.startswith(bytes.fromhex("a0000000871002")):
                    aids.append(inner_value)
    return aids or [bytes.fromhex("a0000000871002")]


def _iccid(card):
    _fcp, sw = card.select_path(bytes.fromhex("3F002FE2"))
    if sw != 0x9000:
        return ""
    data, sw = card.transmit([0x00, 0xB0, 0x00, 0x00, 0x0A])
    if sw != 0x9000:
        return ""
    return bcd_swap(data)


def _spn(card):
    _fcp, sw = card.select_fid(0x6F46)
    if sw != 0x9000:
        return ""
    data, sw = card.transmit([0x00, 0xB0, 0x00, 0x00, 0x11])
    if sw != 0x9000 or not data:
        return ""
    return decode_alpha(data[1:])


def peek():
    """Read the public card identity and how many PIN attempts remain.

    The retry counter comes from an empty VERIFY, which does not spend an attempt.
    """
    card = Card()
    card.connect()
    try:
        card.select_fid(0x3F00)
        iccid = _iccid(card)
        operator = ""
        retries = None
        for aid in _aids(card):
            _fcp, sw = card.select_aid(aid)
            if sw != 0x9000:
                continue
            operator = _spn(card) or operator
            card.select_aid(aid)
            retries = card.pin_retries()
            break
        return card.reader, iccid, operator, retries
    finally:
        card.close()


def read_all(pin, progress=None, debug=False):
    card = Card()
    card.connect()
    try:
        if progress:
            progress("Läser kortet…")
        card.select_fid(0x3F00)
        iccid = _iccid(card)
        aids = _aids(card)
        operator = ""
        parts = []
        slots = 0
        verified = False
        for aid in aids:
            _fcp, sw = card.select_aid(aid)
            if sw != 0x9000:
                continue
            operator = operator or _spn(card)
            card.select_aid(aid)
            retries = card.pin_retries()
            if retries == 0:
                raise SimError("PIN är blockerad.", attempts=0)
            if retries is not None and not verified:
                if not pin:
                    raise SimError(f"Ange PIN. {retries} försök kvar.", attempts=retries)
                if progress:
                    progress("Låser upp kortet…")
                card.verify_pin(pin)
                verified = True
                card.select_aid(aid)
            found = _select_and_read_sms(card, progress)
            if found is None:
                raise SimError("SMS-filen kräver PIN.")
            got, count = found
            parts.extend(got)
            slots += count
        card.select_fid(0x3F00)
        _fcp, sw = card.select_path(bytes.fromhex("3F007F10"))
        if sw == 0x9000:
            found = _select_and_read_sms(card, progress)
            if found:
                got, count = found
                parts.extend(got)
                slots += count
        if debug:
            for part in parts:
                print(f"{part.direction} {part.peer} {part.when_text} {part.text!r}", flush=True)
        messages = reassemble(parts)
        unique = []
        seen = set()
        for message in messages:
            key = (message.direction, message.peer, message.when_text, message.text)
            if key in seen:
                continue
            seen.add(key)
            unique.append(message)
        return CardInfo(card.reader, iccid, operator, unique, slots)
    finally:
        card.close()


def _self_test():
    assert len(GSM7) == 128, len(GSM7)
    assert GSM7[0x48] == "H"
    assert bcd_swap(bytes.fromhex("98640214300000930293")) == "89462041030000392039"
    assert gsm7_unpack(bytes.fromhex("c834"), 2) == "Hi"
    text, _concat = decode_user_data(bytes.fromhex("c834"), 2, 0x00, False)
    assert text == "Hi"
    # SMS-DELIVER: +1234, 2023-09-01 22:09, "Hi"
    tpdu = bytes.fromhex("040491214300003290102290420002c834")
    sms = parse_tpdu(0x01, "", tpdu)
    assert sms is not None, "tpdu"
    assert sms.text == "Hi", sms.text
    assert sms.peer == "+1234", sms.peer
    assert sms.when_text == "2023-09-01 22:09", sms.when_text
    assert sms.direction == "Mottaget"


_self_test()
