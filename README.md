# Format String

`printf(user_controlled_input)` = arbitrary reads and writes.

Normally the format string is a constant the programmer writes, with one conversion per argument, matched left to right:

```
printf("%s scored %d/%d, grade %c\n", name, hits, total, grade);
//      %s=name     %d=hits %d=total     %c=grade
```

Each conversion consumes the next argument the caller passed. Control the format string and that contract breaks: you can write conversions with no arguments behind them, and printf reads the argument slots anyway, whatever the calling convention left in registers then stack.

An easy mental model to understand format string attacks is to see it this way: printf is a tiny machine where the format string is the program. Slots are the operands, the function's argument positions; directives are read/write actions on them.

## Slots

Concretely on amd64 SysV, integer/pointer args go in `rdi, rsi, rdx, rcx, r8, r9`, then the stack (`[rsp], [rsp+8], ...`). For `printf(fmt, ...)`, `rdi` is fmt, so the variadic slots start at `rsi`:

| slot | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|------|-----|-----|-----|-----|-----|--------|----------|
| from | rsi | rdx | rcx | r8 | r9 | `[rsp]` | `[rsp+8]` |

With no index, directives consume slots left to right, one per conversion, sharing a single counter across the whole string:

```
fmt:   %p.%p.%p.%p.%p.%p
slot:   1  2  3  4  5  6
from:  rsi rdx rcx r8 r9 [rsp]
```

Target a slot directly with the positional form `%N$conv`, `N` = 1-based slot index:

```
%6$p   ->  slot 6 ([rsp]) only, nothing printed before it
%1$p   ->  slot 1 (rsi)
```

`N` is order-independent and reusable: `%6$p %6$p` reads the same slot twice. Once you know which slot holds your input, you address it straight: `%6$s` reads through it, `%6$n` writes through it. 

## Reads

A read renders a slot's bytes; the specifier picks the shape.

- `%p`: slot as a hex pointer, full width (8B). The go-to leak.
- `%x` / `%lx`: slot as hex, 4B / 8B. Narrower: `%hx` 2B, `%hhx` 1B. Length family `hh/h/(none)/l/ll` = 1/2/4/8, applies to all.
- `%d` `%i` / `%u` / `%o`: same slot as signed / unsigned decimal / octal.
- `%c`: one byte from the slot; mostly a slot-consumer to advance toward a target.
- `%s`: deref the slot as `char*`, dump the C-string there

## Writes

`%n` stores the number of bytes printed so far through the slot, treated as a pointer. That is the whole primitive: value written = running output count, address = whatever is in the slot.

- `%hhn`: 1 byte (low 8 bits of the count)
- `%hn`: 2 bytes (low 16)
- `%n`: 4 bytes (low 32)
- `%lln`: 8 bytes (full 64)

Same length family as reads; the modifier sets the store width, not the count.

**Padding.** `%n` writes the running byte count, so you set the value by inflating output before it fires. `%<k>c` is the filler idiom: it prints one character in a `k`-wide field, adding exactly `k` to the count for a handful of input bytes (field width works on any conversion, `%c` is just the clean one). The count includes everything already printed, so `target = bytes-so-far + padding`. It only grows, never shrinks, within a single string.

Writing a full-width value (a real address) needs chunked writes across several slots, covered later.

`%n` needs the target writable, and FORTIFY (`__printf_chk`) rejects `%n` in a writable-memory format string. Both decide whether the primitive fires at all.

## Addressing

Reads and writes act on whatever address sits in the referenced slot. To hit an address you choose, get it into a slot, and the slots you control are stack memory you already own: your input buffer.

**Plant it.** Pack the target address little-endian into your payload. Once it is on the stack it is just a slot value, readable with `%N$s`, writable with `%N$n`.

**Align it.** A slot is one 8-byte qword, so each address must land on an 8-byte boundary to occupy exactly one slot. Pad the directive run so the appended address list starts aligned; the pad absorbs the buffer's own low bits (`align = (-&buf) & 7`).

**Order it.** Directives first, addresses last. x86-64 addresses carry null bytes and printf stops at the first null in the format string, so nothing after the address list runs. Every `%...` goes before the packed addresses; the addresses are only ever touched as slot data, never parsed as format text.

**Index it.** An address at 8-aligned byte offset `k` in the payload is slot `S + k/8`. Reference it: `%{S+k/8}$s` reads there, `%{S+k/8}$n` writes there.


